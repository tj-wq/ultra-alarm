#!/usr/bin/env python3
"""Prepare Rocky source audio for RVC voice model training.

Primary use: clean and segment Rocky audio clips for training an RVC v2 model
via Applio. Outputs clean mono WAV files at 44.1kHz (optimal for RVC training).

Can also output LJSpeech-format datasets (22050 Hz, with metadata.csv) when
used with --format ljspeech, for direct Piper fine-tuning.

Workflow:
  1. Accepts a directory of source audio clips (any common format)
  2. Cleans them up (normalize volume, trim silence, reduce background noise)
  3. Splits long clips on silence gaps into individual utterances
  4. Transcribes each segment via Whisper (for LJSpeech format)
  5. Outputs either:
     - RVC format (default): clean 44.1kHz mono WAVs in a flat directory
     - LJSpeech format: 22050 Hz WAVs in wavs/ with metadata.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import wave
from pathlib import Path

from pydub import AudioSegment
from pydub.effects import normalize
from pydub.silence import split_on_silence


def clean_audio(segment: AudioSegment) -> AudioSegment:
    """Normalize volume and apply basic cleanup to an audio segment.

    - Normalizes peak amplitude
    - High-pass filter at 80 Hz to cut low-frequency rumble
    - Low-pass filter at 8000 Hz to cut hiss (voice content preserved)
    """
    segment = normalize(segment)
    segment = segment.high_pass_filter(80)
    segment = segment.low_pass_filter(8000)
    return segment


def split_utterances(
    segment: AudioSegment,
    min_silence_len_ms: int = 500,
    silence_thresh_dbfs: int = -40,
    min_utterance_ms: int = 300,
) -> list[AudioSegment]:
    """Split an audio segment into individual utterances on silence gaps.

    Returns a list of AudioSegment chunks, each representing one utterance.
    Very short chunks (below min_utterance_ms) are discarded.
    """
    chunks = split_on_silence(
        segment,
        min_silence_len=min_silence_len_ms,
        silence_thresh=silence_thresh_dbfs,
        keep_silence=150,
    )
    return [c for c in chunks if len(c) >= min_utterance_ms]


def export_wav(segment: AudioSegment, path: Path, sample_rate: int = 44100) -> None:
    """Export an AudioSegment as a mono 16-bit WAV.

    Args:
        segment: Audio to export.
        path: Output file path.
        sample_rate: Sample rate in Hz. Default 44100 for RVC training,
                     use 22050 for LJSpeech/Piper format.
    """
    segment = segment.set_frame_rate(sample_rate).set_channels(1).set_sample_width(2)
    segment.export(str(path), format="wav")


def transcribe_wav(wav_path: Path, whisper_model: str = "base.en") -> str:
    """Transcribe a WAV file using openai-whisper.

    Returns the transcribed text stripped of leading/trailing whitespace,
    or an empty string on failure.
    """
    try:
        import whisper
    except ImportError:
        print("[error] openai-whisper not installed. Run: pip install openai-whisper", file=sys.stderr)
        return ""

    model = whisper.load_model(whisper_model)
    result = model.transcribe(str(wav_path), language="en", fp16=False)
    text: str = result.get("text", "").strip()
    # Remove characters that are problematic for TTS training metadata
    text = text.replace("|", " ").replace("\n", " ")
    return text


def prepare_dataset(
    source_dir: Path,
    output_dir: Path,
    output_format: str = "rvc",
    whisper_model: str = "base.en",
    min_silence_len_ms: int = 500,
    silence_thresh_dbfs: int = -40,
) -> None:
    """Process all audio files in source_dir and produce a training dataset.

    Args:
        source_dir: Directory containing source audio clips (.wav, .mp3, .flac, .ogg).
        output_dir: Directory where output files will be created.
        output_format: "rvc" for 44.1kHz clean WAVs (RVC training), or
                       "ljspeech" for 22050 Hz WAVs with metadata.csv (Piper training).
        whisper_model: Whisper model size for transcription (ljspeech format only).
        min_silence_len_ms: Minimum silence length (ms) used for splitting.
        silence_thresh_dbfs: Silence threshold in dBFS for splitting.
    """
    sample_rate = 44100 if output_format == "rvc" else 22050

    if output_format == "ljspeech":
        wavs_dir = output_dir / "wavs"
    else:
        wavs_dir = output_dir
    wavs_dir.mkdir(parents=True, exist_ok=True)

    audio_extensions = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
    source_files = sorted(
        p for p in source_dir.iterdir()
        if p.suffix.lower() in audio_extensions
    )

    if not source_files:
        print(f"[error] No audio files found in {source_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(source_files)} audio file(s) in {source_dir}")

    clip_index = 1
    metadata_rows: list[tuple[str, str]] = []

    for src_path in source_files:
        print(f"Processing: {src_path.name}")
        try:
            audio = AudioSegment.from_file(str(src_path))
        except Exception as exc:
            print(f"  [warn] Could not load {src_path.name}: {exc}", file=sys.stderr)
            continue

        audio = clean_audio(audio)
        utterances = split_utterances(
            audio,
            min_silence_len_ms=min_silence_len_ms,
            silence_thresh_dbfs=silence_thresh_dbfs,
        )

        if not utterances:
            # If splitting produced nothing, treat the whole clip as one utterance
            utterances = [audio]

        print(f"  Split into {len(utterances)} utterance(s)")

        for utterance in utterances:
            clip_name = f"clip_{clip_index:04d}"
            wav_path = wavs_dir / f"{clip_name}.wav"
            export_wav(utterance, wav_path, sample_rate=sample_rate)

            if output_format == "ljspeech":
                print(f"  Transcribing {clip_name}...")
                text = transcribe_wav(wav_path, whisper_model=whisper_model)

                if not text:
                    print(f"  [warn] Empty transcription for {clip_name}, skipping")
                    wav_path.unlink(missing_ok=True)
                    continue

                metadata_rows.append((clip_name, text))

            clip_index += 1

    if output_format == "ljspeech":
        # Write metadata.csv
        metadata_path = output_dir / "metadata.csv"
        with open(metadata_path, "w", encoding="utf-8", newline="") as f:
            for clip_name, text in metadata_rows:
                f.write(f"{clip_name}|{text}\n")

        print(f"\nDataset ready: {len(metadata_rows)} clips")
        print(f"  WAVs:     {wavs_dir}")
        print(f"  Metadata: {metadata_path}")
    else:
        total_clips = clip_index - 1
        print(f"\nDataset ready: {total_clips} clips (RVC format, {sample_rate} Hz)")
        print(f"  WAVs: {wavs_dir}")


def main() -> None:
    """CLI entry point for dataset preparation."""
    parser = argparse.ArgumentParser(
        description=(
            "Prepare Rocky source audio for RVC training (default) or LJSpeech format. "
            "Cleans, segments, and exports audio clips."
        ),
    )
    parser.add_argument(
        "source_dir",
        type=Path,
        help="Directory containing source audio clips",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path("dataset"),
        help="Output directory (default: ./dataset)",
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=["rvc", "ljspeech"],
        default="rvc",
        help="Output format: 'rvc' for 44.1kHz clean WAVs (default), "
             "'ljspeech' for 22050 Hz with metadata.csv",
    )
    parser.add_argument(
        "--whisper-model",
        type=str,
        default="base.en",
        help="Whisper model for transcription, ljspeech format only (default: base.en)",
    )
    parser.add_argument(
        "--min-silence",
        type=int,
        default=500,
        help="Minimum silence length in ms for utterance splitting (default: 500)",
    )
    parser.add_argument(
        "--silence-thresh",
        type=int,
        default=-40,
        help="Silence threshold in dBFS (default: -40)",
    )

    args = parser.parse_args()

    if not args.source_dir.is_dir():
        print(f"[error] Source directory not found: {args.source_dir}", file=sys.stderr)
        sys.exit(1)

    prepare_dataset(
        source_dir=args.source_dir,
        output_dir=args.output,
        output_format=args.format,
        whisper_model=args.whisper_model,
        min_silence_len_ms=args.min_silence,
        silence_thresh_dbfs=args.silence_thresh,
    )


if __name__ == "__main__":
    main()
