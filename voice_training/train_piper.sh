#!/usr/bin/env bash
#
# train_piper.sh - Fine-tune a Piper voice model on AMD RX 9070 XT with ROCm
#
# Runs preprocessing and training for a Piper TTS model using an LJSpeech
# dataset. Configured for AMD RDNA 4 GPU via ROCm.
#
# Usage: ./train.sh dataset_dir/ /path/to/lessac-medium-checkpoint.ckpt
#
# Arguments:
#   dataset_dir/      - LJSpeech dataset directory (must contain wavs/ and metadata.csv)
#   checkpoint.ckpt   - Path to the lessac-medium pretrained checkpoint to resume from
#

set -euo pipefail

# --- Usage ---
if [[ $# -lt 2 ]]; then
    echo "Usage: $0 dataset_dir/ /path/to/lessac-medium-checkpoint.ckpt" >&2
    echo "" >&2
    echo "Arguments:" >&2
    echo "  dataset_dir/    - LJSpeech dataset with wavs/ and metadata.csv" >&2
    echo "  checkpoint.ckpt - Pretrained checkpoint to resume from" >&2
    exit 1
fi

DATASET_DIR="$(realpath "$1")"
CHECKPOINT="$(realpath "$2")"

# --- Validate inputs ---
if [[ ! -d "$DATASET_DIR" ]]; then
    echo "Error: Dataset directory not found: $DATASET_DIR" >&2
    exit 1
fi

if [[ ! -f "$DATASET_DIR/metadata.csv" ]]; then
    echo "Error: metadata.csv not found in $DATASET_DIR" >&2
    exit 1
fi

if [[ ! -d "$DATASET_DIR/wavs" ]]; then
    echo "Error: wavs/ directory not found in $DATASET_DIR" >&2
    exit 1
fi

if [[ ! -f "$CHECKPOINT" ]]; then
    echo "Error: Checkpoint file not found: $CHECKPOINT" >&2
    exit 1
fi

# --- AMD ROCm environment for RX 9070 XT (RDNA 4 / gfx1201) ---
export HSA_OVERRIDE_GFX_VERSION=12.0.1
export GPU_MAX_ALLOC_PERCENT=100

echo "=========================================="
echo "  Piper Voice Training - AMD ROCm"
echo "=========================================="
echo "Dataset:    $DATASET_DIR"
echo "Checkpoint: $CHECKPOINT"
echo "GPU:        AMD RX 9070 XT (ROCm)"
echo "HSA_OVERRIDE_GFX_VERSION=$HSA_OVERRIDE_GFX_VERSION"
echo "GPU_MAX_ALLOC_PERCENT=$GPU_MAX_ALLOC_PERCENT"
echo ""

# --- Step 1: Preprocessing ---
echo "[Step 1/2] Running Piper preprocessing..."
echo ""

python3 -m piper_train.preprocess \
    --language en \
    --input-dir "$DATASET_DIR" \
    --output-dir "$DATASET_DIR/training" \
    --dataset-format ljspeech \
    --single-speaker \
    --sample-rate 22050

echo ""
echo "[Step 1/2] Preprocessing complete."
echo ""

# --- Step 2: Training ---
echo "[Step 2/2] Starting training..."
echo "  Batch size:        16"
echo "  Max epochs:        3000"
echo "  Precision:         32"
echo "  Checkpoint every:  1 epoch"
echo ""

python3 -m piper_train \
    --dataset-dir "$DATASET_DIR/training" \
    --accelerator gpu \
    --devices 1 \
    --batch-size 16 \
    --validation-split 0.05 \
    --num-test-examples 0 \
    --max_epochs 3000 \
    --precision 32 \
    --checkpoint-epochs 1 \
    --resume_from_checkpoint "$CHECKPOINT" \
    --quality medium

echo ""
echo "=========================================="
echo "  Training complete!"
echo "=========================================="
echo ""
echo "Checkpoints saved in: $DATASET_DIR/training/lightning_logs/"
echo ""
echo "Next step: export the best checkpoint to ONNX with export_model.sh"
