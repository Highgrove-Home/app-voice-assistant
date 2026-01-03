#!/bin/bash
set -e

echo "🚀 Deploying Voice Assistant..."

# Navigate to the project directory
cd /home/zammitjames/app-voice-assistant

# Pull latest changes
echo "📥 Pulling latest changes from GitHub..."
git pull origin main

# Install/update dependencies
echo "📦 Installing dependencies..."
uv sync

# Restart the systemd service
echo "♻️  Restarting voice-assistant service..."
sudo systemctl restart voice-assistant.service

# Check status
echo "✅ Deployment complete! Checking service status..."
sudo systemctl status voice-assistant.service --no-pager

echo "✨ Voice Assistant deployed successfully!"
