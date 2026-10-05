import os
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).resolve().parent

# Hugging Face Persistent Storage Bucket (/data)
# Automatically detect if /data is mounted and writable
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
if DATA_DIR.exists() and os.access(DATA_DIR, os.W_OK):
    CACHE_DIR = DATA_DIR / "cache"
    DB_PATH = DATA_DIR / "controller.sqlite3"
else:
    CACHE_DIR = BASE_DIR / "cache"
    DB_PATH = BASE_DIR / "controller.sqlite3"

CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Server configuration (Hugging Face Spaces default port is 7860)
HOST = os.getenv("API_HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", os.getenv("API_PORT", "7860")))
BASE_URL = os.getenv("BASE_URL", "https://youtubeapiv2.gameoverhostingservice.workers.dev")
CF_WORKER_URL = os.getenv("CF_WORKER_URL", "https://youtubeapiv2.gameoverhostingservice.workers.dev")

# Cache & Storage limits (50 GB Capacity, Permanent Storage - No Auto-Delete)
MAX_CACHE_SIZE_GB = float(os.getenv("MAX_CACHE_SIZE_GB", "50.0"))

# Downloader & Audio Settings
DEFAULT_TIMEOUT_SEC = 25
CONCURRENT_DOWNLOADS = 5
DEFAULT_AUDIO_FORMAT = "opus"  # Rank #1 Native Telegram 48kHz Studio HD
SUPPORTED_AUDIO_FORMATS = ("opus", "mp3", "m4a", "flac", "wav")

