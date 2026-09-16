import os
import time
import logging
import asyncio
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import psutil

from config import HOST, PORT, BASE_URL, CACHE_DIR
from cache_manager import (
    get_cache_stats,
    get_cache_path,
    cache_cleaner_task,
    is_cached,
)
from engine import resolve_and_download
from scraper_engine import search_youtube_full, extract_playlist_full
from controller_db import check_and_increment_ip
from telegram_bot import broadcast_api_log

LOGS_FILE = Path(__file__).resolve().parent / "logs.txt"

# Completely delete and reset old logs.txt upon startup
try:
    if LOGS_FILE.exists():
        LOGS_FILE.unlink(missing_ok=True)
except Exception:
    pass

# Filter out automated internet vulnerability scanner spam (.env, .git, php, etc.)
class IgnoreScannersFilter(logging.Filter):
    SCANNER_PATTERNS = (
        ".env", ".git", ".docker", ".aws", "cgi-bin", ".php", "wp-", "xmlrpc",
        "shell", "setup.cgi", "actuator", "owa", "mail", "backup", "pma", "myadmin"
    )

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage().lower()
        if "404" in msg:
            for pat in self.SCANNER_PATTERNS:
                if pat in msg:
                    return False
        return True

scanner_filter = IgnoreScannersFilter()

# Setup root logger for both console and logs.txt with fresh write mode ('w')
file_handler = logging.FileHandler(str(LOGS_FILE), mode="w", encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
file_handler.addFilter(scanner_filter)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
console_handler.addFilter(scanner_filter)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers.clear()
root_logger.addHandler(console_handler)
root_logger.addHandler(file_handler)

# Suppress Uvicorn access logger from printing 404 scanner probes
logging.getLogger("uvicorn.access").addFilter(scanner_filter)

logger = logging.getLogger("GameOverAPI")

app = FastAPI(
    title="GameOver YouTube Streaming API",
    description="High-Speed Dedicated Private YouTube Audio & Video Engine for Telegram Music Bots",
    version="2.0.0",
)

# Known vulnerability scanner probes to drop instantly
SCANNER_PATH_PATTERNS = (
    ".env", ".git", ".docker", ".aws", "cgi-bin", ".php", "wp-", "xmlrpc",
    "shell", "setup.cgi", "actuator", "owa", "mail", "backup", "pma", "myadmin"
)

@app.middleware("http")
async def anti_scanner_shield_middleware(request: Request, call_next):
    path = request.url.path.lower()
    if any(pat in path for pat in SCANNER_PATH_PATTERNS):
        return Response(status_code=404, content=b"")
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

START_TIME = time.time()


@app.on_event("startup")
async def on_startup():
    logger.info("==================================================")
    logger.info(f"Starting GameOver YouTube API on {HOST}:{PORT}")
    logger.info(f"Public Base URL: {BASE_URL}")
    logger.info(f"NVMe Cache Directory: {CACHE_DIR}")
    logger.info("==================================================")
    # Start background 24h cache cleaner
    asyncio.create_task(cache_cleaner_task())
    # Start background Telegram Controller & Logger Bot
    from telegram_bot import telegram_polling_loop
    asyncio.create_task(telegram_polling_loop())


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
async def home_dashboard():
    """Interactive visual dashboard for GameOver YouTube API"""
    uptime_sec = int(time.time() - START_TIME)
    uptime_min = uptime_sec // 60
    stats = get_cache_stats()

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>GameOver YouTube High-Speed API</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem; margin: 0; }}
            .container {{ max-width: 900px; margin: 0 auto; }}
            .header {{ display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #334155; padding-bottom: 1rem; margin-bottom: 1.5rem; }}
            .badge {{ background: #10b981; color: #022c22; font-weight: bold; padding: 0.35rem 0.8rem; border-radius: 9999px; font-size: 0.875rem; }}
            .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }}
            .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 0.75rem; padding: 1.25rem; }}
            .card h3 {{ margin: 0 0 0.5rem 0; font-size: 0.875rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }}
            .card .val {{ font-size: 1.5rem; font-weight: bold; color: #38bdf8; }}
            .section {{ background: #1e293b; border: 1px solid #334155; border-radius: 0.75rem; padding: 1.5rem; margin-bottom: 1.5rem; }}
            pre {{ background: #0f172a; padding: 1rem; border-radius: 0.5rem; overflow-x: auto; font-family: monospace; color: #a5f3fc; }}
            code {{ color: #38bdf8; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div>
                    <h1 style="margin:0; font-size:1.75rem;">🚀 GameOver YouTube API</h1>
                    <p style="margin:0.25rem 0 0 0; color:#94a3b8;">High-Speed Private Microservice on Linode VPS</p>
                </div>
                <div class="badge">ONLINE (Port {PORT})</div>
            </div>

            <div class="grid">
                <div class="card">
                    <h3>Uptime</h3>
                    <div class="val">{uptime_min}m ({uptime_sec}s)</div>
                </div>
                <div class="card">
                    <h3>Cache Files</h3>
                    <div class="val">{stats['cached_files_count']}</div>
                </div>
                <div class="card">
                    <h3>Cache Storage</h3>
                    <div class="val">{stats['cache_size_mb']} MB</div>
                </div>
                <div class="card">
                    <h3>Disk Free</h3>
                    <div class="val">{stats['disk_free_gb']} GB</div>
                </div>
            </div>

            <div class="section">
                <h2>⚡ Fast API Endpoints</h2>
                <p>Use these endpoints directly in your Telegram Music Bot:</p>

                <h3>1. Audio Download (Default)</h3>
                <pre>GET /download?type=audio&url=tum+ho</pre>
                <p>Or by YouTube ID:</p>
                <pre>GET /download?type=audio&url=sK7riqg2mr4</pre>

                <h3>2. Video Download (480p / 360p / 720p)</h3>
                <pre>GET /download?type=video&quality=480&url=5dYirJj0I9M</pre>

                <h3>3. Media Stream URL Format</h3>
                <p>Audio is streamed as: <code>/media/audio_{{id}}.mp3</code></p>
                <p>Video is streamed as: <code>/media/video_{{id}}.mp4</code></p>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.get("/health")
async def health_check():
    """Returns server health, RAM, CPU, and NVMe cache stats"""
    vm = psutil.virtual_memory()
    cache_stats = get_cache_stats()
    uptime_sec = round(time.time() - START_TIME, 1)

    return {
        "status": "healthy",
        "uptime_sec": uptime_sec,
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_used_percent": vm.percent,
        "ram_available_mb": round(vm.available / (1024 ** 2), 1),
        "cache": cache_stats,
        "port": PORT,
    }


@app.get("/logs", response_class=HTMLResponse)
async def view_logs():
    """Live interactive log viewer for browser"""
    content = ""
    if LOGS_FILE.exists():
        try:
            with open(LOGS_FILE, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            content = f"Error reading logs: {e}"
    if not content:
        content = "No logs recorded yet."
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>GameOver API - Live Logs</title>
    <meta http-equiv="refresh" content="5">
    <style>
        body {{ background: #0c0d14; color: #00ff88; font-family: 'Consolas', 'Courier New', monospace; padding: 20px; font-size: 13px; line-height: 1.5; }}
        h2 {{ color: #ffffff; margin-bottom: 5px; }}
        p {{ color: #888899; margin-top: 0; margin-bottom: 15px; }}
        pre {{ background: #161722; padding: 15px; border-radius: 8px; border: 1px solid #232738; white-space: pre-wrap; word-break: break-all; max-height: 80vh; overflow-y: auto; }}
        .badge {{ background: #00ff8822; color: #00ff88; padding: 3px 8px; border-radius: 4px; font-size: 12px; }}
    </style>
</head>
<body>
    <h2>📜 GameOver API Live Logs <span class="badge">Auto-refreshing (5s)</span></h2>
    <p>File path: {LOGS_FILE}</p>
    <pre>{content}</pre>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/GET /search")
@app.get("/GET/search")
@app.get("/search")
async def search_media(
    request: Request,
    query: Optional[str] = Query(None, description="Song title, artist name, or YouTube URL"),
    url: Optional[str] = Query(None, description="Alternative parameter for song URL or query"),
    q: Optional[str] = Query(None, description="Short parameter for query"),
):
    """
    Dedicated High-Speed YouTube Search Engine:
    Zero cookies, zero download, instant response (0.2s - 0.4s).
    Caches thumbnail in local NVMe storage and returns clean JSON.
    """
    target = query or url or q
    if not target:
        raw_query = request.url.query
        if "query=" in raw_query:
            target = raw_query.split("query=", 1)[1].split("&")[0]
        elif "url=" in raw_query:
            target = raw_query.split("url=", 1)[1].split("&")[0]
        elif "q=" in raw_query:
            target = raw_query.split("q=", 1)[1].split("&")[0]

    if not target or not target.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required query parameter: 'query' (e.g. /search?query=fakira)"
        )

    clean_query = target.strip()
    t_start = time.time()

    try:
        search_data = await search_youtube_full(clean_query, max_results=5)
    except Exception as e:
        logger.error(f"Search failed for '{clean_query}': {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to search YouTube: {str(e)}"
        )

    req_base = str(request.base_url).rstrip("/")
    primary = search_data["primary"]
    thumb_name = primary["thumbnail_file"]
    local_thumb_url = f"{req_base}/media/{thumb_name}"

    formatted_results = []
    for r in search_data.get("results", []):
        r_thumb = f"{req_base}/media/{r['thumbnail_file']}"
        formatted_results.append({
            "id": r["id"],
            "title": r["title"],
            "duration": r["duration"],
            "duration_sec": r["duration_sec"],
            "thumbnail": r_thumb,
            "uploader": r["uploader"],
            "youtube_url": r["youtube_url"],
        })

    elapsed = round(time.time() - t_start, 2)
    response_payload = {
        "status": "success",
        "id": primary["id"],
        "title": primary["title"],
        "duration": primary["duration"],
        "duration_sec": primary["duration_sec"],
        "thumbnail": local_thumb_url,
        "thumbnail_local": local_thumb_url,
        "thumbnail_remote": primary["thumbnail_remote"],
        "uploader": primary["uploader"],
        "youtube_url": primary["youtube_url"],
        "results": formatted_results,
        "elapsed_sec": elapsed,
        "developer": "@XHamsterFounders",
    }
    return JSONResponse(content=response_payload)


@app.get("/GET /playlist")
@app.get("/GET/playlist")
@app.get("/playlist")
async def playlist_media(
    request: Request,
    url: Optional[str] = Query(None, description="YouTube playlist URL or ID"),
    list: Optional[str] = Query(None, description="YouTube playlist ID or URL"),
    query: Optional[str] = Query(None, description="Alternative parameter for playlist URL"),
    limit: int = Query(25, ge=1, le=50, description="Number of songs to fetch (default 25)"),
):
    """
    Dedicated High-Speed YouTube Playlist API:
    Extracts up to 25 songs (Index 1 to 25) with exact titles, durations, URLs,
    and caches all thumbnails directly into local NVMe storage.
    """
    target = url or list or query
    if not target:
        raw_query = request.url.query
        if "url=" in raw_query:
            target = raw_query.split("url=", 1)[1].split("&")[0]
        elif "list=" in raw_query:
            target = raw_query.split("list=", 1)[1].split("&")[0]
        elif "query=" in raw_query:
            target = raw_query.split("query=", 1)[1].split("&")[0]

    if not target or not target.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required query parameter: 'url' or 'list' (e.g. /playlist?url=https://youtube.com/playlist?list=...)"
        )

    t_start = time.time()
    try:
        pl_data = await extract_playlist_full(target.strip(), max_items=limit)
    except Exception as e:
        logger.error(f"Playlist extraction failed for '{target}': {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to extract YouTube playlist: {str(e)}"
        )

    req_base = str(request.base_url).rstrip("/")
    formatted_items = []
    indexes_dict = {}

    for i, itm in enumerate(pl_data["items"], 1):
        local_thumb = f"{req_base}/media/{itm['thumbnail_file']}"
        entry = {
            "index": i,
            "id": itm["id"],
            "title": itm["title"],
            "duration": itm["duration"],
            "duration_sec": itm["duration_sec"],
            "thumbnail": local_thumb,
            "thumbnail_local": local_thumb,
            "thumbnail_remote": itm["thumbnail_remote"],
            "uploader": itm["uploader"],
            "youtube_url": itm["youtube_url"],
        }
        formatted_items.append(entry)
        indexes_dict[f"index_{i}"] = entry

    elapsed = round(time.time() - t_start, 2)
    response_payload = {
        "status": "success",
        "playlist_id": pl_data["playlist_id"],
        "playlist_title": pl_data["playlist_title"],
        "total_items": len(formatted_items),
        "items": formatted_items,
        "indexes": indexes_dict,
        "elapsed_sec": elapsed,
        "developer": "@XHamsterFounders",
    }
    return JSONResponse(content=response_payload)


@app.get("/GET /download")
@app.get("/GET/download")
@app.get("/download")
async def download_media(
    request: Request,
    type: str = Query("audio", description="Media type: 'audio' (default) or 'video'"),
    quality: Optional[str] = Query(None, description="Video quality (480, 360, 720) or Audio bitrate"),
    stream: bool = Query(False, description="If true, directly streams bytes. If false, returns JSON"),
    url: Optional[str] = Query(None, description="YouTube Video ID, Full URL, or Song Search title (Placed at end)"),
):
    """
    Unified Resolution and Download Endpoint:
    Order of query parameters: ?type=video&quality=480&url=...
    Default: ?type=audio&url=...
    """
    # If url is missing from explicit param, try to extract from raw query string
    target_url = url
    if not target_url:
        raw_query = request.url.query
        if "url=" in raw_query:
            target_url = raw_query.split("url=", 1)[1]

    if not target_url or not target_url.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required query parameter: 'url' (e.g. ?type=audio&url=tum+ho)"
        )

    client_ip = request.client.host if request.client else "127.0.0.1"
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()

    allowed, is_blocked, msg = check_and_increment_ip(client_ip)
    if not allowed:
        if is_blocked:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=msg)
        else:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=msg)

    clean_query = target_url.strip()
    media_type = "video" if type.lower() == "video" else "audio"

    # Default quality settings
    if media_type == "video" and not quality:
        quality = "480"

    start_time_req = time.time()
    try:
        result = await resolve_and_download(clean_query, media_type=media_type, quality=quality)
    except Exception as e:
        logger.error(f"Resolution failed for query '{clean_query}' [{media_type}]: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process YouTube query: {str(e)}"
        )

    filename = result["filename"]
    is_cached_status = result.pop("_cached", False)
    
    # Determine base url from request or config
    req_base = str(request.base_url).rstrip("/")
    stream_url = f"{req_base}/media/{filename}"
    result["stream_url"] = stream_url
    result["developer"] = "@XHamsterFounders"

    # Broadcast log to Telegram Bot admins
    elapsed = round(time.time() - start_time_req, 2)
    log_data = {
        "ip": client_ip,
        "query": clean_query,
        "type": media_type,
        "quality": quality or "default",
        "cached": is_cached_status,
        "elapsed_sec": elapsed,
        "title": result.get("title", clean_query),
        "response": result
    }
    asyncio.create_task(broadcast_api_log(log_data))

    # If user wants direct binary stream
    if stream:
        filepath = get_cache_path(filename)
        return range_requests_response(request, filepath)

    return JSONResponse(content=result)


@app.get("/media/{filename}")
async def stream_media_file(filename: str, request: Request):
    """
    High-Performance Media Streaming with full HTTP 206 Partial Content (Range) support.
    Essential for Telegram Voice/Video WebRTC playback.
    Filename pattern:
    - audio_{id}.mp3
    - video_{id}.mp4
    """
    # Sanitize filename
    clean_name = os.path.basename(filename)
    filepath = get_cache_path(clean_name)

    if not filepath.is_file():
        raise HTTPException(status_code=404, detail="Media file not found or expired")

    return range_requests_response(request, filepath)


def range_requests_response(request: Request, file_path: Path):
    """Handles HTTP 206 Partial Content range requests for smooth WebRTC audio/video streaming"""
    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")

    suffix = file_path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        content_type = "image/jpeg"
    elif suffix == ".png":
        content_type = "image/png"
    elif suffix == ".webp":
        content_type = "image/webp"
    elif suffix == ".mp4":
        content_type = "video/mp4"
    else:
        content_type = "audio/mpeg"

    if not range_header:
        # Return complete file with Accept-Ranges
        def iter_file():
            with open(file_path, mode="rb") as f:
                while chunk := f.read(64 * 1024):
                    yield chunk

        headers = {
            "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Content-Type": content_type,
        }
        return StreamingResponse(iter_file(), headers=headers, status_code=200)

    try:
        # Parse range header: e.g. "bytes=0-1024" or "bytes=1024-"
        range_str = range_header.replace("bytes=", "").strip()
        parts = range_str.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1

        if start >= file_size or end >= file_size or start > end:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{file_size}"}
            )

        chunk_size = (end - start) + 1

        def iter_range():
            with open(file_path, mode="rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    read_bytes = min(64 * 1024, remaining)
                    chunk = f.read(read_bytes)
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_size),
            "Content-Type": content_type,
        }
        return StreamingResponse(iter_range(), headers=headers, status_code=206)
    except Exception as e:
        logger.error(f"Error serving range stream: {e}")
        return Response(status_code=500, content=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT, reload=False, workers=1)
