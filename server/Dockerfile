FROM nvidia/cuda:12.2.2-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    espeak sox libsox-fmt-all \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Piper TTS
ARG PIPER_VERSION=2023.11.14-2
RUN mkdir -p /opt/piper && \
    curl -L "https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_linux_amd64.tar.gz" \
    | tar -xz -C /opt && \
    chmod +x /opt/piper/piper

# Download default voice model
RUN mkdir -p /opt/piper/models && \
    curl -L "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx" \
    -o /opt/piper/models/en_US-lessac-medium.onnx && \
    curl -L "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json" \
    -o /opt/piper/models/en_US-lessac-medium.onnx.json

WORKDIR /app

# Install Python dependencies
COPY server/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy server code
COPY config.py ical_parser.py /app/
COPY server/protocol.py server/stt.py server/tts.py server/server.py /app/
COPY server/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# /data is the persistent volume — config.json, .env, and cache live here
VOLUME /data

# Environment defaults
ENV PIPER_BINARY=/opt/piper/piper
ENV PIPER_MODEL=/opt/piper/models/en_US-lessac-medium.onnx
ENV HOST=0.0.0.0
ENV PORT=8765
ENV WHISPER_MODEL=large-v3
ENV WHISPER_DEVICE=cuda
ENV CONFIG_PATH=/data/config.json

EXPOSE 8765

ENTRYPOINT ["/app/entrypoint.sh"]
