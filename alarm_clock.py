"""One-shot alarm clock: fetch workout, calculate alarm, speak coaching message."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import time as time_mod
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from config import Config, init_config, load_config
from ical_parser import Workout, calculate_alarm_time, fetch_calendar, get_workout_for_date


def fetch_todays_workout(config: Config) -> Workout | None:
    """Fetch the iCal feed and return today's workout, or None on failure."""
    tz = ZoneInfo(config.timezone)
    today = datetime.now(tz).date()
    try:
        cal = fetch_calendar(config.ical_url)
        return get_workout_for_date(cal, today, config.timezone)
    except Exception as exc:
        print(f"[warn] Failed to fetch calendar: {exc}")
        return None


def build_claude_prompt(workout: Workout | None, alarm_time: time, config: Config) -> str:
    """Build the user prompt for the Claude coaching message."""
    tz = ZoneInfo(config.timezone)
    now = datetime.now(tz)

    parts: list[str] = [
        f"Current date and time: {now.strftime('%A, %B %d, %Y %I:%M %p')}",
        f"Work starts at: {config.work_start}",
        f"Alarm time: {alarm_time.strftime('%H:%M')}",
    ]

    if workout and not workout.is_rest_day:
        parts.append(f"Today's workout: {workout.summary}")
        if workout.distance_miles:
            parts.append(f"Distance: {workout.distance_miles} miles")
        if workout.workout_type:
            parts.append(f"Type: {workout.workout_type}")
        if workout.description:
            parts.append(f"Details: {workout.description}")
    elif workout and workout.is_rest_day:
        parts.append("Today is a rest day.")
    else:
        parts.append("No workout scheduled today.")

    parts.append(
        "Give a short wake-up coaching message for right now. "
        "Plain text only, suitable for text-to-speech."
    )
    return "\n".join(parts)


def generate_message_claude(workout: Workout | None, alarm_time: time, config: Config) -> str:
    """Call the Claude API for a coaching wake-up message.

    Falls back to a static message if the API is unreachable.
    """
    api_key = config.get_api_key()
    if not api_key:
        print("[warn] No Anthropic API key configured, using static message.")
        return generate_static_message(workout, alarm_time, config)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        user_prompt = build_claude_prompt(workout, alarm_time, config)

        response = client.messages.create(
            model=config.model,
            max_tokens=300,
            system=config.coach_system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text
        return text.strip()
    except Exception as exc:
        print(f"[warn] Claude API error: {exc}")
        return generate_static_message(workout, alarm_time, config)


def generate_static_message(workout: Workout | None, alarm_time: time, config: Config) -> str:
    """Generate a fallback wake-up message without the API."""
    if workout and not workout.is_rest_day:
        distance = f"{workout.distance_miles} miles" if workout.distance_miles else "a run"
        return (
            f"Good morning friend! Time to wake up. "
            f"Today you have {distance}, {workout.workout_type}. "
            f"Work starts at {config.work_start}. Let us go!"
        )
    if workout and workout.is_rest_day:
        return (
            f"Good morning friend! Today is rest day. "
            f"Your body recovers, this is good. Work starts at {config.work_start}."
        )
    return (
        f"Good morning friend! No workout on the schedule today. "
        f"Work starts at {config.work_start}. Have a good day!"
    )


def speak_espeak(message: str) -> None:
    """Speak a message using the espeak command."""
    subprocess.run(["espeak", message], check=False)


def speak_piper(message: str, config: Config) -> None:
    """Speak a message using Piper TTS, with optional rocky_filter post-processing."""
    if not config.piper_model:
        print("[warn] No piper_model configured, falling back to espeak.")
        speak_espeak(message)
        return

    wav_fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(wav_fd)

    try:
        # Generate wav via piper
        proc = subprocess.run(
            ["piper", "--model", config.piper_model, "--output_file", wav_path],
            input=message,
            text=True,
            check=True,
            capture_output=True,
        )

        # Apply rocky_filter.sh if it exists alongside this script
        filter_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rocky_filter.sh")
        if os.path.isfile(filter_script):
            filtered_fd, filtered_path = tempfile.mkstemp(suffix=".wav")
            os.close(filtered_fd)
            try:
                subprocess.run(
                    ["bash", filter_script, wav_path, filtered_path],
                    check=True,
                    capture_output=True,
                )
                os.replace(filtered_path, wav_path)
            except Exception:
                # Clean up filtered file on failure, play unfiltered
                if os.path.exists(filtered_path):
                    os.unlink(filtered_path)

        subprocess.run(["aplay", wav_path], check=False)
    except FileNotFoundError:
        print("[warn] piper not found, falling back to espeak.")
        speak_espeak(message)
    except subprocess.CalledProcessError as exc:
        print(f"[warn] Piper failed: {exc}, falling back to espeak.")
        speak_espeak(message)
    finally:
        if os.path.exists(wav_path):
            os.unlink(wav_path)


def speak_pyttsx3(message: str) -> None:
    """Speak a message using the pyttsx3 engine."""
    try:
        import pyttsx3

        engine = pyttsx3.init()
        engine.say(message)
        engine.runAndWait()
    except Exception as exc:
        print(f"[warn] pyttsx3 failed: {exc}, falling back to espeak.")
        speak_espeak(message)


def speak(message: str, config: Config) -> None:
    """Dispatch TTS to the configured engine."""
    engine = config.tts_engine.lower()
    if engine == "piper":
        speak_piper(message, config)
    elif engine == "pyttsx3":
        speak_pyttsx3(message)
    else:
        speak_espeak(message)


def fire_alarm(config: Config) -> None:
    """Execute the full alarm sequence: fetch, calculate, speak."""
    workout = fetch_todays_workout(config)
    alarm_time = calculate_alarm_time(workout, config)

    print(f"Alarm time: {alarm_time.strftime('%H:%M')}")
    if workout:
        print(f"Workout: {workout.summary}")
    else:
        print("No workout found for today.")

    message = generate_message_claude(workout, alarm_time, config)
    print(f"\n{message}\n")
    speak(message, config)


def preview(config: Config) -> None:
    """Show today's workout and calculated alarm time without audio."""
    workout = fetch_todays_workout(config)
    alarm_time = calculate_alarm_time(workout, config)

    print(f"Alarm time: {alarm_time.strftime('%H:%M')}")
    if workout:
        print(f"Workout:    {workout.summary}")
        print(f"Type:       {workout.workout_type}")
        if workout.distance_miles is not None:
            print(f"Distance:   {workout.distance_miles} mi")
        if workout.description:
            print(f"Details:    {workout.description}")
        if workout.is_rest_day:
            print("Rest day:   yes")
    else:
        print("No workout found for today.")


def schedule(config: Config) -> None:
    """Sleep until the calculated alarm time, then fire."""
    workout = fetch_todays_workout(config)
    alarm_time = calculate_alarm_time(workout, config)

    tz = ZoneInfo(config.timezone)
    now = datetime.now(tz)
    today = now.date()
    alarm_dt = datetime.combine(today, alarm_time, tzinfo=tz)

    # If alarm time already passed, set for tomorrow
    if alarm_dt <= now:
        alarm_dt += timedelta(days=1)
        print(f"Alarm time already passed today. Scheduling for tomorrow.")

    delta = alarm_dt - now
    total_seconds = delta.total_seconds()
    print(f"Alarm set for {alarm_dt.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"Sleeping for {int(total_seconds // 3600)}h {int((total_seconds % 3600) // 60)}m")

    time_mod.sleep(total_seconds)
    # Re-fetch and fire at alarm time for freshest data
    fire_alarm(config)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Ultra-alarm: one-shot alarm clock with coaching wake-up message"
    )
    parser.add_argument(
        "command",
        choices=["alarm", "preview", "schedule", "init-config"],
        help="Command to run",
    )
    parser.add_argument(
        "--override",
        type=str,
        default=None,
        metavar="HH:MM",
        help="Override alarm time (e.g. 05:30)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.json",
        help="Path to config.json (default: config.json)",
    )

    args = parser.parse_args()

    if args.command == "init-config":
        init_config(args.config)
        print(f"Wrote default config to {args.config}")
        return

    config = load_config(args.config)

    if args.override:
        config.alarm_override = args.override

    if args.command == "alarm":
        fire_alarm(config)
    elif args.command == "preview":
        preview(config)
    elif args.command == "schedule":
        schedule(config)


if __name__ == "__main__":
    main()
