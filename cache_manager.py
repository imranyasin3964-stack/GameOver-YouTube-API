import os
import time
import shutil
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from config import CACHE_DIR, MAX_CACHE_SIZE_GB, CACHE_TTL_HOURS

logger = logging.getLogger("GameOverAPI.Cache")

# Active download locks to prevent duplicate concurrent downloads of the same file
_active_locks: Dict[str, asyncio.Lock] = {}
_global_lock = asyncio.Lock()


def get_cache_filename(media_id: str, media_type: str, ext: Optional[str] = None) -> str:
    """
    Generate file name following user specification:
    audio -> audio_{id}.mp3 (or given ext)
    video -> video_{id}.mp4
    """
    clean_id = "".join(c for c in media_id if c.isalnum() or c in ("-", "_"))
    if media_type.lower() == "video":
        extension = ext if ext else "mp4"
        return f"video_{clean_id}.{extension}"
    else:
        extension = ext if ext else "mp3"
        return f"audio_{clean_id}.{extension}"


def get_cache_path(filename: str) -> Path:
    return CACHE_DIR / filename


def is_cached(filename: str) -> bool:
    """Check if file exists and has non-zero size"""
    filepath = get_cache_path(filename)
    try:
        return filepath.is_file() and filepath.stat().st_size > 1024  # At least 1KB
    except Exception:
        return False


async def get_download_lock(filename: str) -> asyncio.Lock:
    """Retrieve or create an async lock for a specific filename to prevent stampedes"""
    async with _global_lock:
        if filename not in _active_locks:
            _active_locks[filename] = asyncio.Lock()
        return _active_locks[filename]


def get_cache_stats() -> Dict[str, Any]:
    """Calculate cache usage and system disk stats"""
    total_bytes = 0
    file_count = 0
    now = time.time()
    oldest_age_hours = 0.0

    try:
        for entry in CACHE_DIR.iterdir():
            if entry.is_file():
                try:
                    stat = entry.stat()
                    total_bytes += stat.st_size
                    file_count += 1
                    age_hours = (now - stat.st_mtime) / 3600.0
                    if age_hours > oldest_age_hours:
                        oldest_age_hours = age_hours
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Error scanning cache dir: {e}")

    # NVMe disk stats
    try:
        disk = shutil.disk_usage(str(CACHE_DIR))
        disk_total_gb = round(disk.total / (1024 ** 3), 2)
        disk_used_gb = round(disk.used / (1024 ** 3), 2)
        disk_free_gb = round(disk.free / (1024 ** 3), 2)
    except Exception:
        disk_total_gb = disk_used_gb = disk_free_gb = 0.0

    return {
        "cached_files_count": file_count,
        "cache_size_mb": round(total_bytes / (1024 ** 2), 2),
        "cache_size_gb": round(total_bytes / (1024 ** 3), 3),
        "oldest_file_age_hours": round(oldest_age_hours, 1),
        "disk_total_gb": disk_total_gb,
        "disk_used_gb": disk_used_gb,
        "disk_free_gb": disk_free_gb,
    }


def prune_old_cache():
    """
    Removes files older than CACHE_TTL_HOURS or prunes oldest files
    if total cache size exceeds MAX_CACHE_SIZE_GB.
    """
    now = time.time()
    ttl_seconds = CACHE_TTL_HOURS * 3600
    max_bytes = MAX_CACHE_SIZE_GB * (1024 ** 3)

    files = []
    total_bytes = 0

    for entry in CACHE_DIR.iterdir():
        if entry.is_file():
            try:
                stat = entry.stat()
                age = now - stat.st_mtime
                if age > ttl_seconds:
                    entry.unlink(missing_ok=True)
                    logger.info(f"Cleaned expired cache file: {entry.name}")
                else:
                    files.append((entry, stat.st_size, stat.st_mtime))
                    total_bytes += stat.st_size
            except Exception as e:
                logger.warning(f"Failed to inspect/delete cache file {entry}: {e}")

    # If cache still exceeds max size, delete oldest files first
    if total_bytes > max_bytes:
        files.sort(key=lambda x: x[2])  # Sort by modification time ascending (oldest first)
        for entry, size, _ in files:
            try:
                entry.unlink(missing_ok=True)
                total_bytes -= size
                logger.info(f"Pruned cache to free space: {entry.name}")
                if total_bytes <= max_bytes * 0.8:  # Prune down to 80%
                    break
            except Exception as e:
                logger.warning(f"Failed to prune {entry}: {e}")


async def cache_cleaner_task():
    """Periodic background worker running every 1 hour"""
    while True:
        try:
            await asyncio.sleep(3600)
            await asyncio.to_thread(prune_old_cache)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Cache cleaner error: {e}")
