#!/bin/bash
set -e

# Generate default config.json if none exists on the data volume
if [ ! -f /data/config.json ]; then
    echo "[entrypoint] No config.json found in /data — generating defaults"
    python3 -c "from config import init_config; init_config('/data/config.json')"
fi

# Symlink data volume config into /app where server.py expects it
ln -sf /data/config.json /app/config.json

# Load .env from data volume if present
if [ -f /data/.env ]; then
    set -a
    source /data/.env
    set +a
fi

exec python3 server.py
