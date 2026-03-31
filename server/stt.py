"""Speech-to-text using faster-whisper (GPU-accelerated)."""

from __future__ import annotations

import io
import wave
import logging

from protocol import SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH

log = logging.getLogger(__name__)

_model = None


def _get_model(model_size: str = "large-v3", device: str = "cuda"):
    """Lazy-load the faster-whisper model."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        log.info("Loading faster-whisper model: %s on %s", model_size, device)
        _model = WhisperModel(
            model_size,
            device=device,
            compute_type="float16" if device == "cuda" else "int8",
        )
        log.info("Model loaded.")
    return _model


def transcribe(pcm_audio: bytes, model_size: str = "large-v3", device: str = "cuda") -> str:
    """Transcribe raw PCM audio bytes to text.

    Args:
        pcm_audio: Raw 16-bit signed LE PCM, 16kHz, mono.
        model_size: Whisper model size.
        device: "cuda" or "cpu".

    Returns:
        Transcribed text, or empty string on failure.
    """
    if not pcm_audio:
        return ""

    # Wrap PCM in a WAV in-memory for faster-whisper
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_audio)
    buf.seek(0)

    model = _get_model(model_size, device)
    segments, _ = model.transcribe(buf, language="en", vad_filter=True)

    text = " ".join(seg.text.strip() for seg in segments).strip()
    return text
