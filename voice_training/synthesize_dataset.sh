#!/usr/bin/env bash
#
# synthesize_dataset.sh - Build an LJSpeech dataset from a phrases text file
#
# For each line in the phrases file, synthesizes audio via Piper TTS,
# applies the rocky_filter.sh post-processing, and saves to wavs/.
# Builds metadata.csv in LJSpeech format: clip_name|text
#
# Usage: ./synthesize_dataset.sh phrases.txt /path/to/base-model.onnx output_dir/ [filter_preset]
#
# Arguments:
#   phrases.txt       - Text file with one phrase per line
#   base-model.onnx   - Path to Piper ONNX model for synthesis
#   output_dir/       - Output directory (will contain wavs/ and metadata.csv)
#   filter_preset     - Optional: subtle, medium (default), or heavy
#

set -euo pipefail

# --- Usage ---
if [[ $# -lt 3 ]]; then
    echo "Usage: $0 phrases.txt /path/to/model.onnx output_dir/ [filter_preset]" >&2
    echo "" >&2
    echo "Arguments:" >&2
    echo "  phrases.txt     - One phrase per line" >&2
    echo "  model.onnx      - Piper base model for synthesis" >&2
    echo "  output_dir/     - Output directory for wavs/ and metadata.csv" >&2
    echo "  filter_preset   - Optional: subtle, medium (default), heavy" >&2
    exit 1
fi

PHRASES_FILE="$1"
PIPER_MODEL="$2"
OUTPUT_DIR="$3"
FILTER_PRESET="${4:-medium}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROCKY_FILTER="$SCRIPT_DIR/../rocky_filter.sh"

# --- Validate inputs ---
if [[ ! -f "$PHRASES_FILE" ]]; then
    echo "Error: Phrases file not found: $PHRASES_FILE" >&2
    exit 1
fi

if [[ ! -f "$PIPER_MODEL" ]]; then
    echo "Error: Piper model not found: $PIPER_MODEL" >&2
    exit 1
fi

if ! command -v piper &>/dev/null; then
    echo "Error: piper is not installed or not in PATH" >&2
    exit 1
fi

if [[ ! -f "$ROCKY_FILTER" ]]; then
    echo "Error: rocky_filter.sh not found at: $ROCKY_FILTER" >&2
    exit 1
fi

# --- Setup output directories ---
WAVS_DIR="$OUTPUT_DIR/wavs"
mkdir -p "$WAVS_DIR"

METADATA_FILE="$OUTPUT_DIR/metadata.csv"
TMPDIR_SYNTH=$(mktemp -d)
trap "rm -rf '$TMPDIR_SYNTH'" EXIT

# Clear metadata if it exists
> "$METADATA_FILE"

# --- Process each phrase ---
CLIP_INDEX=1
TOTAL_LINES=$(wc -l < "$PHRASES_FILE")
echo "Synthesizing $TOTAL_LINES phrases with preset '$FILTER_PRESET'..."
echo ""

while IFS= read -r phrase || [[ -n "$phrase" ]]; do
    # Skip empty lines
    if [[ -z "${phrase// /}" ]]; then
        continue
    fi

    CLIP_NAME=$(printf "clip_%04d" "$CLIP_INDEX")
    RAW_WAV="$TMPDIR_SYNTH/${CLIP_NAME}_raw.wav"
    FINAL_WAV="$WAVS_DIR/${CLIP_NAME}.wav"

    printf "[%d/%d] %s\n" "$CLIP_INDEX" "$TOTAL_LINES" "$CLIP_NAME"

    # Synthesize with Piper
    echo "$phrase" | piper \
        --model "$PIPER_MODEL" \
        --output_file "$RAW_WAV" \
        2>/dev/null

    if [[ ! -f "$RAW_WAV" ]]; then
        echo "  [warn] Piper synthesis failed for: $phrase" >&2
        continue
    fi

    # Apply Rocky voice filter
    bash "$ROCKY_FILTER" "$RAW_WAV" "$FINAL_WAV" "$FILTER_PRESET" 2>/dev/null

    if [[ ! -f "$FINAL_WAV" ]]; then
        echo "  [warn] Rocky filter failed for: $CLIP_NAME" >&2
        continue
    fi

    # Append to metadata
    echo "${CLIP_NAME}|${phrase}" >> "$METADATA_FILE"

    CLIP_INDEX=$((CLIP_INDEX + 1))

done < "$PHRASES_FILE"

TOTAL_CLIPS=$((CLIP_INDEX - 1))
echo ""
echo "Done. Synthesized $TOTAL_CLIPS clips."
echo "  WAVs:     $WAVS_DIR"
echo "  Metadata: $METADATA_FILE"
