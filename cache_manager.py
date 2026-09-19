import os
import time
import shutil
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from config import CACHE_DIR, MAX_CACHE_SIZE_GB

logger = logging.getLogger("GameOverAPI.Cache")

# Active download locks to prevent duplicate concurrent downloads of the same file
_active_locks: Dict[str, asyncio.Lock] = {}
_global_lock = asyncio.Lock()


def get_cache_filename(media_id: str, media_type: str, ext: Optional[str] = None) -> str:
    """
    Generate file name following user specification:
    audio -> audio_{id}.opus (default) or .mp3, .m4a
    video -> video_{id}.mp4
    """
    clean_id = "".join(c for c in media_id if c.isalnum() or c in ("-", "_"))
    if media_type.lower() == "video":
        extension = ext if ext else "mp4"
        return f"video_{clean_id}.{extension}"
    else:
        extension = ext if ext else "opus"
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


def find_cached_video(media_id: str) -> Optional[str]:
    """
    Checks if ANY video file already exists in local cache for this ID.
    Reuses existing 480p video even if 720p/1080p is requested, avoiding re-downloads!
    """
    clean_id = "".join(c for c in media_id if c.isalnum() or c in ("-", "_"))
    for ext in ("mp4", "mkv", "webm"):
        fname = f"video_{clean_id}.{ext}"
        if is_cached(fname):
            return fname
    return None


def find_cached_audio(media_id: str, preferred_format: str = "opus") -> Optional[str]:
    """
    Checks if audio already exists in local cache for this ID.
    Prioritizes requested format (opus, mp3, m4a).
    """
    clean_id = "".join(c for c in media_id if c.isalnum() or c in ("-", "_"))
    pref_file = f"audio_{clean_id}.{preferred_format.lower()}"
    if is_cached(pref_file):
        return pref_file
    for ext in ("opus", "m4a", "mp3", "flac", "wav"):
        fname = f"audio_{clean_id}.{ext}"
        if is_cached(fname):
            return fname
    return None


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
        "max_threshold_gb": MAX_CACHE_SIZE_GB,
    }


_last_limit_alert_time: float = 0.0


async def notify_cache_threshold_alert(current_gb: float, max_gb: float, file_count: int):
    """Sends a Telegram alert to the owner when cache storage hits the 50GB threshold."""
    global _last_limit_alert_time
    now = time.time()
    # Alert at most once every 12 hours to prevent chat spam
    if now - _last_limit_alert_time < 43200:
        return
    _last_limit_alert_time = now

    try:
        from telegram_bot import send_msg, OWNER_ID
        stats = get_cache_stats()
        alert_msg = (
            f"⚠️ <b>NVMe Cᴀᴄʜᴇ Sᴛᴏʀᴀɢᴇ Tʜʀᴇsʜᴏʟᴅ Aʟᴇʀᴛ!</b>\n\n"
            f"📦 <b>Cᴜʀʀᴇɴᴛ Cᴀᴄʜᴇ:</b> <code>{current_gb:.2f} GB / {max_gb:.1f} GB</code>\n"
            f"🎵 <b>Tᴏᴛᴀʟ Fɪʟᴇs:</b> <code>{file_count}</code>\n"
            f"💽 <b>NVMe Fʀᴇᴇ:</b> <code>{stats.get('disk_free_gb', 0)} GB / {stats.get('disk_total_gb', 0)} GB</code>\n\n"
            f"ℹ️ <i>Aᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ ɪs ᴅɪsᴀʙʟᴇᴅ ᴘᴇʀ ʏᴏᴜʀ sᴇᴛᴛɪɴɢs. Sᴏɴɢs ᴡɪʟʟ sᴛᴀʏ sᴀᴠᴇᴅ ᴘᴇʀᴍᴀɴᴇɴᴛʟʏ. Yᴏᴜ ᴄᴀɴ ᴍᴀɴᴜᴀʟʟʏ ᴄʟᴇᴀʀ ᴄᴀᴄʜᴇ ɪғ ɴᴇᴇᴅᴇᴅ ᴠɪᴀ ʙᴏᴛ.</i>"
        )
        await send_msg(OWNER_ID, alert_msg)
    except Exception as e:
        logger.warning(f"Failed to send cache alert to owner: {e}")


def check_cache_threshold_sync() -> Dict[str, Any]:
    """Scans cache size without deleting any files."""
    total_bytes = 0
    file_count = 0
    for entry in CACHE_DIR.iterdir():
        if entry.is_file():
            try:
                total_bytes += entry.stat().st_size
                file_count += 1
            except Exception:
                pass
    current_gb = round(total_bytes / (1024 ** 3), 2)
    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "current_gb": current_gb,
        "exceeded": current_gb >= MAX_CACHE_SIZE_GB,
    }


def prune_old_cache():
    """
    Permanent Storage Policy:
    NO automatic 7-day deletion. Songs remain cached permanently.
    Logs a warning if storage hits MAX_CACHE_SIZE_GB (50 GB).
    """
    res = check_cache_threshold_sync()
    if res["exceeded"]:
        logger.warning(f"[Cache Storage Alert] NVMe cache ({res['current_gb']} GB) reached {MAX_CACHE_SIZE_GB} GB threshold!")


async def cache_cleaner_task():
    """Periodic background monitor running every 1 hour (Monitoring & Alerts only, NO auto-deletion)"""
    while True:
        try:
            await asyncio.sleep(3600)
            res = await asyncio.to_thread(check_cache_threshold_sync)
            if res["exceeded"]:
                await notify_cache_threshold_alert(res["current_gb"], MAX_CACHE_SIZE_GB, res["file_count"])
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Cache monitor worker error: {e}")


def manual_clear_cache(keep_latest_gb: float = 30.0) -> Dict[str, Any]:
    """
    Manual cache pruner executed ONLY when user explicitly commands it.
    Deletes oldest files first until size drops to keep_latest_gb.
    """
    now = time.time()
    files = []
    total_bytes = 0
    deleted_count = 0
    freed_bytes = 0

    for entry in CACHE_DIR.iterdir():
        if entry.is_file():
            try:
                stat = entry.stat()
                files.append((entry, stat.st_size, stat.st_mtime))
                total_bytes += stat.st_size
            except Exception:
                pass

    target_bytes = keep_latest_gb * (1024 ** 3)
    if total_bytes > target_bytes:
        files.sort(key=lambda x: x[2])  # Sort by modification time ascending (oldest first)
        for entry, size, _ in files:
            try:
                entry.unlink(missing_ok=True)
                total_bytes -= size
                freed_bytes += size
                deleted_count += 1
                if total_bytes <= target_bytes:
                    break
            except Exception as e:
                logger.warning(f"Failed to delete {entry}: {e}")

    return {
        "deleted_count": deleted_count,
        "freed_mb": round(freed_bytes / (1024 ** 2), 2),
        "freed_gb": round(freed_bytes / (1024 ** 3), 2),
        "remaining_gb": round(total_bytes / (1024 ** 3), 2),
    }
