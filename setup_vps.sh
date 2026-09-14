#!/usr/bin/env bash
# ==============================================================================
# GameOver YouTube API - One-Command VPS Setup & Installer (Ubuntu 22.04 / 24.04)
# ==============================================================================
set -e

echo "=========================================================="
echo "🚀 Setting up GameOver YouTube API on Port 3001..."
echo "=========================================================="

# 1. Update APT & Install Core Dependencies (Python, Pip, FFmpeg)
echo "[1/5] Installing Python3, Pip, FFmpeg, Node.js and network tools..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv ffmpeg curl ufw nodejs

# 2. Setup Directory & Python Virtual Environment
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[2/5] Setting up virtual environment in $APP_DIR..."
cd "$APP_DIR"

if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip yt-dlp
pip install -r requirements.txt
playwright install chromium || true

# 3. Create Cache Directory
mkdir -p "$APP_DIR/cache"
chmod -R 755 "$APP_DIR/cache"

# 4. Configure Systemd 24/7 Background Service
SERVICE_FILE="/etc/systemd/system/gameover-api.service"
echo "[3/5] Registering systemd service at $SERVICE_FILE..."

sudo bash -c "cat <<EOF > $SERVICE_FILE
[Unit]
Description=GameOver YouTube High-Speed Streaming API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python3 $APP_DIR/main.py
Restart=always
RestartSec=3
Environment=API_PORT=3001
Environment=API_HOST=0.0.0.0
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF"

sudo systemctl daemon-reload
sudo systemctl enable gameover-api
sudo systemctl restart gameover-api

# 5. Open Firewall Port 3001 if UFW is active
echo "[4/5] Checking firewall rules for Port 3001..."
if command -v ufw >/dev/null 2>&1; then
    sudo ufw allow 3001/tcp || true
fi

# 6. Verify Service Health
echo "[5/5] Verifying API health on localhost:3001..."
sleep 3

if curl -s http://127.0.0.1:3001/health | grep -q "healthy"; then
    echo "=========================================================="
    echo "✅ SUCCESS: GameOver YouTube API is running smoothly!"
    echo "🌐 Public API URL: http://172.104.38.31:3001"
    echo "📊 Health Check:  http://172.104.38.31:3001/health"
    echo "📖 Interactive Docs: http://172.104.38.31:3001/docs"
    echo "=========================================================="
    echo "Useful Service Commands:"
    echo "  Status:  sudo systemctl status gameover-api"
    echo "  Logs:    sudo journalctl -u gameover-api -f"
    echo "  Restart: sudo systemctl restart gameover-api"
    echo "  Stop:    sudo systemctl stop gameover-api"
    echo "=========================================================="
else
    echo "⚠️ Warning: Service started, but health check did not return immediately."
    echo "Run 'sudo journalctl -u gameover-api -e' to view startup logs."
fi
