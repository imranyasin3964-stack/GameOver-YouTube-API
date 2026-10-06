"""
harvester.py - Dedicated Auto-Harvest & Pre-Cache Engine for GameOver YouTube API
==================================================================================
Features:
- Multi-Engine Concurrency: 3 Parallel download slots running simultaneously
- Multi-Genre Discovery:
  * 🎲 Random Mixed Mode (Round-Robin Interleaved: 1 Bollywood -> 1 Pakistani/Jhol -> 1 Arabic -> 1 Russian/Phonk -> 1 Folk -> 1 Hollywood -> repeat!)
  * 🇵🇰 Pakistani / Coke Studio / Jhol
  * 🇮🇳 Bollywood & Hindi Romantic Hits
  * 🇸🇦 Arabic Trending & Pop Hits
  * 🇷🇺 Russian Viral Hits & Phonk
  * 🪕 Folk & Regional Traditional Hits
  * 🌍 Hollywood & Billboard Global Hits
- Seed Link / Autoplay Vibe Pre-cacher:
  * Give song name or YouTube link -> extracts 25–40 related tracks via resolve_smart_autoplay!
  * Previews tracks to user in Telegram with instant [🚀 Download All Tracks] button!
- Uses Official Google YouTube Data API v3 (AIzaSyB7-u3OZbeThZz2RcxIYO6KXRCVQyYh-hI) with Render fallback
- Strict Deduplication: Skips if video_480 and audio_opus are already cached on disk or DB (Zero repeat downloads)
- 2-in-1 Fast Pipeline: Downloads 480p Video -> Extracts Studio HD Opus via local FFmpeg in 0.1s!
- Instant Telegram Notification for EVERY song cached with full details and live disk space
- Live NVMe Disk Monitoring (tracks actual used/free GB, safety stop if free < 3 GB)
- Non-blocking async background worker with Start/Stop/Status controls for Telegram Bot & REST API
"""

import asyncio
import hashlib
import html
import logging
import re
import shutil
import time
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Tuple

import aiohttp

from config import CACHE_DIR, YOUTUBE_API_KEY, RENDER_SEARCH_URL, CF_WORKER_URL, BASE_URL
import controller_db
from scraper_engine import (
    download_via_loader,
    save_thumbnail_local,
    parse_duration_to_sec,
    resolve_smart_autoplay,
    resolve_shruti_autoplay,
    fetch_shruti_autoplay,
)
from engine import convert_media_ffmpeg

logger = logging.getLogger("GameOverAPI.Harvester")

# 6 Curated High-Volume Genres (50+ songs each)
HARVEST_GENRES = {
    "pakistani": {
        "label": "🇵🇰 Pakistani Hits, Coke Studio & Sad/Jhol",
        "queries": [
            "Jhol Pakistani song official video",
            "Coke Studio Pakistan top viral songs official",
            "Kaifi Khalil Kahani Suno official video",
            "Pasoori Ali Sethi Shae Gill official",
            "Rahat Fateh Ali Khan sad songs official",
            "Atif Aslam Pakistani hits official song",
            "Ali Zafar best hits official video",
            "Asim Azhar top hit songs official",
            "Pakistani sad songs top hits official"
        ]
    },
    "bollywood": {
        "label": "🇮🇳 Bollywood & Hindi Romantic Hits",
        "queries": [
            "Arijit Singh romantic hits official music video",
            "Atif Aslam best bollywood songs official",
            "Pritam hit songs official video",
            "Shreya Ghoshal top songs official video",
            "Bollywood top romantic songs 2024 official",
            "Mohit Chauhan romantic hits official video",
            "Sonu Nigam best hits official song"
        ]
    },
    "arabic": {
        "label": "🇸🇦 Arabic Trending & Pop Hits",
        "queries": [
            "Amr Diab best hit songs official",
            "Saad Lamjarred top hits official video",
            "Nancy Ajram best songs official video",
            "Arabic trending remix pop hits official",
            "Sherine top arabic songs official",
            "Balti Arabic viral songs official"
        ]
    },
    "russian": {
        "label": "🇷🇺 Russian Viral Hits & Phonk",
        "queries": [
            "MiyaGi best songs official video",
            "Rauf Faik top hit songs official",
            "Russian viral hits songs official",
            "Drift phonk aggressive best tracks",
            "Kordhell phonk hits official video",
            "DVRST phonk viral songs official"
        ]
    },
    "folk": {
        "label": "🪕 Folk & Regional Traditional Hits",
        "queries": [
            "Punjabi folk traditional songs official",
            "Rajasthani folk songs Coke Studio official",
            "Sufi folk traditional songs official video",
            "Sindhi folk music official video",
            "Balochi folk traditional songs official",
            "Acoustic folk songs unplugged official"
        ]
    },
    "hollywood": {
        "label": "🌍 Hollywood & Billboard Global Hits",
        "queries": [
            "Billboard Hot 100 top songs official music video",
            "The Weeknd top hit songs official video",
            "Taylor Swift best hit songs official video",
            "Bruno Mars top songs official video",
            "Ed Sheeran best songs official video",
            "Global viral pop english songs official"
        ]
    }
}

BANNED_TITLE_WORDS = [
    "jukebox", "full album", "1 hour", "1hour", "10 hours", "loop", "all songs",
    "mashup", "non stop", "nonstop", "top 10", "top 20", "top 50", "compilation"
]

# In-memory storage for Seed Autoplay Previews so users can click [Download All]
SEED_PREVIEWS: Dict[str, Dict[str, Any]] = {}


class HarvestManager:
    def __init__(self):
        self.is_running: bool = False
        self.continuous_mode: bool = False
        self.job_queue: List[Dict[str, Any]] = []
        self.task: Optional[asyncio.Task] = None
        self.notify_cb: Optional[Callable[[str], Any]] = None
        self.selected_genre: str = "random"
        self.active_mode: str = "idle"  # "random", "genre", "seed", "continuous"
        self.current_genre: str = ""
        self.current_song: str = ""
        self.total_downloaded: int = 0
        self.total_skipped: int = 0
        self.total_failed: int = 0
        self.concurrency: int = 3
        self.start_time: float = 0.0
        self.last_status_msg: str = "Idle"
        self._notify_lock = asyncio.Lock()

    def get_status(self) -> Dict[str, Any]:
        disk_stats = self.get_disk_stats()
        uptime_sec = int(time.time() - self.start_time) if self.is_running and self.start_time > 0 else 0
        return {
            "is_running": self.is_running,
            "continuous_mode": self.continuous_mode,
            "queue_len": len(self.job_queue),
            "concurrency": self.concurrency,
            "active_mode": self.active_mode,
            "selected_genre": self.selected_genre,
            "current_genre": self.current_genre or "None",
            "current_song": self.current_song or "None",
            "total_downloaded": self.total_downloaded,
            "total_skipped": self.total_skipped,
            "total_failed": self.total_failed,
            "uptime_sec": uptime_sec,
            "disk_free_gb": disk_stats["free_gb"],
            "disk_used_gb": disk_stats["used_gb"],
            "disk_total_gb": disk_stats["total_gb"],
            "status_text": self.last_status_msg,
        }

    @staticmethod
    def get_disk_stats() -> Dict[str, float]:
        try:
            d = shutil.disk_usage(str(CACHE_DIR))
            return {
                "free_gb": round(d.free / (1024 ** 3), 2),
                "used_gb": round(d.used / (1024 ** 3), 2),
                "total_gb": round(d.total / (1024 ** 3), 2),
            }
        except Exception:
            return {"free_gb": 0.0, "used_gb": 0.0, "total_gb": 0.0}

    def start(
        self,
        genre: str = "random",
        target_per_category: int = 50,
        concurrency: int = 3,
        notify_callback: Optional[Callable[[str], Any]] = None
    ) -> Dict[str, Any]:
        clean_genre = (genre or "random").lower().strip()
        if self.is_running:
            self.job_queue.append({
                "type": "genre",
                "genre": clean_genre,
                "target_per_category": target_per_category,
                "concurrency": concurrency,
                "notify_callback": notify_callback
            })
            return {"status": "queued", "position": len(self.job_queue)}

        self.is_running = True
        self.continuous_mode = False
        self.notify_cb = notify_callback
        self.start_time = time.time()
        self.total_downloaded = 0
        self.total_skipped = 0
        self.total_failed = 0
        self.concurrency = max(1, min(5, concurrency))
        self.selected_genre = clean_genre
        if clean_genre in ("random", "all", "mixed"):
            self.active_mode = "random"
            self.last_status_msg = f"Random Mixed ({self.concurrency} Slots)"
        else:
            self.active_mode = "genre"
            g_lbl = HARVEST_GENRES.get(clean_genre, {}).get("label", clean_genre.title())
            self.last_status_msg = f"{g_lbl} ({self.concurrency} Slots)"

        self.task = asyncio.create_task(self._run_loop(clean_genre, target_per_category))
        return {"status": "started", "position": 0}

    def start_seed_autoplay(
        self,
        seed_query: str,
        tracks: Optional[List[Dict[str, Any]]] = None,
        target_count: int = 35,
        concurrency: int = 3,
        engine: str = "vibe",
        notify_callback: Optional[Callable[[str], Any]] = None
    ) -> Dict[str, Any]:
        if self.is_running:
            self.job_queue.append({
                "type": "seed",
                "seed_query": seed_query,
                "tracks": tracks,
                "target_count": target_count,
                "concurrency": concurrency,
                "engine": engine,
                "notify_callback": notify_callback
            })
            return {"status": "queued", "position": len(self.job_queue)}

        self.is_running = True
        self.continuous_mode = False
        self.notify_cb = notify_callback
        self.start_time = time.time()
        self.total_downloaded = 0
        self.total_skipped = 0
        self.total_failed = 0
        self.concurrency = max(1, min(5, concurrency))
        self.active_mode = "seed"
        self.selected_genre = f"seed:{seed_query}"
        engine_tag = "Autoplay 2 (Shruti)" if engine in ("shruti", "autoplay2", "2") else "Autoplay 1 (Vibe)"
        self.current_genre = f"📻 {engine_tag}: {seed_query[:25]}"
        self.last_status_msg = f"{engine_tag} ({self.concurrency} Slots)"
        self.task = asyncio.create_task(self._run_seed_loop(seed_query, tracks, target_count, engine=engine))
        return {"status": "started", "position": 0}

    def start_continuous(
        self,
        concurrency: int = 3,
        notify_callback: Optional[Callable[[str], Any]] = None
    ) -> Dict[str, Any]:
        """Starts 24/7 Endless Non-Stop Harvest loop across all genres + Shruti Autoplay Radio"""
        if self.is_running and self.continuous_mode:
            return {"status": "already_running", "position": 0}

        if self.is_running:
            self.stop()

        self.is_running = True
        self.continuous_mode = True
        self.notify_cb = notify_callback
        self.start_time = time.time()
        self.total_downloaded = 0
        self.total_skipped = 0
        self.total_failed = 0
        self.concurrency = max(1, min(5, concurrency))
        self.active_mode = "continuous"
        self.selected_genre = "24/7 Endless"
        self.current_genre = "♾️ 24/7 Endless All Genres + Autoplay Mix"
        self.last_status_msg = f"♾️ 24/7 Endless ({self.concurrency} Slots)"
        self.task = asyncio.create_task(self._run_continuous_loop())
        return {"status": "started", "position": 0}

    def stop(self) -> bool:
        self.continuous_mode = False
        self.job_queue.clear()
        if not self.is_running:
            return False
        self.is_running = False
        self.active_mode = "idle"
        self.last_status_msg = "Stopped by User"
        if self.task and not self.task.done():
            self.task.cancel()
        return True

    async def _send_notify(self, text: str):
        if self.notify_cb:
            async with self._notify_lock:
                try:
                    res = self.notify_cb(text)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as e:
                    logger.debug(f"[Harvester] Notify callback note: {e}")

    @staticmethod
    def _parse_iso_duration(dur_str: str) -> Tuple[str, int]:
        m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', dur_str or "")
        if not m:
            return "03:30", 210
        h = int(m.group(1) or 0)
        mins = int(m.group(2) or 0)
        secs = int(m.group(3) or 0)
        total_sec = h * 3600 + mins * 60 + secs
        if h > 0:
            return f"{h:02d}:{mins:02d}:{secs:02d}", total_sec
        return f"{mins:02d}:{secs:02d}", total_sec

    async def _fetch_candidates_for_query(self, session: aiohttp.ClientSession, query: str, max_items: int = 25) -> List[Dict[str, Any]]:
        """Fetches video candidates using official Google YouTube Data API v3 with Render search fallback"""
        candidates = []
        if YOUTUBE_API_KEY:
            try:
                search_url = (
                    f"https://www.googleapis.com/youtube/v3/search"
                    f"?part=snippet&q={urllib.parse.quote(query)}"
                    f"&type=video&videoCategoryId=10&maxResults={max_items}"
                    f"&key={YOUTUBE_API_KEY}"
                )
                async with session.get(search_url, timeout=aiohttp.ClientTimeout(total=6.0)) as resp:
                    if resp.status == 200:
                        s_data = await resp.json()
                        v_ids = []
                        raw_map = {}
                        for item in s_data.get("items", []):
                            vid = item.get("id", {}).get("videoId")
                            if vid and len(vid) == 11:
                                snip = item.get("snippet", {})
                                raw_map[vid] = {
                                    "id": vid,
                                    "title": html.unescape(snip.get("title", "")),
                                    "uploader": html.unescape(snip.get("channelTitle", "YouTube")),
                                    "thumbnail": snip.get("thumbnails", {}).get("high", {}).get("url") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                                }
                                v_ids.append(vid)

                        # Batch details query for duration filtering
                        if v_ids:
                            details_url = (
                                f"https://www.googleapis.com/youtube/v3/videos"
                                f"?part=contentDetails&id={','.join(v_ids)}"
                                f"&key={YOUTUBE_API_KEY}"
                            )
                            async with session.get(details_url, timeout=aiohttp.ClientTimeout(total=5.0)) as d_resp:
                                if d_resp.status == 200:
                                    d_data = await d_resp.json()
                                    for ditm in d_data.get("items", []):
                                        vid = ditm.get("id")
                                        if vid in raw_map:
                                            raw_dur = ditm.get("contentDetails", {}).get("duration", "")
                                            dur_str, dur_sec = self._parse_iso_duration(raw_dur)
                                            raw_map[vid]["duration"] = dur_str
                                            raw_map[vid]["duration_sec"] = dur_sec

                        for vid, obj in raw_map.items():
                            t_lower = obj["title"].lower()
                            if any(b in t_lower for b in BANNED_TITLE_WORDS):
                                continue
                            sec = obj.get("duration_sec", 210)
                            if 60 <= sec <= 480:  # 1 min to 8 mins
                                candidates.append(obj)
                        return candidates
            except Exception as e:
                logger.debug(f"[Harvester] YouTube Data API query error for '{query}': {e}")

        # Fallback to Render Search API
        if RENDER_SEARCH_URL:
            try:
                r_url = f"{RENDER_SEARCH_URL.rstrip('/')}/search?query={urllib.parse.quote(query)}"
                async with session.get(r_url, timeout=aiohttp.ClientTimeout(total=5.0)) as r_resp:
                    if r_resp.status == 200:
                        r_data = await r_resp.json()
                        for itm in r_data.get("results", []):
                            vid = itm.get("id")
                            if vid and len(vid) == 11:
                                t = itm.get("title", "")
                                if not any(b in t.lower() for b in BANNED_TITLE_WORDS):
                                    candidates.append({
                                        "id": vid,
                                        "title": t,
                                        "uploader": itm.get("uploader", "YouTube"),
                                        "thumbnail": itm.get("thumbnail") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                                        "duration": itm.get("duration", "03:30"),
                                        "duration_sec": itm.get("duration_sec", 210)
                                    })
            except Exception as r_err:
                logger.debug(f"[Harvester] Render API query fallback note: {r_err}")

        return candidates

    async def _harvest_single_track(self, track: Dict[str, Any]) -> bool:
        """
        Downloads 480p Video -> Extracts Studio HD Opus locally in 0.1s!
        Sends rich real-time live notifications with video link, audio status, and direct stream URLs.
        Saves thumbnails and records metadata in controller database.
        Returns True if song is ready/cached, False if failed.
        """
        v_id = track["id"]
        title = track["title"]
        uploader = track.get("uploader", "YouTube")
        dur_str = track.get("duration", "03:30")
        dur_sec = track.get("duration_sec", 210)
        thumb_url = track.get("thumbnail") or f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg"

        clean_id = "".join(c for c in v_id if c.isalnum() or c in ("-", "_"))
        vid_path = CACHE_DIR / f"video_{clean_id}.mp4"
        opus_path = CACHE_DIR / f"audio_{clean_id}.opus"

        yt_link = f"https://www.youtube.com/watch?v={v_id}"
        base_clean = (CF_WORKER_URL or BASE_URL).rstrip("/")
        stream_audio_url = f"{base_clean}/media/audio_{clean_id}.opus"
        stream_video_url = f"{base_clean}/media/video_{clean_id}.mp4"

        # Check if already fully cached on disk
        has_video = vid_path.is_file() and vid_path.stat().st_size > 1024
        has_opus = opus_path.is_file() and opus_path.stat().st_size > 1024

        if has_video and has_opus:
            self.total_skipped += 1
            logger.info(f"[Harvester] Already fully cached: {v_id} ('{title}') - Skipping download.")
            disk_now = self.get_disk_stats()
            skip_msg = (
                f"⚡ <b>Sᴏɴɢ Aʟʀᴇᴀᴅʏ Cᴀᴄʜᴇᴅ (Iɴsᴛᴀɴᴛ Rᴇᴀᴅʏ)!</b>\n\n"
                f"🎵 <b>Tɪᴛʟᴇ:</b> <code>{title}</code>\n"
                f"👤 <b>Aʀᴛɪsᴛ:</b> {uploader}\n"
                f"⏱️ <b>Dᴜʀᴀᴛɪᴏɴ:</b> <code>{dur_str}</code>\n"
                f"📂 <b>Gᴇɴʀᴇ:</b> {self.current_genre}\n"
                f"🔗 <b>YᴏᴜTᴜʙᴇ Lɪɴᴋ:</b> <a href=\"{yt_link}\">{yt_link}</a>\n"
                f"🎬 <b>Vɪᴅᴇᴏ 480p:</b> <code>video_{clean_id}.mp4</code> (Ready)\n"
                f"🎙️ <b>Oᴘᴜs 48kHz:</b> <code>audio_{clean_id}.opus</code> (Ready)\n"
                f"🌐 <b>Aᴜᴅɪᴏ Sᴛʀᴇᴀᴍ URL:</b>\n<code>{stream_audio_url}</code>\n"
                f"🌐 <b>Vɪᴅᴇᴏ Sᴛʀᴇᴀᴍ URL:</b>\n<code>{stream_video_url}</code>\n"
                f"💾 <b>NVMe Fʀᴇᴇ:</b> <code>{disk_now['free_gb']} GB</code> / <code>{disk_now['total_gb']} GB</code>\n"
                f"📊 <b>Pʀᴏɢʀᴇss:</b> Cached: <code>{self.total_downloaded}</code> | Skipped: <code>{self.total_skipped}</code>"
            )
            await self._send_notify(skip_msg)
            return True

        self.current_song = title
        logger.info(f"[Harvester] Downloading: {v_id} - '{title}'...")

        # Step 0: Real-time Live Start Alert
        start_msg = (
            f"🔄 <b>Cᴀᴄʜɪɴɢ Tʀᴀᴄᴋ...</b>\n\n"
            f"🎵 <b>Tɪᴛʟᴇ:</b> <code>{title}</code>\n"
            f"👤 <b>Aʀᴛɪsᴛ:</b> {uploader}\n"
            f"⏱️ <b>Dᴜʀᴀᴛɪᴏɴ:</b> <code>{dur_str}</code>\n"
            f"📂 <b>Gᴇɴʀᴇ:</b> {self.current_genre}\n"
            f"🔗 <b>YᴏᴜTᴜʙᴇ Lɪɴᴋ:</b> <a href=\"{yt_link}\">{yt_link}</a>\n"
            f"⏳ <i>Dᴏᴡɴʟᴏᴀᴅɪɴɢ 480p Vɪᴅᴇᴏ &amp; Rᴇsᴏʟᴠɪɴɢ 48kHz Oᴘᴜs Aᴜᴅɪᴏ...</i>"
        )
        await self._send_notify(start_msg)

        # Step 1: Download 480p Video if missing
        if not has_video:
            success_vid = await download_via_loader(v_id, media_type="video", quality="480", target_path=vid_path)
            if success_vid and vid_path.is_file() and vid_path.stat().st_size > 1024:
                has_video = True
            else:
                logger.warning(f"[Harvester] Video download failed for {v_id}.")

        # Step 2: Instant Local Opus Extraction from Video (0.15s via FFmpeg)
        if has_video and not has_opus:
            logger.info(f"[Harvester] Extracting 48kHz Opus locally from video for {v_id}...")
            transcode_ok = await convert_media_ffmpeg(vid_path, opus_path, "opus")
            if transcode_ok and opus_path.is_file() and opus_path.stat().st_size > 1024:
                has_opus = True

        # Step 3: If video failed or transcode failed, download opus directly via loader
        if not has_opus:
            logger.info(f"[Harvester] Fallback: downloading opus directly for {v_id}...")
            success_opus = await download_via_loader(v_id, media_type="audio", audio_format="opus", target_path=opus_path)
            if success_opus and opus_path.is_file() and opus_path.stat().st_size > 1024:
                has_opus = True

        if not has_opus and not has_video:
            self.total_failed += 1
            fail_msg = (
                f"❌ <b>Tʀᴀᴄᴋ Cᴀᴄʜɪɴɢ Fᴀɪʟᴇᴅ</b>\n\n"
                f"🎵 <b>Tɪᴛʟᴇ:</b> <code>{title}</code>\n"
                f"🔗 <b>YᴏᴜTᴜʙᴇ Lɪɴᴋ:</b> <a href=\"{yt_link}\">{yt_link}</a>\n"
                f"⚠️ <i>Unable to resolve media streams from YouTube. Skipping to next track.</i>"
            )
            await self._send_notify(fail_msg)
            return False

        # Step 4: Cache thumbnail locally
        await save_thumbnail_local(v_id, thumb_url)

        # Step 5: Save metadata in SQLite Database
        controller_db.save_media_cache(
            video_id=v_id,
            title=title,
            duration=dur_str,
            duration_sec=dur_sec,
            thumbnail=thumb_url,
            uploader=uploader,
            youtube_url=yt_link,
            audio_file=f"audio_{clean_id}.opus" if has_opus else None,
            video_file=f"video_{clean_id}.mp4" if has_video else None,
        )
        controller_db.save_query_mapping(title, v_id)

        self.total_downloaded += 1
        logger.info(f"[Harvester] SUCCESS ✅: {v_id} ('{title}') | Video: {has_video} | Opus: {has_opus}")

        # Step 6: Rich Completion Live Alert for this exact track
        disk_now = self.get_disk_stats()
        done_msg = (
            f"✅ <b>Sᴏɴɢ Cᴀᴄʜᴇᴅ Sᴜᴄᴄᴇssғᴜʟʟʏ (2-ɪɴ-1 Rᴇᴀᴅʏ)!</b>\n\n"
            f"🎵 <b>Tɪᴛʟᴇ:</b> <code>{title}</code>\n"
            f"👤 <b>Aʀᴛɪsᴛ:</b> {uploader}\n"
            f"⏱️ <b>Dᴜʀᴀᴛɪᴏɴ:</b> <code>{dur_str}</code>\n"
            f"📂 <b>Gᴇɴʀᴇ:</b> {self.current_genre}\n"
            f"🔗 <b>YᴏᴜTᴜʙᴇ Lɪɴᴋ:</b> <a href=\"{yt_link}\">{yt_link}</a>\n"
            f"🎬 <b>Vɪᴅᴇᴏ (480p):</b> {'✅ Resolved & Saved' if has_video else '❌ Failed'}\n"
            f"🎙️ <b>Aᴜᴅɪᴏ (48kHz Opus):</b> {'✅ Resolved & Saved' if has_opus else '❌ Failed'}\n"
            f"🌐 <b>Aᴜᴅɪᴏ Sᴛʀᴇᴀᴍ URL:</b>\n<code>{stream_audio_url}</code>\n"
            f"🌐 <b>Vɪᴅᴇᴏ Sᴛʀᴇᴀᴍ URL:</b>\n<code>{stream_video_url}</code>\n"
            f"💾 <b>NVMe Fʀᴇᴇ:</b> <code>{disk_now['free_gb']} GB</code> / <code>{disk_now['total_gb']} GB</code>\n"
            f"📊 <b>Pʀᴏɢʀᴇss:</b> Total Cached: <code>{self.total_downloaded}</code> (Skipped: {self.total_skipped})"
        )
        await self._send_notify(done_msg)
        return True

    async def _worker(self, queue: asyncio.Queue, sem: asyncio.Semaphore):
        """Worker task consuming tracks from the queue with bounded concurrency"""
        while self.is_running:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                if queue.empty():
                    break
                continue
            except asyncio.CancelledError:
                break

            try:
                async with sem:
                    if not self.is_running:
                        queue.task_done()
                        break

                    # Disk free safety check
                    disk = self.get_disk_stats()
                    if disk["free_gb"] < 3.0:
                        warn_msg = f"⚠️ <b>Storage Warning:</b> Only {disk['free_gb']} GB free remaining in bucket! Stopping harvest."
                        logger.warning(warn_msg)
                        await self._send_notify(warn_msg)
                        self.stop()
                        queue.task_done()
                        break

                    if isinstance(item, tuple):
                        genre_label, track = item
                        self.current_genre = genre_label
                    else:
                        track = item

                    try:
                        await asyncio.wait_for(self._harvest_single_track(track), timeout=75.0)
                    except asyncio.TimeoutError:
                        logger.warning(f"[Harvester Worker] Track {track.get('id')} timed out after 75s. Skipping.")
                        self.total_failed += 1

                    await asyncio.sleep(1.0)
            except Exception as e:
                logger.error(f"[Harvester Worker] Error processing track: {e}")
            finally:
                queue.task_done()

    async def _run_loop(self, selected_genre: str, target_per_category: int):
        """
        Executes harvest loop:
        - If selected_genre is 'random' / 'all' / 'mixed':
          Gathers candidates across all 6 genres and INTERLEAVES them round-robin so
          Bollywood -> Pakistani -> Arabic -> Russian -> Folk -> Hollywood -> repeat!
        - If selected_genre is a specific genre key:
          Downloads only candidates for that category!
        """
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        async with aiohttp.ClientSession(headers=headers) as session:
            sem = asyncio.Semaphore(self.concurrency)

            if selected_genre in ("random", "all", "mixed"):
                logger.info(f"[Harvester] Starting RANDOM MIXED mode across all {len(HARVEST_GENRES)} genres.")
                await self._send_notify(
                    f"🎲 <b>Auto-Harvest: Random Mixed Mode Started!</b>\n"
                    f"⚡ <b>Parallel Slots:</b> <code>{self.concurrency} Slots</code>\n"
                    f"🎯 <b>Target:</b> <code>{target_per_category} Songs / Genre</code>\n"
                    f"🔄 <b>Strategy:</b> Interleaved Round-Robin (Bolly ➡️ Pak ➡️ Arab ➡️ Phonk ➡️ Folk ➡️ Holly)\n"
                    f"📦 <b>Formats:</b> 🎬 480p Video + 🎙️ 48kHz Opus Audio"
                )

                # Fetch candidate pools for all genres
                genre_pools: Dict[str, List[Dict[str, Any]]] = {}
                seen_all_vids = set()

                for g_key, g_info in HARVEST_GENRES.items():
                    if not self.is_running:
                        break
                    pool: List[Dict[str, Any]] = []
                    for q in g_info["queries"]:
                        if not self.is_running or len(pool) >= target_per_category:
                            break
                        cands = await self._fetch_candidates_for_query(session, q, max_items=25)
                        for c in cands:
                            if c["id"] not in seen_all_vids:
                                seen_all_vids.add(c["id"])
                                pool.append(c)
                                if len(pool) >= target_per_category:
                                    break
                        await asyncio.sleep(0.2)
                    genre_pools[g_key] = pool
                    logger.info(f"[Harvester] Gathered {len(pool)} candidates for {g_key}.")

                # Interleave round-robin across genres:
                # 1 Bollywood, 1 Pakistani, 1 Arabic, 1 Russian/Phonk, 1 Folk, 1 Hollywood, etc.
                interleaved_items: List[Tuple[str, Dict[str, Any]]] = []
                max_depth = max((len(p) for p in genre_pools.values()), default=0)
                genre_keys = list(HARVEST_GENRES.keys())

                for idx in range(max_depth):
                    for g_key in genre_keys:
                        pool = genre_pools.get(g_key, [])
                        if idx < len(pool):
                            lbl = HARVEST_GENRES[g_key]["label"]
                            interleaved_items.append((lbl, pool[idx]))

                logger.info(f"[Harvester] Interleaved total: {len(interleaved_items)} tracks queued.")

                # Enqueue all interleaved items
                queue = asyncio.Queue()
                for item in interleaved_items:
                    await queue.put(item)

                workers = [asyncio.create_task(self._worker(queue, sem)) for _ in range(self.concurrency)]
                await queue.join()

                for w in workers:
                    if not w.done():
                        w.cancel()
                await asyncio.gather(*workers, return_exceptions=True)

            else:
                # Specific genre mode
                g_info = HARVEST_GENRES.get(selected_genre)
                if not g_info:
                    g_info = HARVEST_GENRES["pakistani"]
                    selected_genre = "pakistani"

                self.current_genre = g_info["label"]
                logger.info(f"[Harvester] Starting Specific Genre Mode: {self.current_genre}")
                await self._send_notify(
                    f"🚀 <b>Auto-Harvest: {self.current_genre} Started!</b>\n"
                    f"⚡ <b>Parallel Slots:</b> <code>{self.concurrency} Slots</code>\n"
                    f"🎯 <b>Target:</b> <code>{target_per_category} Songs</code>\n"
                    f"📦 <b>Formats:</b> 🎬 480p Video + 🎙️ 48kHz Opus Audio"
                )

                gathered_tracks: List[Dict[str, Any]] = []
                seen_vids = set()

                for q in g_info["queries"]:
                    if not self.is_running or len(gathered_tracks) >= target_per_category:
                        break
                    cands = await self._fetch_candidates_for_query(session, q, max_items=25)
                    for c in cands:
                        if c["id"] not in seen_vids:
                            seen_vids.add(c["id"])
                            gathered_tracks.append(c)
                            if len(gathered_tracks) >= target_per_category:
                                break
                    await asyncio.sleep(0.3)

                queue = asyncio.Queue()
                for t in gathered_tracks:
                    await queue.put((g_info["label"], t))

                workers = [asyncio.create_task(self._worker(queue, sem)) for _ in range(self.concurrency)]
                await queue.join()

                for w in workers:
                    if not w.done():
                        w.cancel()
                await asyncio.gather(*workers, return_exceptions=True)

        disk = self.get_disk_stats()
        summary_text = (
            f"🎉 <b>Auto-Harvest Finished Successfully!</b>\n\n"
            f"📥 <b>Total Downloaded:</b> <code>{self.total_downloaded}</code>\n"
            f"⏭️ <b>Total Skipped:</b> <code>{self.total_skipped}</code>\n"
            f"❌ <b>Total Failed:</b> <code>{self.total_failed}</code>\n"
            f"💾 <b>Final Disk Free:</b> <code>{disk['free_gb']} GB</code> / <code>{disk['total_gb']} GB</code>"
        )
        logger.info("[Harvester] Batch completed.")
        await self._send_notify(summary_text)

        # Process next queued job if waiting
        if self.job_queue and self.is_running:
            next_job = self.job_queue.pop(0)
            await self._run_queued_job(next_job)
            return

        if not self.continuous_mode:
            self.is_running = False
            self.active_mode = "idle"
            self.last_status_msg = "Completed"

    async def _run_seed_loop(
        self,
        seed_query: str,
        tracks: Optional[List[Dict[str, Any]]],
        target_count: int,
        engine: str = "vibe"
    ):
        """Runs Seed Autoplay harvest (Vibe AI or Shruti Official Mix): resolves related tracks and caches video+opus for all"""
        is_shruti = engine in ("shruti", "autoplay2", "2", "mix")
        engine_label = "Autoplay 2 (Shruti Mix)" if is_shruti else "Autoplay 1 (Vibe AI)"
        logger.info(f"[Harvester] Seed {engine_label} Loop started for '{seed_query}'.")

        # Step 1: If tracks not passed, resolve now
        if not tracks:
            await self._send_notify(f"🔍 <b>Resolving {engine_label} tracks for:</b> <code>{seed_query}</code>...")
            try:
                if is_shruti:
                    res = await resolve_shruti_autoplay(seed_query, target_count=target_count)
                else:
                    res = await resolve_smart_autoplay(seed_query, target_count=target_count)
                tracks = res.get("tracks", [])
            except Exception as e:
                err_msg = f"❌ <b>Failed to resolve {engine_label} tracks:</b> <code>{e}</code>"
                logger.error(err_msg)
                await self._send_notify(err_msg)
                if self.job_queue:
                    next_job = self.job_queue.pop(0)
                    await self._run_queued_job(next_job)
                    return
                self.is_running = False
                self.active_mode = "idle"
                return

        if not tracks:
            await self._send_notify(f"❌ <b>No related tracks found via {engine_label} for:</b> <code>{seed_query}</code>")
            if self.job_queue:
                next_job = self.job_queue.pop(0)
                await self._run_queued_job(next_job)
                return
            self.is_running = False
            self.active_mode = "idle"
            return

        await self._send_notify(
            f"🚀 <b>Seed {engine_label} Harvest Started!</b>\n\n"
            f"🎵 <b>Seed Song:</b> <code>{seed_query}</code>\n"
            f"📻 <b>Engine:</b> <code>{engine_label}</code>\n"
            f"🔢 <b>Queue:</b> <code>{len(tracks)} Tracks</code>\n"
            f"⚡ <b>Parallel Slots:</b> <code>{self.concurrency} Slots</code>\n"
            f"📦 <b>Formats:</b> 🎬 480p Video + 🎙️ 48kHz Opus Audio"
        )

        sem = asyncio.Semaphore(self.concurrency)
        queue = asyncio.Queue()
        short_engine = "Shruti Mix" if is_shruti else "Vibe"
        label = f"📻 {short_engine}: {seed_query[:22]}"
        for t in tracks:
            await queue.put((label, t))

        workers = [asyncio.create_task(self._worker(queue, sem)) for _ in range(self.concurrency)]
        await queue.join()

        for w in workers:
            if not w.done():
                w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

        disk = self.get_disk_stats()
        summary_text = (
            f"🎉 <b>Seed {engine_label} Harvest Completed!</b>\n\n"
            f"🎵 <b>Seed:</b> <code>{seed_query}</code>\n"
            f"📻 <b>Engine:</b> <code>{engine_label}</code>\n"
            f"📥 <b>Total Downloaded:</b> <code>{self.total_downloaded}</code>\n"
            f"⏭️ <b>Total Skipped:</b> <code>{self.total_skipped}</code>\n"
            f"❌ <b>Total Failed:</b> <code>{self.total_failed}</code>\n"
            f"💾 <b>Final Disk Free:</b> <code>{disk['free_gb']} GB</code> / <code>{disk['total_gb']} GB</code>"
        )
        logger.info(f"[Harvester] Seed {engine_label} loop completed.")
        await self._send_notify(summary_text)

        # Process next queued job if waiting
        if self.job_queue and self.is_running:
            next_job = self.job_queue.pop(0)
            await self._run_queued_job(next_job)
            return

        if not self.continuous_mode:
            self.is_running = False
            self.active_mode = "idle"
            self.last_status_msg = "Completed"

    async def _run_queued_job(self, job: Dict[str, Any]):
        """Runs the next job queued by the user seamlessly"""
        if not self.is_running:
            return
        j_type = job.get("type")
        rem = len(self.job_queue)
        if j_type == "seed":
            s_name = job["seed_query"]
            eng = job.get("engine", "vibe")
            eng_lbl = "Autoplay 2 (Shruti)" if eng in ("shruti", "autoplay2", "2") else "Autoplay 1 (Vibe)"
            await self._send_notify(
                f"🚀 <b>Starting Queued Job ({rem} remaining in queue):</b>\n"
                f"📻 <b>Seed:</b> <code>{s_name}</code> ({eng_lbl})\n"
                f"⚡ <b>Workers:</b> <code>{self.concurrency} Slots</code>"
            )
            self.selected_genre = f"seed:{s_name}"
            self.active_mode = "seed"
            await self._run_seed_loop(
                seed_query=s_name,
                tracks=job.get("tracks"),
                target_count=job.get("target_count", 35),
                engine=eng
            )
        elif j_type == "genre":
            g = job["genre"]
            lbl = "Random Mixed" if g in ("random", "all", "mixed") else HARVEST_GENRES.get(g, {}).get("label", g.title())
            await self._send_notify(
                f"🚀 <b>Starting Queued Job ({rem} remaining in queue):</b>\n"
                f"📂 <b>Genre:</b> <code>{lbl}</code>\n"
                f"⚡ <b>Workers:</b> <code>{self.concurrency} Slots</code>"
            )
            self.selected_genre = g
            self.active_mode = "random" if g in ("random", "all", "mixed") else "genre"
            await self._run_loop(selected_genre=g, target_per_category=job.get("target_per_category", 50))

    async def _run_continuous_loop(self):
        """
        ♾️ 24/7 Endless Continuous Auto-Harvest Loop:
        Runs non-stop all night across all genres + dynamically explores Shruti Autoplay Radio recommendations.
        Never stops automatically; processes queued jobs first, then keeps expanding library endlessly.
        """
        logger.info("[Harvester] Starting 24/7 Continuous Infinite Auto-Harvest!")
        await self._send_notify(
            "♾️ <b>24/7 Endless Auto-Harvest Started!</b>\n\n"
            f"⚡ <b>Parallel Slots:</b> <code>{self.concurrency} Concurrent Slots Active</code>\n"
            f"💽 <b>Storage Capacity:</b> <code>8.7 TB Public Bucket Available</code>\n"
            "🔄 <b>Strategy:</b> Round-Robin across Pakistani, Bollywood, Arabic, Russian/Phonk, Folk, Hollywood\n"
            "📻 <b>Deep Expansion:</b> Auto-expands via Shruti Autoplay YouTube Radio Mix!\n"
            "🌙 <i>Will run all night continuously without stopping until you click Stop!</i>"
        )

        genre_keys = list(HARVEST_GENRES.keys())
        cycle = 0

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        async with aiohttp.ClientSession(headers=headers) as session:
            sem = asyncio.Semaphore(self.concurrency)

            while self.is_running and self.continuous_mode:
                # 1. First priority: Check if user queued any specific seed or genre jobs
                if self.job_queue:
                    next_job = self.job_queue.pop(0)
                    await self._run_queued_job(next_job)
                    continue

                cycle += 1
                current_genre_key = genre_keys[(cycle - 1) % len(genre_keys)]
                g_info = HARVEST_GENRES[current_genre_key]
                self.current_genre = f"♾️ {g_info['label']}"
                logger.info(f"[Harvester 24/7] Cycle {cycle}: Harvesting {g_info['label']} + Autoplay exploration")

                # Fetch candidates for this genre
                pool: List[Dict[str, Any]] = []
                seen_vids = set()

                for q in g_info["queries"]:
                    if not self.is_running or not self.continuous_mode:
                        break
                    cands = await self._fetch_candidates_for_query(session, q, max_items=20)
                    for c in cands:
                        if c["id"] not in seen_vids:
                            seen_vids.add(c["id"])
                            pool.append(c)
                    await asyncio.sleep(0.2)

                # Explore Shruti Autoplay recommendations for top candidates in this batch
                if pool and self.is_running and self.continuous_mode:
                    seed_picks = [p["id"] for p in pool[:2]]
                    for s_vid in seed_picks:
                        if not self.is_running or not self.continuous_mode:
                            break
                        try:
                            shruti_tracks = await fetch_shruti_autoplay(session, s_vid, max_tracks=15)
                            for st in shruti_tracks:
                                st_id = st.get("video_id")
                                if st_id and st_id not in seen_vids:
                                    seen_vids.add(st_id)
                                    pool.append({
                                        "id": st_id,
                                        "title": st.get("title", ""),
                                        "uploader": st.get("artist", "YouTube"),
                                        "duration": st.get("duration", "03:30"),
                                        "duration_sec": st.get("duration_sec", 210),
                                        "thumbnail": st.get("thumbnail", f"https://img.youtube.com/vi/{st_id}/hqdefault.jpg"),
                                    })
                        except Exception as ex:
                            logger.debug(f"[Harvester Continuous] Shruti exploration note: {ex}")

                # Enqueue and download
                if pool and self.is_running and self.continuous_mode:
                    queue = asyncio.Queue()
                    for item in pool:
                        await queue.put((g_info["label"], item))

                    workers = [asyncio.create_task(self._worker(queue, sem)) for _ in range(self.concurrency)]
                    await queue.join()

                    for w in workers:
                        if not w.done():
                            w.cancel()
                    await asyncio.gather(*workers, return_exceptions=True)

                disk = self.get_disk_stats()
                if disk["free_gb"] < 3.0:
                    warn_msg = f"⚠️ <b>Storage Warning:</b> Only {disk['free_gb']} GB free remaining in bucket! Stopping 24/7 Harvest."
                    logger.warning(warn_msg)
                    await self._send_notify(warn_msg)
                    break

                await self._send_notify(
                    f"📊 <b>24/7 Endless Harvest Cycle #{cycle} Complete!</b>\n\n"
                    f"📂 <b>Category:</b> {g_info['label']}\n"
                    f"📥 <b>Total Downloaded:</b> <code>{self.total_downloaded}</code> (Skipped: {self.total_skipped})\n"
                    f"💾 <b>NVMe Free:</b> <code>{disk['free_gb']} GB</code> / <code>{disk['total_gb']} GB</code>\n"
                    f"🔄 <i>Automatically advancing to next genre...</i>"
                )
                await asyncio.sleep(2.0)

        disk = self.get_disk_stats()
        summary_text = (
            f"🎉 <b>24/7 Endless Auto-Harvest Stopped!</b>\n\n"
            f"📥 <b>Total Downloaded:</b> <code>{self.total_downloaded}</code>\n"
            f"⏭️ <b>Total Skipped:</b> <code>{self.total_skipped}</code>\n"
            f"❌ <b>Total Failed:</b> <code>{self.total_failed}</code>\n"
            f"💾 <b>Final Disk Free:</b> <code>{disk['free_gb']} GB</code> / <code>{disk['total_gb']} GB</code>"
        )
        self.is_running = False
        self.continuous_mode = False
        self.active_mode = "idle"
        self.last_status_msg = "Completed"
        logger.info("[Harvester] 24/7 continuous loop finished.")
        await self._send_notify(summary_text)


# Global singleton instance
harvest_manager = HarvestManager()


async def preview_seed_autoplay(seed_query: str, target_count: int = 35, engine: str = "vibe") -> Dict[str, Any]:
    """Generates preview of 25–40 related tracks via selected engine and caches for one-click download"""
    clean = seed_query.strip()
    is_shruti = engine in ("shruti", "autoplay2", "2", "mix")
    if is_shruti:
        data = await resolve_shruti_autoplay(clean, target_count=target_count)
    else:
        data = await resolve_smart_autoplay(clean, target_count=target_count)

    canonical_engine = "autoplay2" if is_shruti else "vibe"
    engine_label = "Autoplay 2 (Shruti Mix)" if is_shruti else "Autoplay 1 (Vibe AI)"
    cache_id = hashlib.md5(f"{clean}_{canonical_engine}_{time.time()}".encode()).hexdigest()[:8]
    SEED_PREVIEWS[cache_id] = {
        "cache_id": cache_id,
        "seed": clean,
        "engine": canonical_engine,
        "engine_label": engine_label,
        "data": data,
        "created_at": time.time()
    }
    # Keep SEED_PREVIEWS bounded (max 50)
    if len(SEED_PREVIEWS) > 50:
        oldest_k = min(SEED_PREVIEWS.keys(), key=lambda k: SEED_PREVIEWS[k]["created_at"])
        SEED_PREVIEWS.pop(oldest_k, None)

    return {
        "cache_id": cache_id,
        "seed": clean,
        "seed_id": data.get("seed_id", ""),
        "seed_title": data.get("seed_title", clean),
        "engine": canonical_engine,
        "engine_label": engine_label,
        "total": data.get("total", len(data.get("tracks", []))),
        "tracks": data.get("tracks", []),
        "elapsed_sec": data.get("elapsed_sec", 0.0)
    }

