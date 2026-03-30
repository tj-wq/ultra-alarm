"""Conversational voice coach — primary interface for ultra-alarm."""

from __future__ import annotations

import argparse
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import wave
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config import Config, init_config, load_config
from ical_parser import (
    Workout,
    calculate_alarm_time,
    fetch_calendar,
    get_workout_for_date,
)
from alarm_clock import speak

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(os.path.abspath(__file__)).parent
_LAST_AT_JOB_FILE = _SCRIPT_DIR / ".last_at_job"

_TIME_OVERRIDE_RE = re.compile(
    r"(?:^|make\s+it|set\s+(?:it\s+)?(?:to|for)?|how\s+about)\s*"
    r"(\d{1,2})[:\s](\d{2})"
    r"(?:\s*(a\.?m\.?|p\.?m\.?))?"
    , re.IGNORECASE,
)

# Audio recording parameters
_SAMPLE_RATE = 16000
_CHANNELS = 1
_CHUNK_SIZE = 1024
_SAMPLE_WIDTH = 2  # 16-bit
_SILENCE_THRESHOLD_RMS = 500
_SILENCE_SECONDS = 2.0
_MAX_RECORD_SECONDS = 30


# ---------------------------------------------------------------------------
# Voice Pipeline — STT via openai-whisper (local)
# ---------------------------------------------------------------------------

def _rms(data: bytes) -> float:
    """Calculate root mean square of 16-bit PCM audio data."""
    count = len(data) // 2
    if count == 0:
        return 0.0
    fmt = f"<{count}h"
    samples = struct.unpack(fmt, data[:count * 2])
    sum_sq = sum(s * s for s in samples)
    return math.sqrt(sum_sq / count)


def record_audio(max_seconds: int = _MAX_RECORD_SECONDS) -> str | None:
    """Record from the microphone until silence is detected.

    Returns the path to a temporary WAV file, or None on failure.
    The caller is responsible for deleting the file.
    """
    try:
        import pyaudio
    except ImportError:
        print("[error] pyaudio not installed. Run: pip install pyaudio")
        return None

    pa = pyaudio.PyAudio()
    stream = None
    frames: list[bytes] = []

    try:
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=_CHANNELS,
            rate=_SAMPLE_RATE,
            input=True,
            frames_per_buffer=_CHUNK_SIZE,
        )

        print("[listening...]")
        speech_started = False
        silent_chunks = 0
        chunks_per_second = _SAMPLE_RATE / _CHUNK_SIZE
        silence_limit = int(chunks_per_second * _SILENCE_SECONDS)
        max_chunks = int(chunks_per_second * max_seconds)

        for _ in range(max_chunks):
            data = stream.read(_CHUNK_SIZE, exception_on_overflow=False)
            frames.append(data)
            level = _rms(data)

            if level > _SILENCE_THRESHOLD_RMS:
                speech_started = True
                silent_chunks = 0
            elif speech_started:
                silent_chunks += 1
                if silent_chunks >= silence_limit:
                    break

    except Exception as exc:
        print(f"[error] Recording failed: {exc}")
        return None
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        pa.terminate()

    if not frames:
        return None

    fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(_CHANNELS)
            wf.setsampwidth(_SAMPLE_WIDTH)
            wf.setframerate(_SAMPLE_RATE)
            wf.writeframes(b"".join(frames))
    except Exception:
        os.unlink(wav_path)
        return None

    return wav_path


def transcribe(wav_path: str, model_name: str = "base.en") -> str:
    """Transcribe a WAV file using openai-whisper (local).

    Returns the transcribed text, or an empty string on failure.
    """
    try:
        import whisper  # openai-whisper
    except ImportError:
        print("[error] openai-whisper not installed. Run: pip install openai-whisper")
        return ""

    try:
        model = whisper.load_model(model_name)
        result = model.transcribe(wav_path, language="en", fp16=False)
        return result.get("text", "").strip()
    except Exception as exc:
        print(f"[error] Transcription failed: {exc}")
        return ""


def listen() -> str:
    """Record audio and transcribe it. Returns the transcribed text."""
    wav_path = record_audio()
    if wav_path is None:
        return ""
    try:
        text = transcribe(wav_path)
        return text
    finally:
        if os.path.exists(wav_path):
            os.unlink(wav_path)


# ---------------------------------------------------------------------------
# Time override parsing
# ---------------------------------------------------------------------------

def parse_time_override(text: str) -> time | None:
    """Extract an alarm time override from spoken text.

    Handles patterns like:
        "make it 5:30"
        "set it to 5:30 AM"
        "how about 6:00"
        "5:15"
        "5 30 pm"

    Returns a datetime.time or None if no time was found.
    """
    match = _TIME_OVERRIDE_RE.search(text)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    ampm = match.group(3)

    if ampm:
        ampm_clean = ampm.replace(".", "").lower()
        if ampm_clean == "pm" and hour < 12:
            hour += 12
        elif ampm_clean == "am" and hour == 12:
            hour = 0

    # Sanity check
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    return time(hour, minute)


# ---------------------------------------------------------------------------
# Alarm scheduling via `at`
# ---------------------------------------------------------------------------

def _cancel_previous_at_job() -> None:
    """Cancel the previously scheduled at job, if any."""
    if not _LAST_AT_JOB_FILE.exists():
        return
    try:
        job_id = _LAST_AT_JOB_FILE.read_text().strip()
        if job_id:
            subprocess.run(["atrm", job_id], check=False, capture_output=True)
            print(f"[info] Cancelled previous at job {job_id}")
    except Exception:
        pass
    finally:
        try:
            _LAST_AT_JOB_FILE.unlink(missing_ok=True)
        except Exception:
            pass


def schedule_morning_alarm(alarm_time: time, config_path: str) -> bool:
    """Schedule the morning coach via the Linux `at` command.

    Returns True on success.
    """
    # Check that atd is available
    check = subprocess.run(["which", "at"], capture_output=True)
    if check.returncode != 0:
        print("[error] 'at' command not found. Install with: sudo apt install at")
        return False

    _cancel_previous_at_job()

    coach_path = os.path.abspath(__file__)
    time_str = alarm_time.strftime("%H:%M")
    cmd = f"python3 {coach_path} morning --config {os.path.abspath(config_path)}"

    try:
        result = subprocess.run(
            ["at", time_str],
            input=cmd,
            text=True,
            capture_output=True,
        )
        # `at` writes the job info to stderr
        stderr = result.stderr.strip()
        print(f"[info] Scheduled morning alarm: {stderr}")

        # Parse job ID from output like "job 42 at Mon Mar 30 05:15:00 2026"
        job_match = re.search(r"job\s+(\d+)", stderr)
        if job_match:
            _LAST_AT_JOB_FILE.write_text(job_match.group(1))

        return result.returncode == 0
    except Exception as exc:
        print(f"[error] Failed to schedule alarm: {exc}")
        return False


# ---------------------------------------------------------------------------
# Workout fetching helpers
# ---------------------------------------------------------------------------

def fetch_workout_for(config: Config, target_date: date) -> Workout | None:
    """Fetch the iCal feed and return the workout for a given date."""
    try:
        cal = fetch_calendar(config.ical_url)
        return get_workout_for_date(cal, target_date, config.timezone)
    except Exception as exc:
        print(f"[warn] Failed to fetch calendar: {exc}")
        return None


# ---------------------------------------------------------------------------
# CoachConversation
# ---------------------------------------------------------------------------

class CoachConversation:
    """Multi-turn conversation with the Claude-powered coach.

    Holds message history, system prompt, and configuration.
    """

    def __init__(self, config: Config, workout: Workout | None, mode: str) -> None:
        self.config = config
        self.workout = workout
        self.mode = mode
        self.messages: list[dict[str, str]] = []
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        """Build the system prompt with workout context and MCP guidance injected."""
        tz = ZoneInfo(self.config.timezone)
        now = datetime.now(tz)

        context_parts: list[str] = [
            self.config.coach_system_prompt,
            "",
            "--- Current Context ---",
            f"Date/Time: {now.strftime('%A, %B %d, %Y %I:%M %p')}",
            f"Mode: {self.mode}",
            f"Work starts at: {self.config.work_start}",
        ]

        if self.workout and not self.workout.is_rest_day:
            context_parts.append(f"Workout: {self.workout.summary}")
            if self.workout.distance_miles:
                context_parts.append(f"Distance: {self.workout.distance_miles} miles")
            if self.workout.workout_type:
                context_parts.append(f"Type: {self.workout.workout_type}")
            if self.workout.description:
                context_parts.append(f"Details: {self.workout.description}")
        elif self.workout and self.workout.is_rest_day:
            context_parts.append("Today is a rest day.")
        else:
            context_parts.append("No workout found on the calendar.")

        # Add MCP guidance based on mode
        if self.config.use_mcp:
            context_parts.append("")
            context_parts.append("--- MCP Tool Guidance ---")
            if self.mode == "morning":
                context_parts.append(
                    "On your first response, use get_upcoming_workouts to get "
                    "today's workout and get_activity_stats for weekly context."
                )
            elif self.mode == "evening":
                context_parts.append(
                    "Use get_upcoming_workouts to get tomorrow's workout details."
                )

        return "\n".join(context_parts)

    def _call_anthropic_mcp(self, api_key: str) -> str:
        """Call the Anthropic API with MCP server attached via raw httpx POST.

        Returns the extracted text from the response, or raises on failure.
        """
        import httpx

        headers = {
            "x-api-key": api_key,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "mcp-client-2025-11-20",
        }

        body: dict = {
            "model": self.config.model,
            "max_tokens": 1024,
            "system": self.system_prompt,
            "messages": self.messages,
        }

        if self.config.use_mcp:
            auth_token = (
                self.config.get_mcp_token()
                or self.config.mcp_oauth_access_token
            )
            body["mcp_servers"] = [{
                "type": "url",
                "url": self.config.mcp_server_url,
                "name": "ultrarun-club",
                "authorization_token": auth_token,
            }]
            body["tools"] = [{
                "type": "mcp_toolset",
                "mcp_server_name": "ultrarun-club",
            }]

        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        # Extract only text blocks for TTS (filter out mcp_tool_use, mcp_tool_result, etc.)
        text_parts = [
            block["text"]
            for block in data["content"]
            if block["type"] == "text"
        ]
        return " ".join(text_parts).strip()

    def _call_anthropic_fallback(self, api_key: str) -> str:
        """Call the Anthropic API without MCP (iCal-based context in system prompt).

        Returns the extracted text from the response, or raises on failure.
        """
        import httpx

        headers = {
            "x-api-key": api_key,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        body = {
            "model": self.config.model,
            "max_tokens": 1024,
            "system": self.system_prompt,
            "messages": self.messages,
        }

        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        text_parts = [
            block["text"]
            for block in data["content"]
            if block["type"] == "text"
        ]
        return " ".join(text_parts).strip()

    def chat(self, user_input: str) -> str:
        """Send a user message and get the assistant response.

        Appends both the user and assistant messages to history.
        Returns the assistant's text reply.

        Uses a fallback chain:
        1. MCP-enabled API call (if use_mcp is True)
        2. Plain API call without MCP (iCal-based context in system prompt)
        3. Static fallback message
        """
        self.messages.append({"role": "user", "content": user_input})

        api_key = self.config.get_api_key()
        if not api_key:
            fallback = "I am having trouble connecting right now. Go ahead and tell me how you are feeling."
            self.messages.append({"role": "assistant", "content": fallback})
            return fallback

        text: str | None = None

        # Step 1: Try with MCP
        if self.config.use_mcp:
            try:
                text = self._call_anthropic_mcp(api_key)
            except Exception as exc:
                print(f"[warn] MCP API call failed: {exc}")

        # Step 2: Fallback to plain API (no MCP)
        if text is None:
            try:
                text = self._call_anthropic_fallback(api_key)
            except Exception as exc:
                print(f"[warn] Fallback API call failed: {exc}")

        # Step 3: Static fallback
        if text is None:
            text = "I had trouble getting a response. Go ahead and tell me more."

        self.messages.append({"role": "assistant", "content": text})
        return text

    def is_goodbye(self, text: str) -> bool:
        """Check if the user's text contains a goodbye phrase."""
        text_lower = text.lower().strip()
        return any(phrase in text_lower for phrase in self.config.goodbye_phrases)


# ---------------------------------------------------------------------------
# Voice conversation loop
# ---------------------------------------------------------------------------

def text_input() -> str:
    """Read input from keyboard instead of microphone."""
    try:
        return input("You: ").strip()
    except (EOFError, KeyboardInterrupt):
        return "goodbye"


def voice_loop(conversation: CoachConversation, config: Config, text_mode: bool = False) -> None:
    """Run the conversation loop: listen (or type) -> chat -> speak."""
    turns = 0
    while turns < config.max_conversation_turns:
        text = text_input() if text_mode else listen()
        if not text:
            if text_mode:
                continue
            print("[no speech detected, waiting...]")
            continue

        if not text_mode:
            print(f"You: {text}")

        if conversation.is_goodbye(text):
            farewell = conversation.chat(text)
            print(f"Coach: {farewell}")
            speak(farewell, config)
            break

        response = conversation.chat(text)
        print(f"Coach: {response}")
        speak(response, config)
        turns += 1

    if turns >= config.max_conversation_turns:
        print("[max conversation turns reached]")


# ---------------------------------------------------------------------------
# Play alarm sound
# ---------------------------------------------------------------------------

def play_alarm_sound(config: Config) -> None:
    """Play the configured alarm sound WAV file, if set."""
    if not config.alarm_sound:
        return
    sound_path = config.alarm_sound
    if not os.path.isabs(sound_path):
        sound_path = str(_SCRIPT_DIR / sound_path)
    if not os.path.isfile(sound_path):
        print(f"[warn] Alarm sound not found: {sound_path}")
        return
    try:
        subprocess.run(["aplay", sound_path], check=False)
    except FileNotFoundError:
        print("[warn] aplay not found, skipping alarm sound")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_evening(config: Config, config_path: str, text_mode: bool = False) -> None:
    """Evening mode: confirm tomorrow's workout and set alarm."""
    tz = ZoneInfo(config.timezone)
    tomorrow = datetime.now(tz).date() + timedelta(days=1)
    workout = fetch_workout_for(config, tomorrow)
    alarm_time = calculate_alarm_time(workout, config)

    # Build greeting
    if workout and not workout.is_rest_day:
        dist = f"{workout.distance_miles} miles" if workout.distance_miles else "a workout"
        greeting = (
            f"Tomorrow you have {dist}, {workout.workout_type}. "
            f"I would set the alarm for {alarm_time.strftime('%I:%M %p').lstrip('0')}. "
            f"Sound good?"
        )
    elif workout and workout.is_rest_day:
        greeting = (
            f"Tomorrow is rest day. Very important for recovery. "
            f"Alarm at {alarm_time.strftime('%I:%M %p').lstrip('0')} for work. Sound good?"
        )
    else:
        greeting = (
            f"No workout found for tomorrow. "
            f"Default alarm at {alarm_time.strftime('%I:%M %p').lstrip('0')}. Sound good?"
        )

    print(f"Coach: {greeting}")
    speak(greeting, config)

    conversation = CoachConversation(config, workout, mode="evening")
    # Seed the conversation with the greeting
    conversation.messages.append({"role": "assistant", "content": greeting})

    # Conversation loop with time override detection
    turns = 0
    while turns < config.max_conversation_turns:
        text = text_input() if text_mode else listen()
        if not text:
            if text_mode:
                continue
            print("[no speech detected, waiting...]")
            continue

        if not text_mode:
            print(f"You: {text}")

        # Check for time override
        override = parse_time_override(text)
        if override is not None:
            alarm_time = override
            print(f"[alarm time updated to {alarm_time.strftime('%H:%M')}]")

        if conversation.is_goodbye(text):
            # Schedule alarm and say farewell
            scheduled = schedule_morning_alarm(alarm_time, config_path)
            if scheduled:
                farewell = (
                    f"Alarm set for {alarm_time.strftime('%I:%M %p').lstrip('0')}. "
                    f"Sleep well. See you in the morning."
                )
            else:
                farewell = (
                    f"I could not schedule the alarm automatically. "
                    f"Please set alarm for {alarm_time.strftime('%I:%M %p').lstrip('0')} manually. "
                    f"Sleep well."
                )
            print(f"Coach: {farewell}")
            speak(farewell, config)
            break

        response = conversation.chat(text)
        print(f"Coach: {response}")
        speak(response, config)
        turns += 1

    if turns >= config.max_conversation_turns:
        print("[max conversation turns reached, scheduling alarm]")
        schedule_morning_alarm(alarm_time, config_path)


def cmd_morning(config: Config, text_mode: bool = False) -> None:
    """Morning mode: alarm fires, coach greets and enters conversation."""
    tz = ZoneInfo(config.timezone)
    today = datetime.now(tz).date()
    workout = fetch_workout_for(config, today)

    # Play alarm sound first
    play_alarm_sound(config)

    # Build personalized greeting
    conversation = CoachConversation(config, workout, mode="morning")

    if workout and not workout.is_rest_day:
        dist = f"{workout.distance_miles} miles" if workout.distance_miles else "a run"
        greeting_prompt = (
            f"Give a short, energetic wake-up greeting. "
            f"Today's workout is {dist}, {workout.workout_type}. "
            f"Work starts at {config.work_start}."
        )
    elif workout and workout.is_rest_day:
        greeting_prompt = (
            "Give a short wake-up greeting for a rest day. "
            f"Work starts at {config.work_start}."
        )
    else:
        greeting_prompt = (
            "Give a short wake-up greeting. No workout on the schedule. "
            f"Work starts at {config.work_start}."
        )

    greeting = conversation.chat(greeting_prompt)
    print(f"Coach: {greeting}")
    speak(greeting, config)

    # Enter conversation loop
    voice_loop(conversation, config, text_mode=text_mode)


def cmd_test_voice(config: Config) -> None:
    """Test the full voice pipeline: mic -> transcribe -> respond -> speak."""
    print("Testing voice pipeline. Speak after the prompt.")
    print("Recording...")

    text = listen()
    if not text:
        print("No speech detected.")
        return

    print(f"Transcribed: {text}")
    print("Generating response...")

    conversation = CoachConversation(config, workout=None, mode="test")
    response = conversation.chat(text)
    print(f"Response: {response}")
    print("Speaking...")
    speak(response, config)
    print("Done.")


def cmd_preview(config: Config) -> None:
    """Show today and tomorrow's workout info without audio."""
    tz = ZoneInfo(config.timezone)
    today = datetime.now(tz).date()
    tomorrow = today + timedelta(days=1)

    print(f"=== Today ({today}) ===")
    workout_today = fetch_workout_for(config, today)
    if workout_today:
        alarm_today = calculate_alarm_time(workout_today, config)
        print(f"  Workout:   {workout_today.summary}")
        print(f"  Type:      {workout_today.workout_type}")
        if workout_today.distance_miles is not None:
            print(f"  Distance:  {workout_today.distance_miles} mi")
        if workout_today.description:
            print(f"  Details:   {workout_today.description}")
        print(f"  Rest day:  {'yes' if workout_today.is_rest_day else 'no'}")
        print(f"  Alarm:     {alarm_today.strftime('%H:%M')}")
    else:
        print("  No workout found.")

    print(f"\n=== Tomorrow ({tomorrow}) ===")
    workout_tomorrow = fetch_workout_for(config, tomorrow)
    if workout_tomorrow:
        alarm_tomorrow = calculate_alarm_time(workout_tomorrow, config)
        print(f"  Workout:   {workout_tomorrow.summary}")
        print(f"  Type:      {workout_tomorrow.workout_type}")
        if workout_tomorrow.distance_miles is not None:
            print(f"  Distance:  {workout_tomorrow.distance_miles} mi")
        if workout_tomorrow.description:
            print(f"  Details:   {workout_tomorrow.description}")
        print(f"  Rest day:  {'yes' if workout_tomorrow.is_rest_day else 'no'}")
        print(f"  Alarm:     {alarm_tomorrow.strftime('%H:%M')}")
    else:
        print("  No workout found.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point for coach.py."""
    parser = argparse.ArgumentParser(
        description="Ultra-alarm conversational voice coach"
    )
    parser.add_argument(
        "command",
        choices=["evening", "morning", "test-voice", "preview", "init-config"],
        help="Command to run",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.json",
        help="Path to config.json (default: config.json)",
    )
    parser.add_argument(
        "--text",
        action="store_true",
        help="Use keyboard input instead of microphone (for testing without mic)",
    )

    args = parser.parse_args()

    if args.command == "init-config":
        init_config(args.config)
        print(f"Wrote default config to {args.config}")
        return

    config = load_config(args.config)

    if args.command == "evening":
        cmd_evening(config, args.config, text_mode=args.text)
    elif args.command == "morning":
        cmd_morning(config, text_mode=args.text)
    elif args.command == "test-voice":
        cmd_test_voice(config)
    elif args.command == "preview":
        cmd_preview(config)


if __name__ == "__main__":
    main()
