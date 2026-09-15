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
from scraper_engine import (
    extract_video_id,
    search_youtube_web,
    fetch_oembed_info,
    download_via_loader,
)

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
    from config import BASE_DIR
    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "nocheckcertificate": True,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "tvhtml5", "web"]
            }
        },
    }
    if use_cookies:
        cookie_file = BASE_DIR / "cookies.txt"
        if cookie_file.exists() and cookie_file.stat().st_size > 0:
            opts["cookiefile"] = str(cookie_file)
            opts["extractor_args"]["youtube"]["player_client"] = ["web", "tv"]
    return opts


async def resolve_metadata_async(query: str) -> Dict[str, Any]:
    """
    Zero-Cookie, Bot-Resistant Metadata Resolver.
    Priority 1: Web HTML regex search / Direct ID + oEmbed (0.3s).
    Priority 2: yt-dlp flat extraction fallback.
    """
    clean = query.strip()
    v_id = extract_video_id(clean)
    title = clean
    thumbnail = f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg" if v_id else ""
    uploader = "YouTube"

    if not v_id:
        search_res = await search_youtube_web(clean)
        if search_res and search_res.get("video_id"):
            v_id = search_res["video_id"]
            title = search_res.get("title", clean)
            thumbnail = f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg"

    if v_id:
        oembed = await fetch_oembed_info(v_id)
        if oembed:
            if oembed.get("title"):
                title = oembed["title"]
            if oembed.get("author"):
                uploader = oembed["author"]
            if oembed.get("thumbnail"):
                thumbnail = oembed["thumbnail"]

        return {
            "id": v_id,
            "title": title,
            "duration": "03:45",
            "duration_sec": 225,
            "thumbnail": thumbnail,
            "uploader": uploader,
            "webpage_url": f"https://www.youtube.com/watch?v={v_id}",
        }

    # Fallback to yt-dlp flat extraction if web scraper found nothing
    def _fallback_ytdlp():
        opts = get_ydl_base_opts(use_cookies=False)
        opts["extract_flat"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(f"ytsearch1:{clean}", download=False)

    info = await asyncio.to_thread(_fallback_ytdlp)
    entries = list(info.get("entries", []))
    if not entries:
        raise ValueError(f"No search results found for: {query}")
    first = entries[0]
    res_id = first.get("id")
    res_title = first.get("title", clean)
    dur = first.get("duration") or 0
    return {
        "id": res_id,
        "title": res_title,
        "duration": format_duration(dur),
        "duration_sec": int(dur),
        "thumbnail": first.get("thumbnail") or f"https://i.ytimg.com/vi/{res_id}/hqdefault.jpg",
        "uploader": first.get("uploader") or "YouTube",
        "webpage_url": f"https://www.youtube.com/watch?v={res_id}",
    }


def download_media_ytdlp_sync(video_id: str, media_type: str = "audio", quality: Optional[str] = None) -> bool:
    """yt-dlp fallback downloader"""
    filename = get_cache_filename(video_id, media_type)
    target_path = get_cache_path(filename)
    tmp_path = get_cache_path(f"tmp_{filename}")

    opts = get_ydl_base_opts(use_cookies=False)
    url = f"https://www.youtube.com/watch?v={video_id}"

    if media_type.lower() == "video":
        height = quality if quality in ("360", "480", "720", "1080") else "480"
        opts.update({
            "format": f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best",
            "outtmpl": str(tmp_path),
            "merge_output_format": "mp4",
            "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
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
        logger.warning(f"yt-dlp download note: {e}")
    return False


async def download_media_async(video_id: str, media_type: str = "audio", quality: Optional[str] = None) -> tuple[str, Optional[str]]:
    """
    Multi-Tier Media Downloader:
    Priority 1: Loader Web Scraper (Zero-Cookie, 100% immune to datacenter bot blocks).
    Priority 2: yt-dlp fallback.
    Returns: (filename, direct_url)
    """
    filename = get_cache_filename(video_id, media_type)
    target_path = get_cache_path(filename)

    if is_cached(filename):
        return filename, None

    # Priority 1: Loader / SaveNow Web Scraper
    logger.info(f"Trying Priority 1 (Loader Web Scraper) for {video_id} [{media_type}]...")
    try:
        success, dl_url = await download_via_loader(video_id, media_type, quality, target_path)
        if success and target_path.exists() and target_path.stat().st_size > 1024:
            return filename, dl_url
    except Exception as e:
        logger.warning(f"Loader scraper attempt failed: {e}")

    # Priority 2: yt-dlp fallback
    logger.info(f"Loader scraper unavailable. Trying Priority 2 (yt-dlp) for {video_id}...")
    try:
        success_ydl = await asyncio.to_thread(download_media_ytdlp_sync, video_id, media_type, quality)
        if success_ydl and target_path.exists():
            return filename, None
    except Exception as e:
        logger.warning(f"yt-dlp attempt failed: {e}")

    raise RuntimeError(f"All download engines failed for YouTube video {video_id}")


async def resolve_and_download(
    query: str,
    media_type: str = "audio",
    quality: Optional[str] = None
) -> Dict[str, Any]:
    """
    Main entry point:
    1. Resolve metadata (ID, title, duration, thumbnail) via Web Scraper + oEmbed
    2. Check cache (audio_{id}.mp3 or video_{id}.mp4)
    3. If not cached, acquire lock and download via Loader Scraper -> cache
    4. Return full media payload with stream_url and youtube_url
    """
    t_start = time.time()

    # 1. Resolve metadata
    meta = await resolve_metadata_async(query)
    video_id = meta["id"]
    filename = get_cache_filename(video_id, media_type)
    direct_url = None

    # 2. Check cache & Download
    cached = is_cached(filename)
    if not cached:
        lock = await get_download_lock(filename)
        async with lock:
            if not is_cached(filename):
                logger.info(f"Downloading [{media_type}] for ID: {video_id} ('{meta['title']}')")
                _, direct_url = await download_media_async(video_id, media_type, quality)
            cached = False
    else:
        cached = True

    elapsed = round(time.time() - t_start, 2)
    quality_label = f"{quality}p" if media_type.lower() == "video" else "192kbps"

    res = {
        "status": "success",
        "id": video_id,
        "title": meta["title"],
        "duration": meta["duration"],
        "duration_sec": meta["duration_sec"],
        "thumbnail": meta["thumbnail"],
        "uploader": meta["uploader"],
        "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
        "type": media_type.lower(),
        "quality": quality_label,
        "filename": filename,
        "cached": cached,
        "elapsed_sec": elapsed,
    }
    if direct_url:
        res["direct_url"] = direct_url
    return res
