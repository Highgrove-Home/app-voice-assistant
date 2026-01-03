#!/bin/bash
set -e

echo "🚀 Deploying Voice Assistant..."

# Get the directory where the script is located (GitHub Actions working directory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "📂 Working directory: $SCRIPT_DIR"

# Install/update dependencies
echo "📦 Installing dependencies..."
cd "$SCRIPT_DIR"
uv sync

# Install systemd service if it doesn't exist
if ! systemctl is-enabled voice-assistant.service &> /dev/null; then
    echo "📝 Installing systemd service..."
    sudo cp voice-assistant.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable voice-assistant.service
    echo "✅ Service installed and enabled"
fi

# Restart the systemd service
echo "♻️  Restarting voice-assistant service..."
sudo systemctl restart voice-assistant.service

# Check status
echo "✅ Deployment complete! Checking service status..."
sudo systemctl status voice-assistant.service --no-pager

echo "✨ Voice Assistant deployed successfully!"
