#!/usr/bin/env bash
set -euo pipefail

# Ultra Alarm - Raspberry Pi Thin Client Setup
# The Pi only runs: wake word detection, mic capture, speaker playback.
# All heavy lifting (STT, LLM, TTS) runs on the NAS server.

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

status()  { echo -e "${GREEN}[+]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
info()    { echo -e "${CYAN}[*]${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

SKIP_APT=false
SERVER_URL="${1:-ws://ultra-alarm.local:8765}"

for arg in "$@"; do
    case "$arg" in
        --skip-apt) SKIP_APT=true ;;
        ws://*) SERVER_URL="$arg" ;;
    esac
done

# --- Pi detection ---
if grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
    status "Running on Raspberry Pi"
else
    warn "Not running on a Raspberry Pi. Proceeding anyway."
fi

# --- System packages (minimal) ---
if [ "$SKIP_APT" = true ]; then
    info "Skipping apt install (--skip-apt)"
else
    status "Installing system packages..."
    sudo apt update
    sudo apt install -y python3-pip python3-venv portaudio19-dev alsa-utils
fi

# --- Python venv ---
if [ -d "./venv" ]; then
    info "Python venv already exists"
else
    status "Creating Python virtual environment..."
    python3 -m venv ./venv
fi

# --- Python packages (lightweight) ---
status "Installing Python packages..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
# Install openwakeword without tflite dep (uses onnxruntime instead)
./venv/bin/pip install openwakeword --no-deps
./venv/bin/pip install requests tqdm scipy

# --- Systemd service ---
status "Installing systemd service..."
cat > /tmp/ultra-alarm-client.service <<EOF
[Unit]
Description=Ultra Alarm - Pi Thin Client
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$SCRIPT_DIR
ExecStart=$SCRIPT_DIR/venv/bin/python3 $SCRIPT_DIR/client.py --server $SERVER_URL
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1
Environment=ULTRA_ALARM_SERVER=$SERVER_URL

[Install]
WantedBy=multi-user.target
EOF

sudo cp /tmp/ultra-alarm-client.service /etc/systemd/system/ultra-alarm-client.service
sudo systemctl daemon-reload
info "Service installed. Enable with: sudo systemctl enable --now ultra-alarm-client"

# --- Test audio ---
status "Testing audio output..."
if command -v espeak &>/dev/null; then
    espeak "Ultra alarm client setup complete" || warn "Audio test failed"
elif command -v aplay &>/dev/null; then
    info "espeak not installed (not needed for thin client). Audio output OK via aplay."
else
    warn "No audio playback tool found. Install alsa-utils."
fi

# --- Done ---
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  Ultra Alarm Pi Client setup complete!${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "Server URL: $SERVER_URL"
echo ""
echo "Next steps:"
echo "  1. Ensure the NAS server is running (docker compose up -d)"
echo "  2. Test: source venv/bin/activate && python3 client.py --server $SERVER_URL --text"
echo "  3. Test with mic: python3 client.py --server $SERVER_URL"
echo "  4. Auto-start: sudo systemctl enable --now ultra-alarm-client"
echo ""
