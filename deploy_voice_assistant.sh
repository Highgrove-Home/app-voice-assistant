#!/bin/bash
set -e

echo "🚀 Deploying Voice Assistant..."

# Permanent deployment directory
DEPLOY_DIR="/home/zammitjames/app-voice-assistant"
OLD_DEPLOY_DIR="/home/zammitjames/voice-assistant"

# Remove old deployment directory if it exists (migration)
if [ -d "$OLD_DEPLOY_DIR" ] && [ "$OLD_DEPLOY_DIR" != "$DEPLOY_DIR" ]; then
    echo "🗑️  Removing old deployment directory..."
    rm -rf "$OLD_DEPLOY_DIR"
fi

# Clone or pull latest code
if [ -d "$DEPLOY_DIR" ]; then
    echo "📥 Pulling latest changes from GitHub..."
    cd "$DEPLOY_DIR"
    git fetch origin
    git reset --hard origin/main
else
    echo "📥 Cloning repository..."
    git clone https://github.com/Highgrove-Home/app-voice-assistant.git "$DEPLOY_DIR"
    cd "$DEPLOY_DIR"
fi

echo "📂 Working directory: $DEPLOY_DIR"

# Install/update dependencies with Python 3.11 (required for tflite-runtime)
echo "📦 Installing dependencies with Python 3.11..."
uv python install 3.11
uv python pin 3.11
uv sync

# Download OpenWakeWord models
echo "📥 Downloading wake word models..."
uv run python -c "
from openwakeword.utils import download_models
import os

# Download alexa model
try:
    print('Downloading alexa model...')
    download_models(['alexa'])
    print('✅ Models downloaded successfully')
except Exception as e:
    print(f'⚠️  Model download failed: {e}')
    print('Models will be downloaded on first run')
" || echo "⚠️  Model download will happen on first run"

# Always update systemd service file to ensure correct path
echo "📝 Updating systemd service..."
sudo cp voice-assistant.service /etc/systemd/system/
sudo systemctl daemon-reload

# Enable service if not already enabled
if ! systemctl is-enabled voice-assistant.service &> /dev/null; then
    sudo systemctl enable voice-assistant.service
    echo "✅ Service enabled"
fi

# Force kill any running instance and restart
echo "🛑 Stopping voice-assistant service..."
sudo systemctl kill -s SIGKILL voice-assistant.service 2>/dev/null || true

echo "♻️  Reloading systemd and starting service..."
sudo systemctl daemon-reload
sudo systemctl start voice-assistant.service

# Wait a moment for service to start
sleep 2

# Check status
echo "✅ Deployment complete! Checking service status..."
sudo systemctl status voice-assistant.service --no-pager || true

echo "✨ Voice Assistant deployed successfully!"
