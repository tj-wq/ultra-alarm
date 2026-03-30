#!/usr/bin/env bash
#
# train_rvc.sh - Train an RVC v2 voice model from Rocky audio clips using Applio
#
# Takes a directory of clean Rocky audio clips and trains a Retrieval-Based
# Voice Conversion model that can transform any voice into Rocky's voice.
#
# Usage: ./train_rvc.sh <rocky_clips_dir> <output_model_dir> [epochs]
#
# Arguments:
#   rocky_clips_dir   - Directory of clean Rocky audio clips (.wav)
#   output_model_dir  - Directory to save the trained RVC model
#   epochs            - Optional: training epochs (default: 300, recommended 200-500)
#

set -euo pipefail

# --- Usage ---
if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <rocky_clips_dir> <output_model_dir> [epochs]" >&2
    echo "" >&2
    echo "Arguments:" >&2
    echo "  rocky_clips_dir   - Directory of clean Rocky .wav clips" >&2
    echo "  output_model_dir  - Where to save the trained RVC model" >&2
    echo "  epochs            - Training epochs (default: 300, recommended: 200-500)" >&2
    exit 1
fi

ROCKY_CLIPS_DIR="$(realpath "$1")"
OUTPUT_MODEL_DIR="$(realpath "$2" 2>/dev/null || echo "$2")"
EPOCHS="${3:-300}"

MODEL_NAME="rocky-rvc-v2"

# --- AMD ROCm environment for RX 9070 XT (RDNA 4 / gfx1201) ---
export HSA_OVERRIDE_GFX_VERSION=12.0.1
export GPU_MAX_ALLOC_PERCENT=100

# --- Validate inputs ---
if [[ ! -d "$ROCKY_CLIPS_DIR" ]]; then
    echo "Error: Rocky clips directory not found: $ROCKY_CLIPS_DIR" >&2
    exit 1
fi

WAV_COUNT=$(find "$ROCKY_CLIPS_DIR" -maxdepth 1 -name "*.wav" -type f | wc -l)
if [[ "$WAV_COUNT" -eq 0 ]]; then
    echo "Error: No .wav files found in $ROCKY_CLIPS_DIR" >&2
    exit 1
fi

# Locate Applio
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
    echo "Run setup_textymcspeechy.sh first, or set APPLIO_DIR environment variable." >&2
    exit 1
fi

echo "=========================================="
echo "  RVC v2 Voice Training - Rocky"
echo "=========================================="
echo "Rocky clips:  $ROCKY_CLIPS_DIR ($WAV_COUNT wav files)"
echo "Output dir:   $OUTPUT_MODEL_DIR"
echo "Model name:   $MODEL_NAME"
echo "Epochs:       $EPOCHS"
echo "Applio:       $APPLIO_DIR"
echo "GPU:          AMD RX 9070 XT (ROCm)"
echo "HSA_OVERRIDE_GFX_VERSION=$HSA_OVERRIDE_GFX_VERSION"
echo "GPU_MAX_ALLOC_PERCENT=$GPU_MAX_ALLOC_PERCENT"
echo ""

# --- Step 1: Prepare training data ---
echo "[Step 1/3] Preparing training data..."

TRAINING_DATA_DIR="$OUTPUT_MODEL_DIR/training_data"
mkdir -p "$TRAINING_DATA_DIR"

# Copy Rocky clips into Applio's expected structure
# RVC expects a flat directory of wav files
for wav in "$ROCKY_CLIPS_DIR"/*.wav; do
    cp "$wav" "$TRAINING_DATA_DIR/"
done

echo "  Copied $WAV_COUNT clips to $TRAINING_DATA_DIR"
echo ""

# --- Step 2: Run RVC training via Applio ---
echo "[Step 2/3] Training RVC v2 model ($EPOCHS epochs)..."
echo "  This may take a while depending on dataset size and GPU."
echo ""

cd "$APPLIO_DIR"

# Applio CLI training interface
# Uses RVC v2 with f0 extraction (crepe method for quality)
python3 core.py train \
    --model_name "$MODEL_NAME" \
    --dataset_path "$TRAINING_DATA_DIR" \
    --sample_rate 40000 \
    --epochs "$EPOCHS" \
    --batch_size 8 \
    --rvc_version v2 \
    --f0_method crepe \
    --save_every_epoch 50 \
    --cache_data_every_n_epoch 10 \
    2>&1 | tee "$OUTPUT_MODEL_DIR/training.log"

cd -

echo ""
echo "[Step 2/3] Training complete."
echo ""

# --- Step 3: Copy trained model to output ---
echo "[Step 3/3] Collecting trained model..."

mkdir -p "$OUTPUT_MODEL_DIR"

# Applio saves models to its own logs directory; copy the final model out
APPLIO_MODEL_DIR="$APPLIO_DIR/logs/$MODEL_NAME"
if [[ -d "$APPLIO_MODEL_DIR" ]]; then
    # Copy the .pth model file and index
    find "$APPLIO_MODEL_DIR" -name "*.pth" -exec cp {} "$OUTPUT_MODEL_DIR/" \;
    find "$APPLIO_MODEL_DIR" -name "*.index" -exec cp {} "$OUTPUT_MODEL_DIR/" \;
    echo "  Model files copied to $OUTPUT_MODEL_DIR"
else
    echo "  Warning: Could not find Applio model output at $APPLIO_MODEL_DIR" >&2
    echo "  Check $APPLIO_DIR/logs/ for the trained model." >&2
fi

echo ""
echo "=========================================="
echo "  RVC Training Complete!"
echo "=========================================="
echo ""
echo "Model directory: $OUTPUT_MODEL_DIR"
echo "Training log:    $OUTPUT_MODEL_DIR/training.log"
echo ""
echo "Next step: convert a dataset using the trained model:"
echo "  ./convert_dataset.sh <lessac_dataset_dir> $OUTPUT_MODEL_DIR/<model>.pth <output_dir>"
echo ""
