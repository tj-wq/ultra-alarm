"""Always-on wake word listener and alarm scheduler for ultra-alarm.

This is the main daemon that runs on the Pi. It:
1. Listens for a wake word ("hey jarvis" by default) to start a coaching session
2. Schedules the morning alarm based on tomorrow's workout (checks once per evening)
3. Fires the morning alarm at the scheduled time

Usage:
    python3 listener.py              # Run the listener daemon
    python3 listener.py --help       # Show options
"""

from __future__ import annotations

import argparse
import os
import signal
import struct
import sys
import time as time_mod
from datetime import datetime, time, timedelta
from pathlib import Path
from threading import Event, Thread
from zoneinfo import ZoneInfo

from config import Config, load_config
from ical_parser import calculate_alarm_time, fetch_calendar, get_workout_for_date

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(os.path.abspath(__file__)).parent
_SAMPLE_RATE = 16000
_CHANNELS = 1
_CHUNK_SIZE = 1280  # 80ms at 16kHz — openWakeWord's expected chunk size

# ---------------------------------------------------------------------------
# Wake word detection
# ---------------------------------------------------------------------------

def _load_wake_word_model(model_name: str):
    """Load an openWakeWord model. Returns the Model instance."""
    try:
        from openwakeword.model import Model
    except ImportError:
        print("[error] openwakeword not installed. Run: pip install openwakeword")
        sys.exit(1)

    if os.path.isfile(model_name):
        model = Model(wakeword_models=[model_name], inference_framework="tflite")
    else:
        # Use built-in model by name (e.g., "hey_jarvis")
        model = Model(inference_framework="tflite")
    return model


def _open_mic_stream():
    """Open a PyAudio microphone stream. Returns (pa, stream)."""
    try:
        import pyaudio
    except ImportError:
        print("[error] pyaudio not installed. Run: pip install pyaudio")
        sys.exit(1)

    pa = pyaudio.PyAudio()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=_CHANNELS,
        rate=_SAMPLE_RATE,
        input=True,
        frames_per_buffer=_CHUNK_SIZE,
    )
    return pa, stream


# ---------------------------------------------------------------------------
# Alarm scheduler
# ---------------------------------------------------------------------------

def _calculate_next_alarm(config: Config) -> tuple[datetime | None, str]:
    """Fetch tomorrow's workout and calculate the alarm datetime.

    Returns (alarm_datetime, description) or (None, reason).
    """
    tz = ZoneInfo(config.timezone)
    now = datetime.now(tz)
    tomorrow = now.date() + timedelta(days=1)

    try:
        cal = fetch_calendar(config.ical_url)
        workout = get_workout_for_date(cal, tomorrow, config.timezone)
    except Exception as exc:
        return None, f"Failed to fetch calendar: {exc}"

    alarm_time = calculate_alarm_time(workout, config)
    alarm_dt = datetime.combine(tomorrow, alarm_time, tzinfo=tz)

    if workout and not workout.is_rest_day:
        desc = f"{workout.distance_miles or '?'} mi {workout.workout_type}"
    elif workout and workout.is_rest_day:
        desc = "rest day"
    else:
        desc = "no workout"

    return alarm_dt, desc


def _schedule_alarm_thread(config: Config, stop_event: Event, alarm_callback) -> None:
    """Background thread that checks for tomorrow's alarm each evening.

    Runs alarm_callback when it's time to wake up.
    """
    tz = ZoneInfo(config.timezone)
    alarm_dt = None
    alarm_desc = ""
    last_check_date = None

    while not stop_event.is_set():
        now = datetime.now(tz)

        # Re-check the alarm once per day after 8pm, or if we haven't checked today
        if now.hour >= 20 and last_check_date != now.date():
            alarm_dt, alarm_desc = _calculate_next_alarm(config)
            last_check_date = now.date()
            if alarm_dt:
                print(f"[alarm] Tomorrow: {alarm_desc}. Alarm set for {alarm_dt.strftime('%H:%M')}")
            else:
                print(f"[alarm] No alarm scheduled: {alarm_desc}")

        # Fire the alarm if it's time
        if alarm_dt and now >= alarm_dt:
            print(f"[alarm] FIRING — {alarm_desc}")
            alarm_dt = None  # Don't fire again
            alarm_callback(config)

        stop_event.wait(30)  # Check every 30 seconds


# ---------------------------------------------------------------------------
# Main listener loop
# ---------------------------------------------------------------------------

def run_listener(config: Config, wake_word: str, threshold: float) -> None:
    """Run the always-on wake word listener with alarm scheduling."""
    import numpy as np

    stop_event = Event()

    def _handle_signal(sig, frame):
        print("\n[listener] Shutting down...")
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Load wake word model
    print(f"[listener] Loading wake word model: {wake_word}")
    model = _load_wake_word_model(wake_word)

    # Open microphone
    print("[listener] Opening microphone...")
    pa, stream = _open_mic_stream()

    # Start alarm scheduler thread
    def alarm_callback(cfg: Config):
        """Called when the alarm fires — runs morning coach session."""
        _run_morning_session(cfg)

    alarm_thread = Thread(
        target=_schedule_alarm_thread,
        args=(config, stop_event, alarm_callback),
        daemon=True,
    )
    alarm_thread.start()

    print(f"[listener] Listening for '{wake_word}' (threshold: {threshold})...")
    print("[listener] Say the wake word to start a coaching session.")
    print("[listener] Press Ctrl+C to stop.")

    try:
        while not stop_event.is_set():
            audio = stream.read(_CHUNK_SIZE, exception_on_overflow=False)
            audio_array = np.frombuffer(audio, dtype=np.int16)

            predictions = model.predict(audio_array)

            for mdl_name, score in predictions.items():
                if score >= threshold:
                    print(f"[wake] Detected '{mdl_name}' (score: {score:.2f})")
                    # Reset model state to avoid re-triggering
                    model.reset()
                    _run_adhoc_session(config)
                    print(f"[listener] Listening for '{wake_word}'...")
                    break

    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
        stop_event.set()


# ---------------------------------------------------------------------------
# Session runners (triggered by wake word or alarm)
# ---------------------------------------------------------------------------

def _run_morning_session(config: Config) -> None:
    """Run a morning coaching session (alarm triggered)."""
    from coach import cmd_morning
    try:
        cmd_morning(config)
    except Exception as exc:
        print(f"[error] Morning session failed: {exc}")
        # Fallback: at minimum, speak a wake-up message
        from alarm_clock import speak, generate_static_message, fetch_todays_workout
        from ical_parser import calculate_alarm_time
        workout = fetch_todays_workout(config)
        alarm_time = calculate_alarm_time(workout, config)
        msg = generate_static_message(workout, alarm_time, config)
        speak(msg, config)


def _run_adhoc_session(config: Config) -> None:
    """Run an ad-hoc coaching session (wake word triggered)."""
    from coach import CoachConversation, voice_loop, speak, listen
    from ical_parser import calculate_alarm_time

    tz = ZoneInfo(config.timezone)
    today = datetime.now(tz).date()

    try:
        cal = fetch_calendar(config.ical_url)
        workout = get_workout_for_date(cal, today, config.timezone)
    except Exception:
        workout = None

    conversation = CoachConversation(config, workout, mode="morning")

    # Generate a greeting
    greeting = conversation.chat(
        "The runner just activated you with the wake word. "
        "Give a short greeting and ask what they need."
    )
    print(f"Coach: {greeting}")
    from alarm_clock import speak as tts_speak
    tts_speak(greeting, config)

    # Enter voice conversation loop
    voice_loop(conversation, config)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ultra-alarm: always-on wake word listener and alarm scheduler"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.json",
        help="Path to config.json",
    )
    parser.add_argument(
        "--wake-word",
        type=str,
        default="hey_jarvis",
        help="Wake word model name or path to .tflite/.onnx file (default: hey_jarvis)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Wake word detection threshold 0.0-1.0 (default: 0.5)",
    )

    args = parser.parse_args()
    config = load_config(args.config)

    run_listener(config, args.wake_word, args.threshold)


if __name__ == "__main__":
    main()
