"""Text-to-speech using Piper, producing raw PCM audio chunks for streaming."""

from __future__ import annotations

import io
import logging
import subprocess
import tempfile
import os
import wave

from protocol import SAMPLE_RATE

log = logging.getLogger(__name__)

# Piper outputs 16-bit mono WAV at the model's sample rate (usually 22050).
# We resample to 16kHz to match our protocol.
_PIPER_BINARY = os.environ.get("PIPER_BINARY", "/opt/piper/piper")
_PIPER_MODEL = os.environ.get("PIPER_MODEL", "/opt/piper/models/en_US-lessac-medium.onnx")
_TTS_CHUNK_SENTENCES = True  # Stream sentence-by-sentence for lower perceived latency


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences for streaming TTS."""
    import re
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [p for p in parts if p]


def synthesize_pcm(text: str) -> bytes:
    """Synthesize text to raw 16kHz 16-bit mono PCM bytes.

    Uses Piper TTS with sox resampling to match protocol sample rate.
    Falls back to espeak if Piper is unavailable.
    """
    try:
        return _synthesize_piper(text)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        log.warning("Piper TTS failed (%s), falling back to espeak", exc)
        return _synthesize_espeak(text)


def synthesize_sentences(text: str):
    """Generator yielding (sentence_text, pcm_bytes) for each sentence.

    Allows the server to stream audio back sentence-by-sentence.
    """
    sentences = _split_sentences(text) if _TTS_CHUNK_SENTENCES else [text]
    for sentence in sentences:
        pcm = synthesize_pcm(sentence)
        if pcm:
            yield sentence, pcm


def _synthesize_piper(text: str) -> bytes:
    """Run Piper TTS and return resampled PCM audio."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        subprocess.run(
            [_PIPER_BINARY, "--model", _PIPER_MODEL, "--output_file", tmp_path],
            input=text,
            text=True,
            check=True,
            capture_output=True,
        )
        # Resample to 16kHz mono 16-bit with sox
        resampled_path = tmp_path + ".16k.wav"
        subprocess.run(
            ["sox", tmp_path, "-r", str(SAMPLE_RATE), "-c", "1", "-b", "16", resampled_path],
            check=True,
            capture_output=True,
        )
        # Read raw PCM from the resampled WAV
        with wave.open(resampled_path, "rb") as wf:
            pcm = wf.readframes(wf.getnframes())
        os.unlink(resampled_path)
        return pcm
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _synthesize_espeak(text: str) -> bytes:
    """Fallback TTS using espeak + sox."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        with open(tmp_path, "wb") as out_f:
            subprocess.run(
                ["espeak", "--stdout", text],
                stdout=out_f,
                check=True,
            )
        resampled_path = tmp_path + ".16k.wav"
        subprocess.run(
            ["sox", tmp_path, "-r", str(SAMPLE_RATE), "-c", "1", "-b", "16", resampled_path],
            check=True,
            capture_output=True,
        )
        with wave.open(resampled_path, "rb") as wf:
            pcm = wf.readframes(wf.getnframes())
        os.unlink(resampled_path)
        return pcm
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
