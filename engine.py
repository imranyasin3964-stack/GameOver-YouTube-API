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
    fetch_duration_web,
    download_via_loader,
)

logger = logging.getLogger("GameOverAPI.Engine")


def format_duration(seconds: Optional[int]) -> str:
    if not seconds or seconds < 0:
        return "00:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def get_ydl_base_opts() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 15,
        "nocheckcertificate": True,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios"]
            }
        },
    }


async def resolve_metadata_async(query: str) -> Dict[str, Any]:
    """
    Zero-Cookie Metadata Resolver:
    1. Extracts direct video ID from URL or query
    2. Uses oEmbed + YouTube Web HTML for instant metadata and duration
    3. 100% bypasses YouTube datacenter bot detection ('Sign in to confirm you're not a bot')
    """
    clean = query.strip()
    video_id = extract_video_id(clean)
    title = clean
    uploader = "YouTube"
    thumbnail = ""
    dur_sec = 210
    dur_str = "03:30"

    # If it's a search term, find the video ID via fast web search
    if not video_id:
        search_res = await search_youtube_web(clean)
        if search_res and search_res.get("video_id"):
            video_id = search_res["video_id"]
            title = search_res.get("title", clean)

    # Fetch title & author from official YouTube oEmbed API (Never blocked)
    if video_id:
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        oembed = await fetch_oembed_info(video_id)
        if oembed:
            if oembed.get("title"):
                title = oembed["title"]
            if oembed.get("author"):
                uploader = oembed["author"]
            if oembed.get("thumbnail"):
                thumbnail = oembed["thumbnail"]

        # Fetch duration from web HTML
        try:
            dur_sec, dur_str = await fetch_duration_web(video_id)
        except Exception as e:
            logger.debug(f"Duration fetch note: {e}")

        return {
            "id": video_id,
            "title": title,
            "duration": dur_str,
            "duration_sec": dur_sec,
            "thumbnail": thumbnail,
            "uploader": uploader,
            "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        }

    # Ultimate fallback: yt-dlp with android client
    def _fallback_ytdlp():
        opts = get_ydl_base_opts()
        opts["extract_flat"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(f"ytsearch1:{clean}", download=False)

    info = await asyncio.to_thread(_fallback_ytdlp)
    entries = list(info.get("entries", [])) if info and "entries" in info else ([info] if info else [])
    if not entries:
        raise ValueError(f"No results found for: {query}")

    target = entries[0]
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
    """yt-dlp fallback downloader using Android/iOS client to bypass botguard"""
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
        logger.warning(f"yt-dlp fallback download note: {e}")
    return False


async def download_media_async(video_id: str, media_type: str = "audio", quality: Optional[str] = None) -> str:
    """
    Multi-Tier Zero-Cookie Media Downloader:
    Priority 1: Loader Web Scraper (Fast CDN, Zero-Cookie, Immune to datacenter botguard).
    Priority 2: yt-dlp Android client fallback.
    """
    filename = get_cache_filename(video_id, media_type)
    target_path = get_cache_path(filename)

    if is_cached(filename):
        return filename

    # Priority 1: Web Scraper (Zero-Cookie)
    logger.info(f"Downloading {video_id} [{media_type}] via Web Scraper Engine...")
    try:
        success = await download_via_loader(video_id, media_type, quality, target_path)
        if success and target_path.exists() and target_path.stat().st_size > 1024:
            return filename
    except Exception as e:
        logger.warning(f"Web Scraper download attempt note: {e}")

    # Priority 2: yt-dlp Android client fallback
    logger.info(f"Trying yt-dlp Android client fallback for {video_id} [{media_type}]...")
    try:
        success_ydl = await asyncio.to_thread(download_media_ytdlp_sync, video_id, media_type, quality)
        if success_ydl and target_path.exists() and target_path.stat().st_size > 1024:
            return filename
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
    1. Resolve metadata via zero-cookie Web Scraper & oEmbed
    2. Check cache
    3. Download media into NVMe cache
    4. Return clean JSON (NO third-party direct_url, only internal stream_url)
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

    # Return pure JSON without any third-party direct_url
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
