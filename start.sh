#!/usr/bin/env bash
# Simple script to run GameOver YouTube API locally or in a screen session
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

if [ -d "venv" ]; then
    source venv/bin/activate
fi

export API_HOST=0.0.0.0
export API_PORT=3001
python3 main.py
