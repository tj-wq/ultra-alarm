"""WebSocket protocol definitions shared between server and client.

Text frames carry JSON control messages. Binary frames carry raw PCM audio.

Audio format: 16-bit signed little-endian PCM, 16kHz, mono.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

# Audio constants
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit
CHUNK_DURATION_MS = 80  # match openWakeWord's expected chunk size
CHUNK_SIZE = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * CHUNK_DURATION_MS // 1000  # 2560 bytes


class MsgType(str, Enum):
    """Control message types sent as JSON text frames."""
    # Client -> Server
    SESSION_START = "session_start"   # {"mode": "morning"|"evening"|"adhoc"}
    SESSION_END = "session_end"
    TEXT_INPUT = "text_input"         # {"text": "..."} — for --text mode testing
    VAD_START = "vad_start"           # speech detected, audio frames follow
    VAD_END = "vad_end"              # speech ended, server should transcribe

    # Server -> Client
    TRANSCRIPT = "transcript"         # {"text": "..."} — what STT heard
    RESPONSE = "response"            # {"text": "..."} — coach's text reply
    AUDIO_START = "audio_start"      # TTS audio frames follow
    AUDIO_END = "audio_end"          # TTS audio done, client can listen again
    ALARM_SCHEDULED = "alarm_scheduled"  # {"time": "HH:MM", "desc": "..."}
    SESSION_CLOSED = "session_closed"
    ERROR = "error"                  # {"message": "..."}


def encode_msg(msg_type: MsgType, **kwargs) -> str:
    """Encode a control message as JSON text."""
    return json.dumps({"type": msg_type.value, **kwargs})


def decode_msg(text: str) -> dict:
    """Decode a JSON control message."""
    return json.loads(text)
