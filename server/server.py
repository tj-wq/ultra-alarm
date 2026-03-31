"""Ultra Alarm WebSocket server — runs on NAS with GPU.

Handles: STT (faster-whisper), LLM (Claude API + MCP), TTS (Piper).
Clients connect over LAN and stream raw PCM audio.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import websockets
from websockets.asyncio.server import ServerConnection

# Add parent dir to path for shared modules
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import Config, load_config
from ical_parser import Workout, calculate_alarm_time, fetch_calendar, get_workout_for_date

from protocol import MsgType, encode_msg, decode_msg, CHUNK_SIZE
import stt
import tts

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Coach conversation (adapted from coach.py for server use)
# ---------------------------------------------------------------------------

class CoachSession:
    """A single coaching conversation session."""

    def __init__(self, config: Config, workout: Workout | None, mode: str):
        self.config = config
        self.workout = workout
        self.mode = mode
        self.messages: list[dict[str, str]] = []
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        tz = ZoneInfo(self.config.timezone)
        now = datetime.now(tz)

        parts = [
            self.config.coach_system_prompt,
            "",
            "--- Current Context ---",
            f"Date/Time: {now.strftime('%A, %B %d, %Y %I:%M %p')}",
            f"Mode: {self.mode}",
            f"Work starts at: {self.config.work_start}",
        ]

        if self.workout and not self.workout.is_rest_day:
            parts.append(f"Workout: {self.workout.summary}")
            if self.workout.distance_miles:
                parts.append(f"Distance: {self.workout.distance_miles} miles")
            if self.workout.workout_type:
                parts.append(f"Type: {self.workout.workout_type}")
            if self.workout.description:
                parts.append(f"Details: {self.workout.description}")
        elif self.workout and self.workout.is_rest_day:
            parts.append("Today is a rest day.")
        else:
            parts.append("No workout found on the calendar.")

        if self.config.use_mcp:
            parts.append("")
            parts.append("--- MCP Tool Guidance ---")
            if self.mode == "morning":
                parts.append(
                    "On your first response, use get_upcoming_workouts to get "
                    "today's workout and get_activity_stats for weekly context."
                )
            elif self.mode == "evening":
                parts.append(
                    "Use get_upcoming_workouts to get tomorrow's workout details."
                )

        return "\n".join(parts)

    def chat(self, user_input: str) -> str:
        """Send user message to Claude, return assistant text."""
        import httpx

        self.messages.append({"role": "user", "content": user_input})

        api_key = self.config.get_api_key()
        if not api_key:
            fallback = "I am having trouble connecting right now."
            self.messages.append({"role": "assistant", "content": fallback})
            return fallback

        text = None

        # Try with MCP first
        if self.config.use_mcp:
            try:
                text = self._call_mcp(api_key)
            except Exception as exc:
                log.warning("MCP call failed: %s", exc)

        # Fallback to plain API
        if text is None:
            try:
                text = self._call_plain(api_key)
            except Exception as exc:
                log.warning("Plain API call failed: %s", exc)

        if text is None:
            text = "I had trouble getting a response. Go ahead and tell me more."

        self.messages.append({"role": "assistant", "content": text})
        return text

    def _call_mcp(self, api_key: str) -> str:
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
        auth_token = self.config.get_mcp_token() or self.config.mcp_oauth_access_token
        body["mcp_servers"] = [{
            "type": "url",
            "url": self.config.mcp_server_url,
            "name": "ultrarun-club",
            "authorization_token": auth_token,
        }]
        body["tools"] = [{"type": "mcp_toolset", "mcp_server_name": "ultrarun-club"}]

        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers, json=body, timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        text_parts = [b["text"] for b in data["content"] if b["type"] == "text"]
        return " ".join(text_parts).strip()

    def _call_plain(self, api_key: str) -> str:
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
            headers=headers, json=body, timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        text_parts = [b["text"] for b in data["content"] if b["type"] == "text"]
        return " ".join(text_parts).strip()

    def is_goodbye(self, text: str) -> bool:
        text_lower = text.lower().strip()
        return any(phrase in text_lower for phrase in self.config.goodbye_phrases)


# ---------------------------------------------------------------------------
# Workout helpers
# ---------------------------------------------------------------------------

def fetch_workout(config: Config, target_date: date) -> Workout | None:
    try:
        cal = fetch_calendar(config.ical_url)
        return get_workout_for_date(cal, target_date, config.timezone)
    except Exception as exc:
        log.warning("Failed to fetch calendar: %s", exc)
        return None


# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------

async def handle_client(ws: ServerConnection, config: Config):
    """Handle a single client WebSocket connection."""
    client_addr = ws.remote_address
    log.info("Client connected: %s", client_addr)

    session: CoachSession | None = None
    audio_buffer = bytearray()
    stt_model_size = os.environ.get("WHISPER_MODEL", "large-v3")
    stt_device = os.environ.get("WHISPER_DEVICE", "cuda")

    try:
        async for message in ws:
            # Binary frame = PCM audio
            if isinstance(message, bytes):
                audio_buffer.extend(message)
                continue

            # Text frame = JSON control message
            msg = decode_msg(message)
            msg_type = msg.get("type")

            if msg_type == MsgType.SESSION_START.value:
                mode = msg.get("mode", "adhoc")
                tz = ZoneInfo(config.timezone)
                today = datetime.now(tz).date()

                if mode == "evening":
                    target = today + timedelta(days=1)
                else:
                    target = today

                workout = fetch_workout(config, target)
                session = CoachSession(config, workout, mode)
                audio_buffer.clear()
                log.info("Session started: mode=%s", mode)

                # Send initial greeting for all modes
                if mode == "morning":
                    greeting = await _generate_greeting(session, config, workout, mode)
                    await _send_response(ws, session, greeting)
                elif mode == "evening":
                    greeting = await _generate_evening_greeting(config, workout)
                    if session:
                        session.messages.append({"role": "assistant", "content": greeting})
                    await _send_response(ws, session, greeting)
                else:
                    # Adhoc: generate a short greeting
                    loop = asyncio.get_event_loop()
                    greeting = await loop.run_in_executor(
                        None, session.chat,
                        "The runner just activated you. Give a short greeting and ask what they need.",
                    )
                    await _send_response(ws, session, greeting)

            elif msg_type == MsgType.VAD_END.value:
                # Client finished sending speech — transcribe and respond
                if not session:
                    await ws.send(encode_msg(MsgType.ERROR, message="No active session"))
                    continue

                pcm_data = bytes(audio_buffer)
                audio_buffer.clear()

                # Run STT in thread pool to avoid blocking
                loop = asyncio.get_event_loop()
                transcript = await loop.run_in_executor(
                    None, stt.transcribe, pcm_data, stt_model_size, stt_device,
                )

                if not transcript:
                    await ws.send(encode_msg(MsgType.TRANSCRIPT, text=""))
                    continue

                log.info("Transcript: %s", transcript)
                await ws.send(encode_msg(MsgType.TRANSCRIPT, text=transcript))

                # Check for goodbye
                if session.is_goodbye(transcript):
                    farewell = await loop.run_in_executor(
                        None, session.chat, transcript,
                    )
                    await _send_response(ws, session, farewell)
                    await ws.send(encode_msg(MsgType.SESSION_CLOSED))
                    session = None
                    continue

                # Get coach response
                response_text = await loop.run_in_executor(
                    None, session.chat, transcript,
                )
                await _send_response(ws, session, response_text)

            elif msg_type == MsgType.TEXT_INPUT.value:
                # Text mode input (for testing without mic)
                if not session:
                    await ws.send(encode_msg(MsgType.ERROR, message="No active session"))
                    continue

                text = msg.get("text", "")
                if not text:
                    continue

                loop = asyncio.get_event_loop()

                if session.is_goodbye(text):
                    farewell = await loop.run_in_executor(None, session.chat, text)
                    await _send_response(ws, session, farewell)
                    await ws.send(encode_msg(MsgType.SESSION_CLOSED))
                    session = None
                    continue

                response_text = await loop.run_in_executor(None, session.chat, text)
                await _send_response(ws, session, response_text)

            elif msg_type == MsgType.VAD_START.value:
                audio_buffer.clear()

            elif msg_type == MsgType.SESSION_END.value:
                session = None
                audio_buffer.clear()
                log.info("Session ended by client")

    except websockets.exceptions.ConnectionClosed:
        log.info("Client disconnected: %s", client_addr)
    except Exception as exc:
        log.error("Error handling client %s: %s", client_addr, exc, exc_info=True)


async def _generate_greeting(session: CoachSession, config: Config, workout: Workout | None, mode: str) -> str:
    """Generate the initial morning greeting via Claude."""
    loop = asyncio.get_event_loop()
    if workout and not workout.is_rest_day:
        dist = f"{workout.distance_miles} miles" if workout.distance_miles else "a run"
        prompt = (
            f"Give a short, energetic wake-up greeting. "
            f"Today's workout is {dist}, {workout.workout_type}. "
            f"Work starts at {config.work_start}."
        )
    elif workout and workout.is_rest_day:
        prompt = f"Give a short wake-up greeting for a rest day. Work starts at {config.work_start}."
    else:
        prompt = f"Give a short wake-up greeting. No workout on the schedule. Work starts at {config.work_start}."

    return await loop.run_in_executor(None, session.chat, prompt)


async def _generate_evening_greeting(config: Config, workout: Workout | None) -> str:
    """Generate the evening alarm-confirmation greeting."""
    alarm_time = calculate_alarm_time(workout, config)
    time_str = alarm_time.strftime('%I:%M %p').lstrip('0')

    if workout and not workout.is_rest_day:
        dist = f"{workout.distance_miles} miles" if workout.distance_miles else "a workout"
        return f"Tomorrow you have {dist}, {workout.workout_type}. I would set the alarm for {time_str}. Sound good?"
    elif workout and workout.is_rest_day:
        return f"Tomorrow is rest day. Very important for recovery. Alarm at {time_str} for work. Sound good?"
    else:
        return f"No workout found for tomorrow. Default alarm at {time_str}. Sound good?"


async def _send_response(ws: ServerConnection, session: CoachSession | None, text: str):
    """Send coach response text + streamed TTS audio to client."""
    await ws.send(encode_msg(MsgType.RESPONSE, text=text))

    # Stream TTS audio sentence-by-sentence
    loop = asyncio.get_event_loop()
    await ws.send(encode_msg(MsgType.AUDIO_START))

    for sentence, pcm_data in tts.synthesize_sentences(text):
        # Send PCM in chunks to avoid huge single frames
        offset = 0
        while offset < len(pcm_data):
            chunk = pcm_data[offset:offset + 4096]
            await ws.send(chunk)
            offset += 4096

    await ws.send(encode_msg(MsgType.AUDIO_END))


# ---------------------------------------------------------------------------
# Alarm scheduler (background task)
# ---------------------------------------------------------------------------

async def alarm_scheduler(config: Config, active_clients: set):
    """Background task that fires morning alarms at the right time.

    When the alarm fires, it sends a session_start to all connected clients.
    """
    tz = ZoneInfo(config.timezone)
    last_check_date = None
    alarm_dt = None
    alarm_desc = ""

    while True:
        now = datetime.now(tz)

        # Re-check once per day after 8pm
        if now.hour >= 20 and last_check_date != now.date():
            tomorrow = now.date() + timedelta(days=1)
            workout = fetch_workout(config, tomorrow)
            alarm_time = calculate_alarm_time(workout, config)
            alarm_dt = datetime.combine(tomorrow, alarm_time, tzinfo=tz)

            if workout and not workout.is_rest_day:
                alarm_desc = f"{workout.distance_miles or '?'} mi {workout.workout_type}"
            elif workout and workout.is_rest_day:
                alarm_desc = "rest day"
            else:
                alarm_desc = "no workout"

            last_check_date = now.date()
            log.info("Alarm scheduled: %s at %s", alarm_desc, alarm_dt.strftime("%H:%M"))

        # Fire alarm
        if alarm_dt and now >= alarm_dt:
            log.info("ALARM FIRING: %s", alarm_desc)
            alarm_dt = None
            # Notify all connected clients to start morning session
            for client_ws in list(active_clients):
                try:
                    await client_ws.send(encode_msg(
                        MsgType.ALARM_SCHEDULED, time=now.strftime("%H:%M"), desc=alarm_desc,
                    ))
                except Exception:
                    pass

        await asyncio.sleep(30)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config_path = os.environ.get("CONFIG_PATH", "/app/config.json")
    config = load_config(config_path)

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8765"))

    active_clients: set[ServerConnection] = set()

    async def handler(ws: ServerConnection):
        active_clients.add(ws)
        try:
            await handle_client(ws, config)
        finally:
            active_clients.discard(ws)

    # Pre-load the whisper model at startup
    stt_model = os.environ.get("WHISPER_MODEL", "large-v3")
    stt_device = os.environ.get("WHISPER_DEVICE", "cuda")
    log.info("Pre-loading STT model: %s on %s", stt_model, stt_device)
    stt.transcribe(b"\x00" * 3200, stt_model, stt_device)  # Warm up with silence
    log.info("STT model ready.")

    # Start alarm scheduler
    asyncio.create_task(alarm_scheduler(config, active_clients))

    log.info("Ultra Alarm server starting on %s:%d", host, port)

    stop = asyncio.get_event_loop().create_future()

    def _signal_handler():
        if not stop.done():
            stop.set_result(None)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    async with websockets.serve(handler, host, port, max_size=2**20):
        log.info("Server listening on ws://%s:%d", host, port)
        await stop

    log.info("Server shut down.")


if __name__ == "__main__":
    asyncio.run(main())
