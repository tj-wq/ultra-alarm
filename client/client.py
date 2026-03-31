"""Ultra Alarm thin client — runs on Raspberry Pi.

Handles: wake word detection, VAD, mic capture, speaker playback.
Streams audio to the NAS server over WebSocket.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import math
import os
import signal
import struct
import sys
import wave
from pathlib import Path

# Add parent/server dir to path for protocol
sys.path.insert(0, str(Path(__file__).parent.parent / "server"))
from protocol import MsgType, encode_msg, decode_msg, SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH

log = logging.getLogger(__name__)

# Audio constants
CHUNK_SIZE = 1280  # 80ms at 16kHz — matches openWakeWord
RMS_THRESHOLD = 500
SILENCE_SECONDS = 2.0
MAX_RECORD_SECONDS = 30


def _rms(data: bytes) -> float:
    """Calculate RMS of 16-bit PCM audio."""
    count = len(data) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"<{count}h", data[:count * 2])
    return math.sqrt(sum(s * s for s in samples) / count)


class AudioPlayer:
    """Plays raw PCM audio through ALSA."""

    def __init__(self):
        self._buffer = bytearray()

    def feed(self, pcm_data: bytes):
        self._buffer.extend(pcm_data)

    def play_and_clear(self):
        """Play accumulated audio buffer via aplay, then clear."""
        if not self._buffer:
            return
        try:
            import subprocess
            # Write to temp WAV and play
            tmp_path = "/tmp/ultra_alarm_tts.wav"
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(SAMPLE_WIDTH)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(bytes(self._buffer))
            subprocess.run(["aplay", tmp_path], check=False, capture_output=True)
            os.unlink(tmp_path)
        except Exception as exc:
            log.error("Audio playback failed: %s", exc)
        finally:
            self._buffer.clear()


class WakeWordDetector:
    """Wraps openWakeWord for wake word detection."""

    def __init__(self, model_name: str = "hey_jarvis", threshold: float = 0.5):
        self.threshold = threshold
        try:
            from openwakeword.model import Model
        except ImportError:
            log.error("openwakeword not installed. Run: pip install openwakeword")
            sys.exit(1)

        if os.path.isfile(model_name):
            self.model = Model(wakeword_model_paths=[model_name], inference_framework="onnx")
        else:
            self.model = Model(inference_framework="onnx")
        self.model_name = model_name

    def detect(self, audio_chunk) -> bool:
        """Check if wake word was detected in this audio chunk.

        Args:
            audio_chunk: numpy int16 array of audio samples.

        Returns True if wake word detected above threshold.
        """
        predictions = self.model.predict(audio_chunk)
        for name, score in predictions.items():
            if score >= self.threshold:
                log.info("Wake word '%s' detected (score: %.2f)", name, score)
                self.model.reset()
                return True
        return False


async def _recv_until_audio_end(ws, player: AudioPlayer):
    """Receive server messages until audio_end (full response cycle complete).

    Returns True to continue session, False if session closed.
    """
    while True:
        msg = await asyncio.wait_for(ws.recv(), timeout=60)

        if isinstance(msg, bytes):
            player.feed(msg)
            continue

        data = decode_msg(msg)
        msg_type = data.get("type")

        if msg_type == MsgType.RESPONSE.value:
            print(f"Coach: {data.get('text', '')}")

        elif msg_type == MsgType.TRANSCRIPT.value:
            text = data.get("text", "")
            if text:
                print(f"You: {text}")

        elif msg_type == MsgType.AUDIO_START.value:
            continue

        elif msg_type == MsgType.AUDIO_END.value:
            player.play_and_clear()
            return True

        elif msg_type == MsgType.SESSION_CLOSED.value:
            log.info("Session closed by server")
            return False

        elif msg_type == MsgType.ERROR.value:
            log.error("Server error: %s", data.get("message"))
            return False

        elif msg_type == MsgType.ALARM_SCHEDULED.value:
            log.info("Alarm: %s at %s", data.get("desc"), data.get("time"))


async def run_session(ws, mode: str, mic_stream, pa, player: AudioPlayer, text_mode: bool = False):
    """Run a single coaching session over the WebSocket connection.

    Handles the listen -> send audio -> receive response -> play cycle.
    """
    await ws.send(encode_msg(MsgType.SESSION_START, mode=mode))

    # Wait for initial greeting from server
    if not await _recv_until_audio_end(ws, player):
        return

    while True:
        if text_mode:
            try:
                loop = asyncio.get_event_loop()
                text = await loop.run_in_executor(None, lambda: input("You: ").strip())
                if not text:
                    continue
                await ws.send(encode_msg(MsgType.TEXT_INPUT, text=text))
            except (EOFError, KeyboardInterrupt):
                await ws.send(encode_msg(MsgType.SESSION_END))
                return
        else:
            await _record_and_send(ws, mic_stream)

        # Wait for full response (text + audio) before prompting again
        if not await _recv_until_audio_end(ws, player):
            return


async def _record_and_send(ws, mic_stream):
    """Record audio until silence, then send to server."""
    await ws.send(encode_msg(MsgType.VAD_START))

    speech_started = False
    silent_chunks = 0
    chunks_per_second = SAMPLE_RATE / CHUNK_SIZE
    silence_limit = int(chunks_per_second * SILENCE_SECONDS)
    max_chunks = int(chunks_per_second * MAX_RECORD_SECONDS)

    log.debug("Listening...")

    for _ in range(max_chunks):
        data = mic_stream.read(CHUNK_SIZE, exception_on_overflow=False)
        level = _rms(data)

        if level > RMS_THRESHOLD:
            speech_started = True
            silent_chunks = 0
        elif speech_started:
            silent_chunks += 1
            if silent_chunks >= silence_limit:
                break

        if speech_started:
            await ws.send(data)

    await ws.send(encode_msg(MsgType.VAD_END))


async def main_loop(server_url: str, wake_word: str, threshold: float, text_mode: bool):
    """Main client loop: wake word detection -> session -> repeat."""
    import numpy as np
    import pyaudio

    pa = pyaudio.PyAudio()
    mic_stream = pa.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK_SIZE,
    )

    player = AudioPlayer()

    if not text_mode:
        detector = WakeWordDetector(wake_word, threshold)
        print(f"[listener] Listening for '{wake_word}' (threshold: {threshold})")
        print("[listener] Say the wake word to start a coaching session.")
    else:
        detector = None
        print("[client] Text mode — type to interact.")

    try:
        while True:
            if text_mode:
                # In text mode, start a session immediately
                import websockets
                async with websockets.connect(server_url) as ws:
                    await run_session(ws, "adhoc", mic_stream, pa, player, text_mode=True)
                break

            # Listen for wake word
            audio = mic_stream.read(CHUNK_SIZE, exception_on_overflow=False)
            audio_array = np.frombuffer(audio, dtype=np.int16)

            if detector and detector.detect(audio_array):
                print("[wake] Starting session...")
                try:
                    import websockets
                    async with websockets.connect(server_url) as ws:
                        await run_session(ws, "adhoc", mic_stream, pa, player)
                except Exception as exc:
                    log.error("Session failed: %s", exc)
                print(f"[listener] Listening for '{wake_word}'...")

    except KeyboardInterrupt:
        print("\n[client] Shutting down...")
    finally:
        mic_stream.stop_stream()
        mic_stream.close()
        pa.terminate()


def main():
    parser = argparse.ArgumentParser(description="Ultra Alarm Pi client")
    parser.add_argument(
        "--server",
        type=str,
        default=os.environ.get("ULTRA_ALARM_SERVER", "ws://ultra-alarm.local:8765"),
        help="WebSocket server URL (default: ws://ultra-alarm.local:8765)",
    )
    parser.add_argument(
        "--wake-word",
        type=str,
        default="hey_jarvis",
        help="Wake word model name or path (default: hey_jarvis)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Wake word detection threshold (default: 0.5)",
    )
    parser.add_argument(
        "--text",
        action="store_true",
        help="Text input mode (no mic, for testing)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    asyncio.run(main_loop(args.server, args.wake_word, args.threshold, args.text))


if __name__ == "__main__":
    main()
