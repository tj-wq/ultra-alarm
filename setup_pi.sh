#!/usr/bin/env bash
set -euo pipefail

# Ultra Alarm - Raspberry Pi Setup Script

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

status()  { echo -e "${GREEN}[+]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[x]${NC} $1"; }
info()    { echo -e "${CYAN}[*]${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- Parse flags ---
SKIP_APT=false
for arg in "$@"; do
    case "$arg" in
        --skip-apt) SKIP_APT=true ;;
        *) warn "Unknown flag: $arg" ;;
    esac
done

# --- Pi detection ---
if grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
    status "Running on Raspberry Pi"
else
    warn "Not running on a Raspberry Pi. Proceeding anyway."
fi

# --- Step 1: System packages ---
if [ "$SKIP_APT" = true ]; then
    info "Skipping apt install (--skip-apt)"
else
    status "Installing system packages..."
    sudo apt update
    sudo apt install -y python3-pip python3-venv portaudio19-dev espeak alsa-utils at ffmpeg sox
fi

# --- Step 2: Create directories ---
status "Creating directories..."
mkdir -p models sounds

# --- Step 3: Python venv ---
if [ -d "./venv" ]; then
    info "Python venv already exists, skipping creation"
else
    status "Creating Python virtual environment..."
    python3 -m venv ./venv
fi

# --- Step 4: Install Python packages ---
status "Installing Python packages..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install httpx icalendar PyAudio anthropic python-dotenv openwakeword numpy

# --- Step 5: Install whisper.cpp (lightweight STT for Pi Zero 2 / Pi 4) ---
WHISPER_CPP_DIR="./whisper.cpp"
if [ -x "$WHISPER_CPP_DIR/main" ] || [ -x "$WHISPER_CPP_DIR/build/bin/whisper-cli" ]; then
    info "whisper.cpp already built, skipping"
else
    status "Building whisper.cpp (this may take a few minutes on Pi)..."
    if [ -d "$WHISPER_CPP_DIR" ]; then
        info "whisper.cpp directory exists, pulling latest"
        cd "$WHISPER_CPP_DIR" && git pull && cd "$SCRIPT_DIR"
    else
        git clone https://github.com/ggerganov/whisper.cpp "$WHISPER_CPP_DIR"
    fi
    cd "$WHISPER_CPP_DIR"
    make -j"$(nproc)" 2>&1 | tail -3
    cd "$SCRIPT_DIR"
    if [ -x "$WHISPER_CPP_DIR/main" ]; then
        status "whisper.cpp built successfully"
    else
        warn "whisper.cpp build may have failed. Check $WHISPER_CPP_DIR/"
    fi
fi

# Download whisper model (tiny.en for Pi Zero 2, base.en for Pi 4+)
WHISPER_MODEL="tiny.en"
if [ -f "$WHISPER_CPP_DIR/models/ggml-${WHISPER_MODEL}.bin" ]; then
    info "whisper model ggml-${WHISPER_MODEL}.bin already downloaded"
else
    status "Downloading whisper model: ${WHISPER_MODEL}..."
    bash "$WHISPER_CPP_DIR/models/download-ggml-model.sh" "$WHISPER_MODEL"
    status "Whisper model downloaded"
fi

# --- Step 6: Install Piper TTS ---
ARCH="$(uname -m)"
status "Detected architecture: $ARCH"

PIPER_DIR="./piper"
if [ -x "$PIPER_DIR/piper" ]; then
    info "Piper binary already installed, skipping"
else
    status "Installing Piper TTS..."
    case "$ARCH" in
        aarch64) PIPER_ARCH="aarch64" ;;
        armv7l)  PIPER_ARCH="armv7l" ;;
        x86_64)  PIPER_ARCH="amd64" ;;
        *)       error "Unsupported architecture: $ARCH"; exit 1 ;;
    esac

    PIPER_VERSION="2023.11.14-2"
    PIPER_URL="https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_linux_${PIPER_ARCH}.tar.gz"

    info "Downloading Piper from $PIPER_URL"
    curl -L "$PIPER_URL" -o /tmp/piper.tar.gz
    tar -xzf /tmp/piper.tar.gz -C .
    rm /tmp/piper.tar.gz
    status "Piper installed to $PIPER_DIR/"
fi

# --- Step 7: Download default voice model ---
VOICE_MODEL="models/en_US-lessac-medium.onnx"
VOICE_CONFIG="models/en_US-lessac-medium.onnx.json"

if [ -f "$VOICE_MODEL" ] && [ -f "$VOICE_CONFIG" ]; then
    info "Default voice model already downloaded, skipping"
else
    status "Downloading default Piper voice model (en_US-lessac-medium)..."
    VOICE_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium"
    curl -L "${VOICE_BASE}/en_US-lessac-medium.onnx" -o "$VOICE_MODEL"
    curl -L "${VOICE_BASE}/en_US-lessac-medium.onnx.json" -o "$VOICE_CONFIG"
    status "Voice model downloaded to models/"
fi

# --- Step 8: Enable atd service ---
status "Enabling atd service..."
sudo systemctl enable --now atd

# --- Step 9: Generate default config ---
if [ -f "config.json" ]; then
    info "config.json already exists, skipping init-config"
else
    status "Generating default config.json..."
    ./venv/bin/python3 alarm_clock.py init-config
fi

# --- Step 10: Install systemd service ---
status "Installing systemd service..."
if [ -f "ultra-alarm.service" ]; then
    sudo cp ultra-alarm.service /etc/systemd/system/ultra-alarm.service
    sudo systemctl daemon-reload
    info "Service installed. Enable with: sudo systemctl enable --now ultra-alarm"
else
    warn "ultra-alarm.service not found, skipping"
fi

# --- Step 11: Test audio ---
status "Testing audio output..."
espeak "Ultra alarm setup complete" || warn "Audio test failed. Check your audio output settings."

# --- Done ---
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  Ultra Alarm setup complete!${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "Next steps:"
echo "  1. Create .env file with ANTHROPIC_API_KEY and MCP_AUTH_TOKEN"
echo "  2. Test with: source venv/bin/activate && python3 alarm_clock.py preview"
echo "  3. Test audio: python3 alarm_clock.py alarm"
echo "  4. Test voice: python3 coach.py morning --text"
echo "  5. Run the listener: python3 listener.py"
echo "     (always-on: wake word + alarm scheduler, say 'hey jarvis' to talk)"
echo "  6. Auto-start on boot: sudo systemctl enable --now ultra-alarm"
echo ""
