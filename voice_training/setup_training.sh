#!/usr/bin/env bash
#
# setup_training.sh - Set up GPU training environment for Piper on AMD ROCm
#
# Verifies ROCm installation, installs PyTorch with ROCm support,
# clones the Piper repo, installs training dependencies, and downloads
# the lessac-medium pretrained checkpoint.
#
# Usage: ./setup_training.sh
#

set -euo pipefail

PIPER_REPO="https://github.com/rhasspy/piper.git"
CHECKPOINT_URL="https://huggingface.co/datasets/rhasspy/piper-checkpoints/resolve/main/en/en_US/lessac/medium/epoch%3D2164-step%3D1355540.ckpt"
CHECKPOINT_NAME="lessac-medium.ckpt"

echo "=========================================="
echo "  Piper Training Environment Setup"
echo "  Target GPU: AMD RX 9070 XT (ROCm)"
echo "=========================================="
echo ""

# --- Step 1: Verify ROCm ---
echo "[Step 1/5] Verifying ROCm installation..."

if ! command -v rocminfo &>/dev/null; then
    echo "Error: rocminfo not found. ROCm does not appear to be installed." >&2
    echo "Install ROCm: https://rocm.docs.amd.com/en/latest/deploy/linux/installer/install.html" >&2
    exit 1
fi

GPU_INFO=$(rocminfo 2>/dev/null | grep -i "marketing name" | head -1 || true)
if [[ -z "$GPU_INFO" ]]; then
    echo "Warning: Could not detect GPU via rocminfo. Continuing anyway..." >&2
else
    echo "  Detected: $GPU_INFO"
fi

AGENT_COUNT=$(rocminfo 2>/dev/null | grep -c "Agent" || true)
echo "  ROCm agents: $AGENT_COUNT"
echo "  ROCm OK."
echo ""

# --- Step 2: Install PyTorch with ROCm ---
echo "[Step 2/5] Installing PyTorch with ROCm 6.4 support..."

pip3 install torch torchaudio --index-url https://download.pytorch.org/whl/rocm6.4

echo "  PyTorch installed."
echo ""

# --- Step 3: Clone Piper and install training dependencies ---
echo "[Step 3/5] Setting up Piper training code..."

if [[ -d "piper" ]]; then
    echo "  Piper directory already exists, pulling latest..."
    cd piper && git pull && cd ..
else
    echo "  Cloning piper repository..."
    git clone "$PIPER_REPO"
fi

echo "  Installing piper-train dependencies..."
cd piper/src/python
pip3 install -e .
cd ../../..

# Install additional training dependencies
pip3 install piper-phonemize

echo "  Piper training dependencies installed."
echo ""

# --- Step 4: Download lessac-medium checkpoint ---
echo "[Step 4/5] Downloading lessac-medium pretrained checkpoint..."

if [[ -f "$CHECKPOINT_NAME" ]]; then
    echo "  Checkpoint already exists: $CHECKPOINT_NAME"
else
    echo "  Downloading from HuggingFace..."
    curl -L -o "$CHECKPOINT_NAME" "$CHECKPOINT_URL"

    if [[ -f "$CHECKPOINT_NAME" ]]; then
        FILE_SIZE=$(du -h "$CHECKPOINT_NAME" | cut -f1)
        echo "  Downloaded: $CHECKPOINT_NAME ($FILE_SIZE)"
    else
        echo "  Error: Download failed." >&2
        exit 1
    fi
fi
echo ""

# --- Step 5: Verify PyTorch GPU access ---
echo "[Step 5/5] Verifying PyTorch can see the GPU..."

export HSA_OVERRIDE_GFX_VERSION=12.0.1

GPU_AVAILABLE=$(python3 -c "import torch; print(torch.cuda.is_available())" 2>/dev/null || echo "False")
if [[ "$GPU_AVAILABLE" == "True" ]]; then
    GPU_NAME=$(python3 -c "import torch; print(torch.cuda.get_device_name(0))" 2>/dev/null || echo "unknown")
    echo "  GPU available: $GPU_NAME"
    echo "  torch.cuda.is_available() = True"
else
    echo "  Warning: torch.cuda.is_available() = False" >&2
    echo "  Training will fall back to CPU, which will be very slow." >&2
    echo "  Make sure ROCm is properly installed and HSA_OVERRIDE_GFX_VERSION=12.0.1 is set." >&2
fi

echo ""
echo "=========================================="
echo "  Setup complete!"
echo "=========================================="
echo ""
echo "Environment ready. To train:"
echo "  1. Prepare your dataset (prepare_dataset.py or synthesize_dataset.sh)"
echo "  2. Run training:"
echo "     ./train.sh /path/to/dataset/ $CHECKPOINT_NAME"
echo ""
echo "Remember to set these env vars before training:"
echo "  export HSA_OVERRIDE_GFX_VERSION=12.0.1"
echo "  export GPU_MAX_ALLOC_PERCENT=100"
echo ""
