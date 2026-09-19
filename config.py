import os
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Server configuration
HOST = os.getenv("API_HOST", "0.0.0.0")
PORT = int(os.getenv("API_PORT", "80"))
BASE_URL = os.getenv("BASE_URL", "http://172.104.38.31")

# Cache & Storage limits (50 GB Capacity, Permanent Storage - No Auto-Delete)
MAX_CACHE_SIZE_GB = float(os.getenv("MAX_CACHE_SIZE_GB", "50.0"))  # Alert threshold at 50GB

# Downloader & Audio Settings
DEFAULT_TIMEOUT_SEC = 25
CONCURRENT_DOWNLOADS = 5
DEFAULT_AUDIO_FORMAT = "opus"  # Rank #1 Native Telegram 48kHz Studio HD
SUPPORTED_AUDIO_FORMATS = ("opus", "mp3", "m4a", "flac", "wav")
