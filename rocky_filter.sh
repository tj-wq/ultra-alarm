#!/usr/bin/env bash
#
# rocky_filter.sh - Rocky's Translation Computer Voice Filter
#
# Transforms TTS audio output to sound like Rocky's "translation computer"
# from Project Hail Mary by Andy Weir.
#
# Usage: ./rocky_filter.sh input.wav output.wav [preset]
#
# Presets:
#   subtle  - Light processing. Slight pitch shift down (1-2 semitones),
#             wide bandpass (200-6000 Hz), gentle high-freq rolloff, normalize.
#             Good for intelligibility with just a hint of alien computer.
#
#   medium  - Default. Pitch shift down 2-3 semitones, bandpass 300-5000 Hz,
#             sample rate crunch (downsample to 11025 Hz for bitcrushy feel),
#             subtle small-room reverb, high-freq rolloff, normalize.
#             The sweet spot between recognizable speech and alien machine.
#
#   heavy   - Full alien computer. Pitch shift down 3-4 semitones, narrow
#             bandpass (400-4000 Hz), aggressive sample rate crunch (8000 Hz),
#             more reverb, light overdrive/distortion, high-freq rolloff,
#             normalize. Speech is still parseable but clearly inhuman.
#

set -euo pipefail

# --- Dependency check ---
if ! command -v sox &>/dev/null; then
    echo "Error: sox is not installed. Install it with: sudo apt install sox" >&2
    exit 1
fi

# --- Usage ---
if [[ $# -lt 2 ]]; then
    echo "Usage: $0 input.wav output.wav [subtle|medium|heavy]" >&2
    echo "  Presets: subtle, medium (default), heavy" >&2
    exit 1
fi

INPUT="$1"
OUTPUT="$2"
PRESET="${3:-medium}"

# --- Validate input ---
if [[ ! -f "$INPUT" ]]; then
    echo "Error: Input file not found: $INPUT" >&2
    exit 1
fi

OUTPUT_DIR="$(dirname "$OUTPUT")"
if [[ ! -d "$OUTPUT_DIR" ]]; then
    echo "Error: Output directory does not exist: $OUTPUT_DIR" >&2
    exit 1
fi
if [[ ! -w "$OUTPUT_DIR" ]]; then
    echo "Error: Output directory is not writable: $OUTPUT_DIR" >&2
    exit 1
fi

# Get original sample rate for resampling tricks
ORIG_RATE=$(soxi -r "$INPUT")

# --- Preset functions ---

preset_subtle() {
    echo "Applying preset: subtle"
    sox "$INPUT" "$OUTPUT" \
        pitch -200 \
        sinc 200-6000 \
        lowpass 5500 \
        norm
}

preset_medium() {
    echo "Applying preset: medium"

    local tmpdir
    tmpdir=$(mktemp -d)
    trap "rm -rf '$tmpdir'" EXIT

    # Step 1: pitch shift + bandpass + high-freq rolloff
    sox "$INPUT" "$tmpdir/stage1.wav" \
        pitch -350 \
        sinc 300-5000 \
        lowpass 4500

    # Step 2: sample rate crunch (downsample to 11025 then back up)
    sox "$tmpdir/stage1.wav" -r 11025 "$tmpdir/stage2.wav"
    sox "$tmpdir/stage2.wav" -r "$ORIG_RATE" "$tmpdir/stage3.wav"

    # Step 3: subtle reverb + normalize
    sox "$tmpdir/stage3.wav" "$OUTPUT" \
        reverb 20 50 80 \
        norm
}

preset_heavy() {
    echo "Applying preset: heavy"

    local tmpdir
    tmpdir=$(mktemp -d)
    trap "rm -rf '$tmpdir'" EXIT

    # Step 1: pitch shift + narrow bandpass + high-freq rolloff
    sox "$INPUT" "$tmpdir/stage1.wav" \
        pitch -500 \
        sinc 400-4000 \
        lowpass 3500

    # Step 2: aggressive sample rate crunch (downsample to 8000 then back up)
    sox "$tmpdir/stage1.wav" -r 8000 "$tmpdir/stage2.wav"
    sox "$tmpdir/stage2.wav" -r "$ORIG_RATE" "$tmpdir/stage3.wav"

    # Step 3: light overdrive + reverb + normalize
    sox "$tmpdir/stage3.wav" "$OUTPUT" \
        overdrive 15 \
        reverb 35 60 90 \
        norm
}

# --- Run selected preset ---
case "$PRESET" in
    subtle) preset_subtle ;;
    medium) preset_medium ;;
    heavy)  preset_heavy  ;;
    *)
        echo "Error: Unknown preset '$PRESET'. Choose: subtle, medium, heavy" >&2
        exit 1
        ;;
esac

echo "Done: $OUTPUT"
