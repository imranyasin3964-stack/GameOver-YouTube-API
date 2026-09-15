import os
import re
import time
import logging
import asyncio
from typing import Dict, Any, Optional
from pathlib import Path
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


def get_ydl_base_opts() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 15,
        "nocheckcertificate": True,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "tvhtml5", "web"]
            }
        },
    }
    return opts


async def resolve_metadata_async(query: str) -> Dict[str, Any]:
    """
    Resolves video ID and metadata using official yt-dlp search/extract.
    No third-party scrapers or cookies used.
    """
    clean = query.strip()
    is_id, vid_or_query = extract_youtube_id_or_query(clean)
    
    search_query = vid_or_query if is_id else f"ytsearch1:{clean}"
    
    def _fetch_info():
        opts = get_ydl_base_opts()
        opts["extract_flat"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(search_query, download=False)

    info = await asyncio.to_thread(_fetch_info)
    
    if not info:
        raise ValueError(f"No results found for: {query}")

    if "entries" in info:
        entries = list(info.get("entries", []))
        if not entries:
            raise ValueError(f"No search results found for: {query}")
        target = entries[0]
    else:
        target = info

    res_id = target.get("id")
    res_title = target.get("title", clean)
    dur = target.get("duration") or 0
    
    return {
        "id": res_id,
        "title": res_title,
        "duration": format_duration(dur),
        "duration_sec": int(dur),
        "thumbnail": target.get("thumbnail") or f"https://i.ytimg.com/vi/{res_id}/hqdefault.jpg",
        "uploader": target.get("uploader") or "YouTube",
        "webpage_url": f"https://www.youtube.com/watch?v={res_id}",
    }


def download_media_ytdlp_sync(video_id: str, media_type: str = "audio", quality: Optional[str] = None) -> bool:
    """Official fast downloader via yt-dlp directly."""
    filename = get_cache_filename(video_id, media_type)
    target_path = get_cache_path(filename)
    tmp_path = get_cache_path(f"tmp_{filename}")

    opts = get_ydl_base_opts()
    url = f"https://www.youtube.com/watch?v={video_id}"

    if media_type.lower() == "video":
        height = quality if quality in ("360", "480", "720", "1080") else "480"
        opts.update({
            "format": f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best",
            "outtmpl": str(tmp_path),
            "merge_output_format": "mp4",
        })
    else:
        opts.update({
            "format": "bestaudio/best",
            "outtmpl": str(tmp_path).replace(".mp3", ".%(ext)s"),
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        })

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        expected = get_cache_path(f"tmp_{filename}".replace(".mp3", ".mp3"))
        if media_type.lower() == "audio" and expected.exists():
            expected.replace(target_path)
            return True
        elif tmp_path.exists():
            tmp_path.replace(target_path)
            return True
    except Exception as e:
        logger.warning(f"Official download failed: {e}")
    return False


async def download_media_async(video_id: str, media_type: str = "audio", quality: Optional[str] = None) -> str:
    filename = get_cache_filename(video_id, media_type)
    target_path = get_cache_path(filename)

    if is_cached(filename):
        return filename

    logger.info(f"Downloading {video_id} using Official YouTube API engine...")
    success = await asyncio.to_thread(download_media_ytdlp_sync, video_id, media_type, quality)
    if success and target_path.exists():
        return filename

    raise RuntimeError(f"Failed to download YouTube video {video_id}")


async def resolve_and_download(
    query: str,
    media_type: str = "audio",
    quality: Optional[str] = None
) -> Dict[str, Any]:
    """
    Main entry point:
    1. Resolve metadata (ID, title) via official yt-dlp search.
    2. Check cache
    3. Download via yt-dlp directly
    4. Return pure JSON without direct_url
    """
    t_start = time.time()

    meta = await resolve_metadata_async(query)
    video_id = meta["id"]
    filename = get_cache_filename(video_id, media_type)

    cached = is_cached(filename)
    if not cached:
        lock = await get_download_lock(filename)
        async with lock:
            if not is_cached(filename):
                logger.info(f"Processing [{media_type}] for ID: {video_id} ('{meta['title']}')")
                await download_media_async(video_id, media_type, quality)
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
        "youtube_url": meta["webpage_url"],
        "type": media_type.lower(),
        "quality": quality_label,
        "filename": filename,
        "cached": cached,
        "elapsed_sec": elapsed,
    }
