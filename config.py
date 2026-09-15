import os
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Server configuration
HOST = os.getenv("API_HOST", "0.0.0.0")
PORT = int(os.getenv("API_PORT", "80"))
BASE_URL = os.getenv("BASE_URL", "http://139.162.177.184:80")

# Cache & Storage limits (7 Days Rolling Retention)
MAX_CACHE_SIZE_GB = float(os.getenv("MAX_CACHE_SIZE_GB", "20.0"))  # Auto-cleanup oldest files if exceeds 20GB
CACHE_TTL_HOURS = int(os.getenv("CACHE_TTL_HOURS", str(7 * 24)))    # Keep each song for 7 days (168 hours)

# Downloader / yt-dlp Settings
DEFAULT_TIMEOUT_SEC = 25
CONCURRENT_DOWNLOADS = 5
