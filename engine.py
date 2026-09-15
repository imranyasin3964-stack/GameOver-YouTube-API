import os
import re
import time
import logging
import asyncio
from typing import Dict, Any, Optional
import yt_dlp

from config import CACHE_DIR, DEFAULT_TIMEOUT_SEC
from cache_manager import get_cache_filename, get_cache_path, is_cached, get_download_lock

logger = logging.getLogger("GameOverAPI.Engine")

YOUTUBE_URL_REGEX = re.compile(
    r"^(https?://)?(www\.|m\.)?(youtube\.com/(watch\?v=|embed/|v/|shorts/)|youtu\.be/)([\w-]{11})"
)
YOUTUBE_ID_REGEX = re.compile(r"^[\w-]{11}$")


def extract_youtube_id_or_query(input_query: str) -> tuple[bool, str]:
    """
    Checks if input is a YouTube URL or 11-char ID.
    Returns: (is_direct_id, id_or_query)
    """
    cleaned = input_query.strip()
    match_url = YOUTUBE_URL_REGEX.search(cleaned)
    if match_url:
        return True, match_url.group(5)
    if YOUTUBE_ID_REGEX.match(cleaned):
        return True, cleaned
    return False, cleaned


def format_duration(seconds: Optional[int]) -> str:
    if not seconds or seconds < 0:
        return "00:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def get_ydl_base_opts(use_cookies: bool = False) -> dict:
    """
    Base options for yt-dlp.
    Prioritizes 'visionos', 'ios', 'android' clients without cookies (bypasses datacenter bot blocks).
    Uses cookies only when explicitly requested as a fallback.
    """
    from config import BASE_DIR
    
    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "nocheckcertificate": True,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_args": {
            "youtube": {
                "player_client": ["visionos", "ios", "android", "tv", "web"]
            }
        },
        "js_runtimes": {
            "node": {},
            "deno": {},
        },
    }

    if use_cookies:
        cookie_file = BASE_DIR / "cookies.txt"
        if cookie_file.exists() and cookie_file.stat().st_size > 0:
            opts["cookiefile"] = str(cookie_file)
            opts["extractor_args"]["youtube"]["player_client"] = ["web", "tv"]
        
    return opts


def resolve_metadata_sync(query: str) -> Dict[str, Any]:
    """
    Quickly resolves video metadata (ID, title, thumbnail, duration)
    without downloading media files.
    """
    is_direct, target = extract_youtube_id_or_query(query)
    search_url = f"https://www.youtube.com/watch?v={target}" if is_direct else f"ytsearch1:{target}"

    opts = get_ydl_base_opts(use_cookies=False)
    opts["extract_flat"] = True

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(search_url, download=False)
    except Exception as e:
        logger.warning(f"Direct metadata extraction failed ({e}). Retrying with cookies fallback...")
        opts_cookie = get_ydl_base_opts(use_cookies=True)
        opts_cookie["extract_flat"] = True
        if "cookiefile" in opts_cookie:
            with yt_dlp.YoutubeDL(opts_cookie) as ydl:
                info = ydl.extract_info(search_url, download=False)
        else:
            raise e

    if "entries" in info:
        entries = list(info.get("entries", []))
        if not entries:
            raise ValueError("No search results found on YouTube.")
        info = entries[0]

    video_id = info.get("id")
    title = info.get("title", "Unknown Title")
    duration_sec = info.get("duration") or 0
    thumbnail = info.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    uploader = info.get("uploader") or info.get("channel", "YouTube")

    return {
        "id": video_id,
        "title": title,
        "duration": format_duration(duration_sec),
        "duration_sec": int(duration_sec),
        "thumbnail": thumbnail,
        "uploader": uploader,
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
    }


def download_media_sync(video_id: str, media_type: str = "audio", quality: Optional[str] = None) -> str:
    """
    Downloads and caches audio or video.
    Returns: filename in cache (e.g. audio_{id}.mp3 or video_{id}.mp4)
    """
    filename = get_cache_filename(video_id, media_type)
    target_path = get_cache_path(filename)
    tmp_path = get_cache_path(f"tmp_{filename}")

    if is_cached(filename):
        return filename

    opts = get_ydl_base_opts(use_cookies=False)
    url = f"https://www.youtube.com/watch?v={video_id}"

    if media_type.lower() == "video":
        height = quality if quality in ("360", "480", "720", "1080") else "480"
        opts.update({
            "format": f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best",
            "outtmpl": str(tmp_path),
            "merge_output_format": "mp4",
            "postprocessors": [{
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }],
        })
    else:
        opts.update({
            "format": "bestaudio/best",
            "outtmpl": str(tmp_path).replace(".mp3", ".%(ext)s"),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })

    # Remove stale temp files if any
    for p in CACHE_DIR.glob(f"tmp_{filename}*"):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:
        logger.warning(f"Direct download failed ({e}). Retrying with cookies fallback...")
        opts_cookie = get_ydl_base_opts(use_cookies=True)
        if "cookiefile" in opts_cookie:
            if media_type.lower() == "video":
                height = quality if quality in ("360", "480", "720", "1080") else "480"
                opts_cookie.update({
                    "format": f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best",
                    "outtmpl": str(tmp_path),
                    "merge_output_format": "mp4",
                    "postprocessors": [{
                        "key": "FFmpegVideoConvertor",
                        "preferedformat": "mp4",
                    }],
                })
            else:
                opts_cookie.update({
                    "format": "bestaudio/best",
                    "outtmpl": str(tmp_path).replace(".mp3", ".%(ext)s"),
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                })
            with yt_dlp.YoutubeDL(opts_cookie) as ydl:
                ydl.download([url])
        else:
            raise e

    # Ensure output matches target_path
    expected_audio_out = get_cache_path(f"tmp_{filename}".replace(".mp3", ".mp3"))
    if media_type.lower() == "audio" and expected_audio_out.exists():
        if expected_audio_out != tmp_path:
            expected_audio_out.replace(target_path)
        else:
            tmp_path.replace(target_path)
    elif tmp_path.exists():
        tmp_path.replace(target_path)
    else:
        # Fallback search for downloaded file
        candidates = list(CACHE_DIR.glob(f"tmp_{filename}*"))
        if candidates:
            candidates[0].replace(target_path)
        else:
            raise RuntimeError(f"Download completed but target file not found for {video_id}")

    return filename


async def resolve_and_download(
    query: str,
    media_type: str = "audio",
    quality: Optional[str] = None
) -> Dict[str, Any]:
    """
    Main entry point:
    1. Resolve metadata (ID, title, duration, thumbnail)
    2. Check cache (audio_{id}.mp3 or video_{id}.mp4)
    3. If not cached, acquire lock and download to cache
    4. Return full media payload with stream_url
    """
    t_start = time.time()
    
    # 1. Resolve metadata
    meta = await asyncio.to_thread(resolve_metadata_sync, query)
    video_id = meta["id"]
    filename = get_cache_filename(video_id, media_type)
    
    # 2. Check cache
    cached = is_cached(filename)
    if not cached:
        # Acquire download lock to prevent duplicate simultaneous jobs
        lock = await get_download_lock(filename)
        async with lock:
            if not is_cached(filename):
                logger.info(f"Downloading [{media_type}] for ID: {video_id} ('{meta['title']}')")
                await asyncio.to_thread(download_media_sync, video_id, media_type, quality)
            cached = False
    else:
        cached = True

    elapsed = round(time.time() - t_start, 2)
    quality_label = f"{quality}p" if media_type.lower() == "video" else "192kbps"

    return {
        "status": "success",
        "id": video_id,
        "title": meta["title"],
        "duration": meta["duration"],
        "duration_sec": meta["duration_sec"],
        "thumbnail": meta["thumbnail"],
        "uploader": meta["uploader"],
        "type": media_type.lower(),
        "quality": quality_label,
        "filename": filename,
        "cached": cached,
        "elapsed_sec": elapsed,
    }
