"""iCal feed fetching and workout parsing for ultra-alarm."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
from icalendar import Calendar

from config import Config


def fetch_calendar(url: str) -> Calendar:
    """HTTP GET an iCal feed and return a parsed Calendar object.

    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    response = httpx.get(url, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    return Calendar.from_ical(response.content)


@dataclass
class Workout:
    """Parsed workout from an iCal VEVENT."""

    summary: str
    description: str
    distance_miles: float | None
    workout_type: str
    date: date
    duration_minutes: int | None
    is_rest_day: bool


_DISTANCE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mi(?:les?)?)\b", re.IGNORECASE)
_REST_INDICATORS = {"rest", "0 mi", "rest day", "off"}


def _parse_distance(text: str) -> float | None:
    """Extract distance in miles from text like '12 mi' or '6.5 miles'."""
    match = _DISTANCE_RE.search(text)
    if match:
        return float(match.group(1))
    return None


def _detect_rest_day(summary: str, description: str) -> bool:
    """Return True if the event looks like a rest day."""
    combined = f"{summary} {description}".lower()
    return any(indicator in combined for indicator in _REST_INDICATORS)


def _extract_workout_type(summary: str) -> str:
    """Extract workout type from summary text.

    Looks for common training descriptors. Falls back to 'general'.
    """
    summary_lower = summary.lower()
    types = [
        "easy", "tempo", "interval", "long run", "recovery",
        "fartlek", "hill", "speed", "race", "rest", "cross-train",
        "strength",
    ]
    for t in types:
        if t in summary_lower:
            return t
    return "general"


def get_workout_for_date(cal: Calendar, target_date: date, timezone: str = "America/New_York") -> Workout | None:
    """Find the VEVENT for a given date and parse it into a Workout.

    Returns None if no event matches the target date.
    """
    tz = ZoneInfo(timezone)

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        dtstart = component.get("DTSTART")
        if dtstart is None:
            continue

        dt_val = dtstart.dt
        # Convert datetime to date in the configured timezone
        if isinstance(dt_val, datetime):
            event_date = dt_val.astimezone(tz).date()
        elif isinstance(dt_val, date):
            event_date = dt_val
        else:
            continue

        if event_date != target_date:
            continue

        summary = str(component.get("SUMMARY", ""))
        description = str(component.get("DESCRIPTION", ""))

        distance = _parse_distance(summary) or _parse_distance(description)
        is_rest = _detect_rest_day(summary, description)

        # Parse duration if present
        duration_minutes: int | None = None
        ical_duration = component.get("DURATION")
        if ical_duration is not None:
            td = ical_duration.dt
            if isinstance(td, timedelta):
                duration_minutes = int(td.total_seconds() / 60)

        return Workout(
            summary=summary,
            description=description,
            distance_miles=distance,
            workout_type=_extract_workout_type(summary),
            date=event_date,
            duration_minutes=duration_minutes,
            is_rest_day=is_rest,
        )

    return None


def calculate_alarm_time(workout: Workout | None, config: Config) -> time:
    """Calculate the optimal alarm time by working backwards from work_start.

    For rest days or missing workouts, returns config.default_alarm.
    If config.alarm_override is set, returns that instead.

    Algorithm:
        alarm = work_start - pre_run_buffer - estimated_run_time - post_run_buffer
    """
    # Manual override takes priority
    if config.alarm_override:
        h, m = map(int, config.alarm_override.split(":"))
        return time(h, m)

    # Rest day or no workout: use default alarm
    if workout is None or workout.is_rest_day:
        h, m = map(int, config.default_alarm.split(":"))
        return time(h, m)

    # Estimate run duration from distance and pace
    distance = workout.distance_miles or 0.0
    if distance <= 0:
        h, m = map(int, config.default_alarm.split(":"))
        return time(h, m)

    run_minutes = math.ceil(distance * config.default_pace_min_per_mile)
    total_buffer = config.pre_run_buffer_min + run_minutes + config.post_run_buffer_min

    # Parse work_start
    ws_h, ws_m = map(int, config.work_start.split(":"))
    work_start_dt = datetime(2000, 1, 1, ws_h, ws_m)

    alarm_dt = work_start_dt - timedelta(minutes=total_buffer)

    # Clamp: don't set alarm earlier than midnight
    if alarm_dt.date() < work_start_dt.date():
        return time(0, 0)

    return alarm_dt.time()
