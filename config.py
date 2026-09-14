import os
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Server configuration
HOST = os.getenv("API_HOST", "0.0.0.0")
PORT = int(os.getenv("API_PORT", "3001"))
BASE_URL = os.getenv("BASE_URL", "http://172.104.38.31:3001")

# Cache & Storage limits
MAX_CACHE_SIZE_GB = float(os.getenv("MAX_CACHE_SIZE_GB", "15.0"))  # Auto-cleanup if exceeds 15GB
CACHE_TTL_HOURS = int(os.getenv("CACHE_TTL_HOURS", "24"))          # Remove files older than 24 hours

# Downloader / yt-dlp Settings
DEFAULT_TIMEOUT_SEC = 25
CONCURRENT_DOWNLOADS = 5
