#!/usr/bin/env bash
#
# convert_dataset.sh - Convert an LJSpeech dataset to Rocky's voice via RVC
#
# Takes the standard lessac-medium LJSpeech dataset and runs each WAV through
# Applio voice conversion using a trained Rocky RVC model. Optionally applies
# voice_filter.sh for the translation computer quality effect.
#
# Usage: ./convert_dataset.sh <lessac_dataset_dir> <rvc_model_path> <output_dir> [filter_preset]
#
# Arguments:
#   lessac_dataset_dir  - LJSpeech dataset directory (must contain wavs/ and metadata.csv)
#   rvc_model_path      - Path to the trained Rocky RVC .pth model file
#   output_dir          - Output directory for the converted dataset
#   filter_preset       - Optional: apply voice_filter.sh (subtle, medium, heavy, or "none" to skip)
#

set -euo pipefail

# --- Usage ---
if [[ $# -lt 3 ]]; then
    echo "Usage: $0 <lessac_dataset_dir> <rvc_model_path> <output_dir> [filter_preset]" >&2
    echo "" >&2
    echo "Arguments:" >&2
    echo "  lessac_dataset_dir  - LJSpeech dataset with wavs/ and metadata.csv" >&2
    echo "  rvc_model_path      - Trained Rocky RVC .pth model file" >&2
    echo "  output_dir          - Output directory for converted dataset" >&2
    echo "  filter_preset       - Optional: subtle, medium, heavy, or none (default: none)" >&2
    exit 1
fi

INPUT_DIR="$(realpath "$1")"
RVC_MODEL="$(realpath "$2")"
OUTPUT_DIR="$(realpath "$3" 2>/dev/null || echo "$3")"
FILTER_PRESET="${4:-none}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROCKY_FILTER="$SCRIPT_DIR/../voice_filter.sh"

# --- AMD ROCm environment for RX 9070 XT (RDNA 4 / gfx1201) ---
export HSA_OVERRIDE_GFX_VERSION=12.0.1
export GPU_MAX_ALLOC_PERCENT=100

# --- Validate inputs ---
if [[ ! -d "$INPUT_DIR" ]]; then
    echo "Error: Dataset directory not found: $INPUT_DIR" >&2
    exit 1
fi

if [[ ! -f "$INPUT_DIR/metadata.csv" ]]; then
    echo "Error: metadata.csv not found in $INPUT_DIR" >&2
    exit 1
fi

if [[ ! -d "$INPUT_DIR/wavs" ]]; then
    echo "Error: wavs/ directory not found in $INPUT_DIR" >&2
    exit 1
fi

if [[ ! -f "$RVC_MODEL" ]]; then
    echo "Error: RVC model not found: $RVC_MODEL" >&2
    exit 1
fi

if [[ "$FILTER_PRESET" != "none" ]] && [[ ! -f "$ROCKY_FILTER" ]]; then
    echo "Error: voice_filter.sh not found at: $ROCKY_FILTER" >&2
    echo "Set filter_preset to 'none' to skip filtering, or ensure voice_filter.sh exists." >&2
    exit 1
fi

# Locate Applio
APPLIO_DIR=""
for candidate in \
    "$SCRIPT_DIR/textymcspeechy/Applio" \
    "$SCRIPT_DIR/../textymcspeechy/Applio" \
    "$SCRIPT_DIR/Applio" \
    "./Applio"; do
    if [[ -d "$candidate" ]]; then
        APPLIO_DIR="$(realpath "$candidate")"
        break
    fi
done

if [[ -z "$APPLIO_DIR" ]]; then
    echo "Error: Applio installation not found." >&2
    echo "Run setup_textymcspeechy.sh first." >&2
    exit 1
fi

# Look for an index file alongside the model
RVC_INDEX=""
MODEL_DIR="$(dirname "$RVC_MODEL")"
INDEX_FILE=$(find "$MODEL_DIR" -maxdepth 1 -name "*.index" -type f | head -1)
if [[ -n "$INDEX_FILE" ]]; then
    RVC_INDEX="$INDEX_FILE"
fi

# --- Setup output ---
OUTPUT_WAVS="$OUTPUT_DIR/wavs"
mkdir -p "$OUTPUT_WAVS"

TMPDIR_CONVERT=$(mktemp -d)
trap "rm -rf '$TMPDIR_CONVERT'" EXIT

# Count input files
TOTAL_WAVS=$(find "$INPUT_DIR/wavs" -maxdepth 1 -name "*.wav" -type f | wc -l)

echo "=========================================="
echo "  RVC Dataset Conversion - Rocky Voice"
echo "=========================================="
echo "Input dataset:  $INPUT_DIR"
echo "RVC model:      $RVC_MODEL"
echo "RVC index:      ${RVC_INDEX:-none}"
echo "Output dir:     $OUTPUT_DIR"
echo "Filter preset:  $FILTER_PRESET"
echo "Total WAVs:     $TOTAL_WAVS"
echo "GPU:            AMD RX 9070 XT (ROCm)"
echo ""

# --- Convert each WAV ---
echo "[Step 1/2] Converting WAV files through RVC voice conversion..."
echo ""

CONVERTED=0
FAILED=0

for wav_file in "$INPUT_DIR/wavs"/*.wav; do
    BASENAME="$(basename "$wav_file")"
    CONVERTED_WAV="$TMPDIR_CONVERT/$BASENAME"
    FINAL_WAV="$OUTPUT_WAVS/$BASENAME"

    CONVERTED=$((CONVERTED + 1))
    printf "\r  [%d/%d] Converting %s..." "$CONVERTED" "$TOTAL_WAVS" "$BASENAME"

    # Run Applio voice conversion
    # Uses the RVC model to transform the voice while preserving speech content
    INFER_ARGS=(
        --model_path "$RVC_MODEL"
        --input_path "$wav_file"
        --output_path "$CONVERTED_WAV"
        --f0_method crepe
        --f0_up_key 0
        --filter_radius 3
        --index_rate 0.75
        --rms_mix_rate 0.25
        --protect 0.33
    )

    if [[ -n "$RVC_INDEX" ]]; then
        INFER_ARGS+=(--index_path "$RVC_INDEX")
    fi

    if ! python3 "$APPLIO_DIR/core.py" infer "${INFER_ARGS[@]}" 2>/dev/null; then
        FAILED=$((FAILED + 1))
        continue
    fi

    # Apply voice_filter.sh if requested
    if [[ "$FILTER_PRESET" != "none" ]] && [[ -f "$CONVERTED_WAV" ]]; then
        bash "$ROCKY_FILTER" "$CONVERTED_WAV" "$FINAL_WAV" "$FILTER_PRESET" 2>/dev/null
        if [[ ! -f "$FINAL_WAV" ]]; then
            # Filter failed; use the unconverted RVC output
            cp "$CONVERTED_WAV" "$FINAL_WAV"
        fi
    elif [[ -f "$CONVERTED_WAV" ]]; then
        cp "$CONVERTED_WAV" "$FINAL_WAV"
    fi
done

echo ""
echo ""
echo "  Converted: $((CONVERTED - FAILED))/$TOTAL_WAVS  (failed: $FAILED)"
echo ""

# --- Copy metadata ---
echo "[Step 2/2] Copying metadata..."

cp "$INPUT_DIR/metadata.csv" "$OUTPUT_DIR/metadata.csv"

echo "  metadata.csv copied."
echo ""
echo "=========================================="
echo "  Dataset Conversion Complete!"
echo "=========================================="
echo ""
echo "Output dataset: $OUTPUT_DIR"
echo "  WAVs:         $OUTPUT_WAVS"
echo "  Metadata:     $OUTPUT_DIR/metadata.csv"
echo ""
echo "Next step: fine-tune Piper on the converted dataset:"
echo "  ./train_piper.sh $OUTPUT_DIR /path/to/lessac-medium.ckpt"
echo ""
