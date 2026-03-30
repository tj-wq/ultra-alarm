#!/usr/bin/env bash
#
# setup_textymcspeechy.sh - Set up TextyMcSpeechy voice training pipeline
#
# Clones TextyMcSpeechy, installs Applio for RVC voice conversion,
# and installs Piper training dependencies. Requires Python 3.10+ and GPU.
#
# Usage: ./setup_textymcspeechy.sh [install_dir]
#
# Arguments:
#   install_dir - Optional: directory to install into (default: ./textymcspeechy)
#

set -euo pipefail

INSTALL_DIR="${1:-./textymcspeechy}"
INSTALL_DIR="$(realpath "$INSTALL_DIR" 2>/dev/null || echo "$INSTALL_DIR")"

echo "=========================================="
echo "  TextyMcSpeechy Pipeline Setup"
echo "=========================================="
echo "Install directory: $INSTALL_DIR"
echo ""

# --- Step 1: Check Python version ---
echo "[Step 1/6] Checking Python version..."

PYTHON_VERSION=$(python3 --version 2>/dev/null | grep -oP '\d+\.\d+' | head -1)
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [[ -z "$PYTHON_VERSION" ]]; then
    echo "Error: Python 3 not found." >&2
    exit 1
fi

if [[ "$PYTHON_MAJOR" -lt 3 ]] || { [[ "$PYTHON_MAJOR" -eq 3 ]] && [[ "$PYTHON_MINOR" -lt 10 ]]; }; then
    echo "Error: Python 3.10+ required, found $PYTHON_VERSION" >&2
    exit 1
fi

echo "  Python $PYTHON_VERSION OK."
echo ""

# --- Step 2: Check GPU availability ---
echo "[Step 2/6] Checking GPU availability..."

if command -v rocminfo &>/dev/null; then
    GPU_INFO=$(rocminfo 2>/dev/null | grep -i "marketing name" | head -1 || true)
    if [[ -n "$GPU_INFO" ]]; then
        echo "  ROCm GPU detected: $GPU_INFO"
    else
        echo "  ROCm installed but no GPU detected. Continuing anyway..."
    fi
    export HSA_OVERRIDE_GFX_VERSION=12.0.1
    echo "  Set HSA_OVERRIDE_GFX_VERSION=12.0.1"
elif command -v nvidia-smi &>/dev/null; then
    echo "  NVIDIA GPU detected:"
    nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true
else
    echo "  Warning: No GPU detected. Training will be very slow on CPU." >&2
fi
echo ""

# --- Step 3: Clone TextyMcSpeechy ---
echo "[Step 3/6] Setting up TextyMcSpeechy..."

mkdir -p "$INSTALL_DIR"

if [[ -d "$INSTALL_DIR/TextyMcSpeechy" ]]; then
    echo "  TextyMcSpeechy already cloned, pulling latest..."
    cd "$INSTALL_DIR/TextyMcSpeechy" && git pull && cd -
else
    echo "  Cloning TextyMcSpeechy..."
    git clone https://github.com/domesticatedviking/TextyMcSpeechy "$INSTALL_DIR/TextyMcSpeechy"
fi

echo "  TextyMcSpeechy ready."
echo ""

# --- Step 4: Install Applio for RVC voice conversion ---
echo "[Step 4/6] Setting up Applio (RVC voice conversion)..."

if [[ -d "$INSTALL_DIR/Applio" ]]; then
    echo "  Applio already cloned, pulling latest..."
    cd "$INSTALL_DIR/Applio" && git pull && cd -
else
    echo "  Cloning Applio..."
    git clone https://github.com/IAHispano/Applio "$INSTALL_DIR/Applio"
fi

echo "  Installing Applio dependencies..."
cd "$INSTALL_DIR/Applio"

if [[ -f "requirements.txt" ]]; then
    pip3 install -r requirements.txt
else
    echo "  Warning: requirements.txt not found in Applio, skipping pip install." >&2
fi

cd -

echo "  Applio ready."
echo ""

# --- Step 5: Install Piper training dependencies ---
echo "[Step 5/6] Installing Piper training dependencies..."

if [[ -d "$INSTALL_DIR/TextyMcSpeechy" ]]; then
    # TextyMcSpeechy bundles Piper training setup; use it if available
    if [[ -f "$INSTALL_DIR/TextyMcSpeechy/requirements.txt" ]]; then
        pip3 install -r "$INSTALL_DIR/TextyMcSpeechy/requirements.txt"
    fi
fi

# Ensure piper-phonemize and piper-train core deps are present
pip3 install piper-phonemize pydub

echo "  Piper training dependencies installed."
echo ""

# --- Step 6: Verify setup ---
echo "[Step 6/6] Verifying installation..."

CHECKS_PASSED=0
CHECKS_TOTAL=3

if [[ -d "$INSTALL_DIR/TextyMcSpeechy" ]]; then
    echo "  [OK] TextyMcSpeechy cloned"
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
else
    echo "  [FAIL] TextyMcSpeechy not found"
fi

if [[ -d "$INSTALL_DIR/Applio" ]]; then
    echo "  [OK] Applio cloned"
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
else
    echo "  [FAIL] Applio not found"
fi

if python3 -c "import pydub" 2>/dev/null; then
    echo "  [OK] pydub importable"
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
else
    echo "  [FAIL] pydub not importable"
fi

echo ""
echo "=========================================="
echo "  Setup complete! ($CHECKS_PASSED/$CHECKS_TOTAL checks passed)"
echo "=========================================="
echo ""
echo "Installed to: $INSTALL_DIR"
echo ""
echo "Next steps:"
echo "  1. Prepare Rocky audio clips with prepare_dataset.py"
echo "  2. Train RVC model:  ./train_rvc.sh <rocky_clips_dir> <output_model_dir>"
echo "  3. Convert dataset:  ./convert_dataset.sh <lessac_dir> <rvc_model> <output_dir>"
echo "  4. Fine-tune Piper:  ./train_piper.sh <converted_dataset> <checkpoint>"
echo ""
