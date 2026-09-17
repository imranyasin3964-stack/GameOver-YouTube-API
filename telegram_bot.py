import os
import asyncio
import aiohttp
import json
import logging
import time
import urllib.parse
import re
from typing import Optional, Dict, Any, List

import controller_db
from cache_manager import get_cache_stats
from config import BASE_URL, PORT, CACHE_DIR

logger = logging.getLogger("GameOverAPI.TelegramBot")

BOT_TOKEN = "8718878406:AAGOPBTJw1XQv45i5RBf01fGbpHfHbAbM5k"
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
OWNER_ID = 6805412676
OWNER_HANDLE = "@XHamsterFounders"

# Session state for interactive button input (e.g. setting custom limit or test mode)
USER_STATES: Dict[int, Dict[str, Any]] = {}


def get_main_keyboard() -> dict:
    """Mobile-friendly 9-Button Grid with Close button and non-sticky keyboard"""
    return {
        "keyboard": [
            [{"text": "⚡ API Eɴᴅᴘᴏɪɴᴛs"}, {"text": "🔍 Tᴇsᴛ Sᴇᴀʀᴄʜ"}],
            [{"text": "📊 Sᴛᴀᴛs"}, {"text": "🌐 IPs Lɪsᴛ"}],
            [{"text": "🚫 Bʟᴏᴄᴋ Mᴀɴᴀɢᴇʀ"}, {"text": "⏱️ Lɪᴍɪᴛ Mᴀɴᴀɢᴇʀ"}],
            [{"text": "👥 Aᴅᴍɪɴs"}, {"text": "🧹 Cʟᴇᴀʀ Oʟᴅ Lᴏɢs"}],
            [{"text": "❌ Cʟᴏsᴇ Mᴇɴᴜ"}],
        ],
        "resize_keyboard": True,
    }


async def call_tg(method: str, payload: dict) -> Optional[dict]:
    """Helper to make Telegram Bot API requests via aiohttp"""
    url = f"{TELEGRAM_API_URL}/{method}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15.0)) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logger.warning(f"[TelegramAPI] Error in {method}: {data.get('description')}")
                return data
    except Exception as e:
        logger.error(f"[TelegramAPI] Request failed for {method}: {e}")
        return None


async def send_msg(chat_id: int, text: str, reply_markup: Optional[dict] = None, track: bool = True) -> Optional[int]:
    """Sends HTML formatted message to Telegram user and tracks message ID"""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    res = await call_tg("sendMessage", payload)
    if res and res.get("ok"):
        msg_id = res["result"]["message_id"]
        if track:
            controller_db.track_bot_message(chat_id, msg_id)
        return msg_id
    return None


async def edit_msg(chat_id: int, message_id: int, text: str, reply_markup: Optional[dict] = None) -> bool:
    """Edits an existing Telegram message with HTML formatting"""
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    res = await call_tg("editMessageText", payload)
    return bool(res and res.get("ok"))


async def delete_msg(chat_id: int, message_id: int) -> bool:
    res = await call_tg("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    return bool(res and res.get("ok"))


async def send_photo_msg(
    chat_id: int,
    photo_url: str,
    caption: str,
    reply_markup: Optional[dict] = None,
    video_id: Optional[str] = None
) -> Optional[int]:
    """
    Guaranteed 16:9 Photo Delivery:
    1. If local thumbnail file exists on disk in CACHE_DIR, upload directly via multipart/form-data.
    2. Try YouTube official Google CDN 16:9 MaxRes (https://i.ytimg.com/vi/{id}/maxresdefault.jpg).
    3. Try YouTube SD/HQ CDN (sddefault.jpg / hqdefault.jpg).
    4. Try photo_url if it's a valid external HTTPS URL.
    5. Fallback to text message only as last resort.
    """
    # Auto-extract video_id if not provided
    if not video_id:
        if "vi/" in photo_url:
            try:
                video_id = photo_url.split("vi/")[1].split("/")[0]
            except Exception:
                pass
        elif "thumb_" in photo_url:
            try:
                video_id = photo_url.split("thumb_")[1].split(".")[0]
            except Exception:
                pass

    # Tier 1: Check local cache on disk (100% reliable multipart upload)
    if video_id:
        local_thumb = CACHE_DIR / f"thumb_{video_id}.jpg"
        if local_thumb.is_file() and local_thumb.stat().st_size > 500:
            try:
                url = f"{TELEGRAM_API_URL}/sendPhoto"
                form = aiohttp.FormData()
                form.add_field("chat_id", str(chat_id))
                form.add_field("caption", caption)
                form.add_field("parse_mode", "HTML")
                if reply_markup:
                    form.add_field("reply_markup", json.dumps(reply_markup))
                with open(local_thumb, "rb") as f:
                    form.add_field("photo", f, filename=f"thumb_{video_id}.jpg", content_type="image/jpeg")
                    async with aiohttp.ClientSession() as session:
                        async with session.post(url, data=form, timeout=aiohttp.ClientTimeout(total=20.0)) as resp:
                            res = await resp.json()
                            if res and res.get("ok"):
                                msg_id = res["result"]["message_id"]
                                controller_db.track_bot_message(chat_id, msg_id)
                                return msg_id
            except Exception as e:
                logger.debug(f"Local thumbnail multipart upload failed for {video_id}: {e}")

    # Tier 2: Google CDN 16:9 MaxRes, SD, HQ URLs
    cdn_urls = []
    if video_id:
        cdn_urls.extend([
            f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
            f"https://i.ytimg.com/vi/{video_id}/sddefault.jpg",
            f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        ])
    if photo_url and photo_url.startswith("https://") and photo_url not in cdn_urls:
        cdn_urls.append(photo_url)

    for cdn_u in cdn_urls:
        try:
            payload = {
                "chat_id": chat_id,
                "photo": cdn_u,
                "caption": caption,
                "parse_mode": "HTML",
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup
            res = await call_tg("sendPhoto", payload)
            if res and res.get("ok"):
                msg_id = res["result"]["message_id"]
                controller_db.track_bot_message(chat_id, msg_id)
                return msg_id
        except Exception as e:
            logger.debug(f"CDN sendPhoto failed for {cdn_u}: {e}")

    # Tier 3: Text fallback
    fallback_text = f"🎵 {caption}"
    return await send_msg(chat_id, fallback_text, reply_markup=reply_markup)


async def send_audio_file(chat_id: int, file_path: str, title: str, performer: str, duration: int, caption: str) -> bool:
    """Uploads local MP3 file directly into Telegram chat via multipart/form-data"""
    if not os.path.exists(file_path):
        return False
    url = f"{TELEGRAM_API_URL}/sendAudio"
    data = aiohttp.FormData()
    data.add_field("chat_id", str(chat_id))
    data.add_field("title", title)
    data.add_field("performer", performer)
    data.add_field("duration", str(duration))
    data.add_field("caption", caption)
    data.add_field("parse_mode", "HTML")
    with open(file_path, "rb") as f:
        data.add_field("audio", f, filename=os.path.basename(file_path), content_type="audio/mpeg")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=180.0)) as resp:
                    res = await resp.json()
                    return bool(res and res.get("ok"))
            except Exception as e:
                logger.error(f"Failed to upload audio to Telegram: {e}")
                return False


async def send_video_file(chat_id: int, file_path: str, caption: str) -> bool:
    """Uploads local MP4 file directly into Telegram chat (if under 50MB)"""
    if not os.path.exists(file_path):
        return False
    size_mb = os.path.getsize(file_path) / (1024 * 1024)
    if size_mb > 49.0:
        logger.info(f"Video {file_path} ({size_mb:.1f}MB) exceeds Telegram 50MB limit.")
        return False
    url = f"{TELEGRAM_API_URL}/sendVideo"
    data = aiohttp.FormData()
    data.add_field("chat_id", str(chat_id))
    data.add_field("caption", caption)
    data.add_field("parse_mode", "HTML")
    data.add_field("supports_streaming", "true")
    with open(file_path, "rb") as f:
        data.add_field("video", f, filename=os.path.basename(file_path), content_type="video/mp4")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=240.0)) as resp:
                    res = await resp.json()
                    return bool(res and res.get("ok"))
            except Exception as e:
                logger.error(f"Failed to upload video to Telegram: {e}")
                return False


async def download_and_upload_audio(chat_id: int, video_id: str, title: str = ""):
    """Downloads audio via local engine and uploads directly into Telegram chat"""
    progress_msg_id = await send_msg(
        chat_id,
        f"⏳ <b>Dᴏᴡɴʟᴏᴀᴅɪɴɢ Aᴜᴅɪᴏ...</b>\n🎵 <i>{title or video_id}</i>\nPʟᴇᴀsᴇ ᴡᴀɪᴛ ᴀ ғᴇᴡ sᴇᴄᴏɴᴅs..."
    )
    url = f"http://127.0.0.1:{PORT}/download?type=audio&url=https://www.youtube.com/watch?v={video_id}"
    data = None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60.0)) as resp:
                if resp.status == 200:
                    data = await resp.json()
    except Exception as e:
        logger.error(f"Download audio error: {e}")

    if not data or data.get("status") != "success":
        err_msg = data.get("detail", "Download failed") if isinstance(data, dict) else "Download failed"
        await edit_msg(chat_id, progress_msg_id, f"❌ <b>Dᴏᴡɴʟᴏᴀᴅ Fᴀɪʟᴇᴅ:</b> <code>{err_msg}</code>")
        return

    filename = data.get("filename")
    local_file = CACHE_DIR / filename if filename else None
    stream_url = data.get("stream_url", "")
    elapsed = data.get("elapsed_sec", 0.0)
    dur_str = data.get("duration", "00:00")
    dur_sec = data.get("duration_sec", 0)
    song_title = data.get("title", title)
    uploader = data.get("uploader", "YouTube")

    if progress_msg_id:
        await edit_msg(chat_id, progress_msg_id, f"📤 <b>Uᴘʟᴏᴀᴅɪɴɢ Aᴜᴅɪᴏ ᴛᴏ Tᴇʟᴇɢʀᴀᴍ...</b>\n🎵 <i>{song_title}</i>")

    uploaded = False
    if local_file and local_file.exists():
        caption = (
            f"🎵 <b>{song_title}</b>\n\n"
            f"⏱️ <b>Dᴜʀᴀᴛɪᴏɴ:</b> <code>{dur_str}</code> | ⚡ <b>Sᴘᴇᴇᴅ:</b> <code>{elapsed}s</code>\n"
            f"🔗 <b>Sᴛʀᴇᴀᴍ URL:</b> <code>{stream_url}</code>\n"
            f"👨‍💻 <b>Dᴇᴠᴇʟᴏᴘᴇʀ:</b> {OWNER_HANDLE}"
        )
        uploaded = await send_audio_file(
            chat_id=chat_id,
            file_path=str(local_file),
            title=song_title,
            performer=uploader,
            duration=dur_sec,
            caption=caption,
        )

    if not uploaded:
        text = (
            f"🎵 <b>Aᴜᴅɪᴏ Rᴇᴀᴅʏ!</b>\n\n"
            f"🎵 <b>Tɪᴛʟᴇ:</b> <code>{song_title}</code>\n"
            f"⏱️ <b>Dᴜʀᴀᴛɪᴏɴ:</b> <code>{dur_str}</code> | ⚡ <b>Sᴘᴇᴇᴅ:</b> <code>{elapsed}s</code>\n"
            f"🔗 <b>Sᴛʀᴇᴀᴍ URL:</b>\n<code>{stream_url}</code>\n\n"
            f"👨‍💻 <b>Dᴇᴠᴇʟᴏᴘᴇʀ:</b> {OWNER_HANDLE}"
        )
        await send_msg(chat_id, text)

    # Double response: copyable raw JSON
    json_str = json.dumps(data, indent=2)
    await send_msg(chat_id, f"📄 <b>Rᴇsᴘᴏɴsᴇ JSOɴ (Cᴏᴘʏᴀʙʟᴇ):</b>\n<pre><code class=\"language-json\">{json_str}</code></pre>")

    if progress_msg_id:
        await delete_msg(chat_id, progress_msg_id)


async def ask_video_quality(chat_id: int, video_id: str, title: str = ""):
    """Prompts user to select video resolution (720p, 480p, 360p)"""
    text = (
        f"🎬 <b>Sᴇʟᴇᴄᴛ Vɪᴅᴇᴏ Qᴜᴀʟɪᴛʏ:</b>\n\n"
        f"🎵 <b>Tɪᴛʟᴇ:</b> <code>{title or video_id}</code>\n\n"
        f"Cʟɪᴄᴋ ʏᴏᴜʀ ᴘʀᴇғᴇʀʀᴇᴅ ʀᴇsᴏʟᴜᴛɪᴏɴ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ &amp; ᴜᴘʟᴏᴀᴅ:"
    )
    inline_kb = {
        "inline_keyboard": [
            [
                {"text": "🎬 720p (HD)", "callback_data": f"dl_vid:720:{video_id}"},
                {"text": "🎬 480p (SD)", "callback_data": f"dl_vid:480:{video_id}"},
            ],
            [
                {"text": "🎬 360p (Fast)", "callback_data": f"dl_vid:360:{video_id}"},
                {"text": "🔙 Bᴀᴄᴋ", "callback_data": f"dl_cancel:{video_id}"},
            ],
        ]
    }
    await send_msg(chat_id, text, reply_markup=inline_kb)


async def download_and_upload_video(chat_id: int, video_id: str, quality: str = "720", title: str = ""):
    """Downloads video with requested quality and uploads directly to Telegram"""
    progress_msg_id = await send_msg(
        chat_id,
        f"⏳ <b>Dᴏᴡɴʟᴏᴀᴅɪɴɢ Vɪᴅᴇᴏ ({quality}p)...</b>\n🎵 <i>{title or video_id}</i>\nPʟᴇᴀsᴇ ᴡᴀɪᴛ ᴀ ғᴇᴡ sᴇᴄᴏɴᴅs..."
    )
    url = f"http://127.0.0.1:{PORT}/download?type=video&quality={quality}&url=https://www.youtube.com/watch?v={video_id}"
    data = None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=120.0)) as resp:
                if resp.status == 200:
                    data = await resp.json()
    except Exception as e:
        logger.error(f"Download video error: {e}")

    if not data or data.get("status") != "success":
        err_msg = data.get("detail", "Download failed") if isinstance(data, dict) else "Download failed"
        await edit_msg(chat_id, progress_msg_id, f"❌ <b>Dᴏᴡɴʟᴏᴀᴅ Fᴀɪʟᴇᴅ:</b> <code>{err_msg}</code>")
        return

    filename = data.get("filename")
    local_file = CACHE_DIR / filename if filename else None
    stream_url = data.get("stream_url", "")
    elapsed = data.get("elapsed_sec", 0.0)
    dur_str = data.get("duration", "00:00")
    vid_title = data.get("title", title)

    if progress_msg_id:
        await edit_msg(chat_id, progress_msg_id, f"📤 <b>Uᴘʟᴏᴀᴅɪɴɢ Vɪᴅᴇᴏ ({quality}p) ᴛᴏ Tᴇʟᴇɢʀᴀᴍ...</b>\n🎵 <i>{vid_title}</i>")

    uploaded = False
    if local_file and local_file.exists():
        caption = (
            f"🎬 <b>{vid_title}</b> ({quality}p)\n\n"
            f"⏱️ <b>Dᴜʀᴀᴛɪᴏɴ:</b> <code>{dur_str}</code> | ⚡ <b>Sᴘᴇᴇᴅ:</b> <code>{elapsed}s</code>\n"
            f"🔗 <b>Sᴛʀᴇᴀᴍ URL:</b> <code>{stream_url}</code>\n"
            f"👨‍💻 <b>Dᴇᴠᴇʟᴏᴘᴇʀ:</b> {OWNER_HANDLE}"
        )
        uploaded = await send_video_file(chat_id=chat_id, file_path=str(local_file), caption=caption)

    if not uploaded:
        size_str = ""
        if local_file and local_file.exists():
            size_mb = os.path.getsize(local_file) / (1024 * 1024)
            size_str = f"⚠️ <i>Fɪʟᴇ sɪᴢᴇ ({size_mb:.1f} MB) ᴇxᴄᴇᴇᴅs Tᴇʟᴇɢʀᴀᴍ's 50MB ʙᴏᴛ ᴜᴘʟᴏᴀᴅ ʟɪᴍɪᴛ.</i>\n\n"
        text = (
            f"🎬 <b>Vɪᴅᴇᴏ Rᴇᴀᴅʏ ({quality}p)!</b>\n\n"
            f"🎵 <b>Tɪᴛʟᴇ:</b> <code>{vid_title}</code>\n"
            f"⏱️ <b>Dᴜʀᴀᴛɪᴏɴ:</b> <code>{dur_str}</code> | ⚡ <b>Sᴘᴇᴇᴅ:</b> <code>{elapsed}s</code>\n\n"
            f"{size_str}"
            f"🔗 <b>Wᴀᴛᴄʜ / Dᴏᴡɴʟᴏᴀᴅ Dɪʀᴇᴄᴛʟʏ:</b>\n<code>{stream_url}</code>\n\n"
            f"👨‍💻 <b>Dᴇᴠᴇʟᴏᴘᴇʀ:</b> {OWNER_HANDLE}"
        )
        await send_msg(chat_id, text)

    # Double response: copyable raw JSON
    json_str = json.dumps(data, indent=2)
    await send_msg(chat_id, f"📄 <b>Rᴇsᴘᴏɴsᴇ JSOɴ (Cᴏᴘʏᴀʙʟᴇ):</b>\n<pre><code class=\"language-json\">{json_str}</code></pre>")

    if progress_msg_id:
        await delete_msg(chat_id, progress_msg_id)


async def broadcast_api_log(log_data: dict):
    """
    Broadcasts real-time API logs to all registered admins with inline action buttons.
    """
    ip = log_data.get("ip", "Unknown")
    query = log_data.get("query", "Unknown")
    m_type = log_data.get("type", "audio")
    quality = log_data.get("quality", "default")
    cached = "Yᴇs" if log_data.get("cached") else "Nᴏ"
    elapsed = log_data.get("elapsed_sec", 0.0)
    title = log_data.get("title", query)

    # Format type line with appropriate icon and label
    m_type_lower = m_type.lower()
    if m_type_lower == "search":
        type_icon = "🔍"
        type_label = "Sᴇᴀʀᴄʜ (Fast Meta)"
    elif m_type_lower == "playlist":
        type_icon = "📑"
        type_label = f"Pʟᴀʏʟɪsᴛ ({quality})"
    elif m_type_lower == "video":
        type_icon = "🎬"
        type_label = f"Vɪᴅᴇᴏ ({quality})"
    else:
        type_icon = "🎵"
        type_label = f"Aᴜᴅɪᴏ ({quality})"

    # Format copyable monospace JSON payload (limited to 750 chars)
    json_str = json.dumps(log_data.get("response", {}), indent=2)
    if len(json_str) > 750:
        json_str = json_str[:750] + "\n  ...\n}"

    text = (
        f"🚀 <b>Nᴇᴡ API Rᴇǫᴜᴇsᴛ</b>\n\n"
        f"🌐 <b>IP:</b> <code>{ip}</code>\n"
        f"🎵 <b>Tɪᴛʟᴇ:</b> <code>{title}</code>\n"
        f"📁 <b>Tʏᴘᴇ:</b> {type_icon} <code>{type_label}</code>\n"
        f"⚡ <b>Cᴀᴄʜᴇᴅ:</b> <code>{cached}</code> | ⏱️ <b>Tɪᴍᴇ:</b> <code>{elapsed}s</code>\n\n"
        f"📄 <b>Rᴇsᴘᴏɴsᴇ JSOɴ:</b>\n"
        f"<pre><code class=\"language-json\">{json_str}</code></pre>"
    )

    # Inline buttons for instant action
    ip_info = controller_db.get_ip_info(ip)
    is_blocked = bool(ip_info and ip_info.get("is_blocked"))
    btn_text = f"✅ Uɴʙʟᴏᴄᴋ {ip}" if is_blocked else f"🚫 Bʟᴏᴄᴋ {ip}"
    btn_cb = f"toggle_block:{ip}"

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": btn_text, "callback_data": btn_cb},
                {"text": "ℹ️ IP Iɴғᴏ", "callback_data": f"ip_menu:{ip}"}
            ],
            [{"text": f"⏱️ Lɪᴍɪᴛ {ip}", "callback_data": f"select_limit:{ip}"}],
        ]
    }

    admins = controller_db.get_all_admins()
    for a in admins:
        try:
            await send_msg(a["user_id"], text, reply_markup=reply_markup, track=True)
        except Exception as e:
            logger.debug(f"Failed to send log to admin {a['user_id']}: {e}")


def get_flag_emoji(country_code: str) -> str:
    """Converts 2-letter ISO country code into Unicode flag emoji."""
    if not country_code or len(country_code) != 2:
        return "🌐"
    try:
        return chr(127397 + ord(country_code[0].upper())) + chr(127397 + ord(country_code[1].upper()))
    except Exception:
        return "🌐"


async def fetch_ip_osint_info(ip: str) -> Optional[dict]:
    """
    Fetches comprehensive IP geolocation and network OSINT data from ip-api.com.
    Zero auth required, ultra-fast 0.15s response time.
    """
    url = f"http://ip-api.com/json/{ip}?fields=status,message,country,countryCode,region,regionName,city,zip,lat,lon,timezone,isp,org,as,mobile,proxy,hosting,query"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "success":
                        return data
    except Exception as e:
        logger.debug(f"[IP-OSINT] Lookup error for {ip}: {e}")
    return None


async def handle_ip_lookup(chat_id: int, ip_str: str, message_id_to_edit: Optional[int] = None):
    """
    Renders high-aesthetic OSINT inspection card for any IP with location, ISP, hosting/proxy flags,
    and internal API usage database hits.
    """
    ip_str = ip_str.strip()
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip_str):
        await send_msg(
            chat_id,
            "⚠️ <b>Iɴᴠᴀʟɪᴅ IP Aᴅᴅʀᴇss:</b>\nPʟᴇᴀsᴇ ᴘʀᴏᴠɪᴅᴇ ᴀ ᴠᴀʟɪᴅ IPv4.\n<i>Example:</i> <code>/ip 152.55.176.130</code>"
        )
        return

    loading_id = None
    if not message_id_to_edit:
        loading_id = await send_msg(chat_id, f"🔍 <b>Fᴇᴛᴄʜɪɴɢ OSINT Dᴀᴛᴀ ғᴏʀ</b> <code>{ip_str}</code>...")

    t_start = time.time()
    info = await fetch_ip_osint_info(ip_str) or {}
    elapsed = round(time.time() - t_start, 2)

    # Local database record
    db_info = controller_db.get_ip_info(ip_str) or {}
    is_blocked = bool(db_info.get("is_blocked"))
    hits = db_info.get("total_requests", 0)
    today_hits = db_info.get("today_requests", 0)
    limit_val = db_info.get("daily_limit", 0)
    limit_str = "Uɴʟɪᴍɪᴛᴇᴅ" if limit_val == 0 else f"{limit_val}/day"
    status_str = "🚫 Bʟᴏᴄᴋᴇᴅ" if is_blocked else "✅ Aᴄᴛɪᴠᴇ"

    cc = info.get("countryCode", "")
    flag = get_flag_emoji(cc)
    country = info.get("country", "Unknown")
    region = info.get("regionName", "Unknown")
    city = info.get("city", "Unknown")
    zip_code = info.get("zip", "N/A")
    lat = info.get("lat", 0.0)
    lon = info.get("lon", 0.0)
    tz = info.get("timezone", "Unknown")
    isp = info.get("isp", "Unknown")
    as_num = info.get("as", "Unknown")
    org = info.get("org", "Unknown")
    mobile = "📱 Yᴇs" if info.get("mobile") else "📴 Nᴏ"
    proxy = "🛡️ Yᴇs (VPN/Proxy)" if info.get("proxy") else "✅ Nᴏ"
    hosting = "🏢 Yᴇs (Datacenter)" if info.get("hosting") else "🏠 Nᴏ (Residential)"

    card_text = (
        f"┌ <b>G A M E O V E R</b>\n"
        f"└ <code>/ip {ip_str}</code>\n\n"
        f"🌐 <b>IP Iɴsᴘᴇᴄᴛᴏʀ &amp; OSINT Iɴғᴏ</b>\n\n"
        f"  📍 <b>L O C A T I O N</b>\n"
        f"• <b>IP:</b> <code>{ip_str}</code>\n"
        f"• <b>Cᴏᴜɴᴛʀʏ:</b> {flag} <code>{country} ({cc})</code>\n"
        f"• <b>Rᴇɢɪᴏɴ:</b> <code>{region}</code>\n"
        f"• <b>Cɪᴛʏ:</b> <code>{city}</code>\n"
        f"• <b>ZIP:</b> <code>{zip_code}</code>\n"
        f"• <b>Cᴏᴏʀᴅs:</b> <code>{lat}, {lon}</code>\n"
        f"• <b>Tɪᴍᴇᴢᴏɴᴇ:</b> <code>{tz}</code>\n\n"
        f"  📶 <b>N E T W O R K</b>\n"
        f"• <b>ISP:</b> <code>{isp}</code>\n"
        f"• <b>Oʀɢ:</b> <code>{org}</code>\n"
        f"• <b>AS:</b> <code>{as_num}</code>\n\n"
        f"  ⚡ <b>S E C U R I T Y   F L A G S</b>\n"
        f"• <b>Mᴏʙɪʟᴇ:</b> {mobile}\n"
        f"• <b>Pʀᴏxʏ / VPN:</b> {proxy}\n"
        f"• <b>Hᴏsᴛɪɴɢ / DC:</b> {hosting}\n\n"
        f"  📊 <b>API H I T S &amp; S T A T U S</b>\n"
        f"• <b>Sᴛᴀᴛᴜs:</b> <b>{status_str}</b>\n"
        f"• <b>Tᴏᴛᴀʟ Hɪᴛs:</b> <code>{hits}</code>\n"
        f"• <b>Tᴏᴅᴀʏ Hɪᴛs:</b> <code>{today_hits}</code>\n"
        f"• <b>Dᴀɪʟʏ Lɪᴍɪᴛ:</b> <code>{limit_str}</code>\n\n"
        f"⚡ <b>Rᴇsᴘᴏɴsᴇ Tɪᴍᴇ:</b> <code>{elapsed}s</code>\n"
        f"👨‍💻 <b>Dᴇᴠᴇʟᴏᴘᴇʀ:</b> {OWNER_HANDLE}"
    )

    block_btn_text = "✅ Uɴʙʟᴏᴄᴋ IP" if is_blocked else "🚫 Bʟᴏᴄᴋ IP"
    inline_kb = {
        "inline_keyboard": [
            [
                {"text": block_btn_text, "callback_data": f"toggle_block:{ip_str}"},
                {"text": "⏱️ Sᴇᴛ Lɪᴍɪᴛ", "callback_data": f"select_limit:{ip_str}"},
            ],
            [
                {"text": "🔄 Rᴇғʀᴇsʜ", "callback_data": f"ip_menu:{ip_str}"},
                {"text": "🌐 All IPs Lɪsᴛ", "callback_data": "ips_list_menu"},
            ]
        ]
    }

    if message_id_to_edit:
        await edit_msg(chat_id, message_id_to_edit, card_text, reply_markup=inline_kb)
    elif loading_id:
        ok = await edit_msg(chat_id, loading_id, card_text, reply_markup=inline_kb)
        if not ok:
            await delete_msg(chat_id, loading_id)
            await send_msg(chat_id, card_text, reply_markup=inline_kb)
    else:
        await send_msg(chat_id, card_text, reply_markup=inline_kb)


# Button click & text command handlers
async def handle_stats(chat_id: int):
    c_stats = get_cache_stats()
    req_stats = controller_db.get_total_request_stats()
    db_songs, db_hits = controller_db.get_cached_songs_count()
    
    text = (
        f"📊 <b>GᴀᴍᴇOᴠᴇʀ API Sᴛᴀᴛɪsᴛɪᴄs</b>\n\n"
        f"🌐 <b>Tᴏᴛᴀʟ Cʟɪᴇɴᴛ IPs:</b> <code>{req_stats['total_ips']}</code>\n"
        f"🔥 <b>Tᴏᴛᴀʟ Rᴇǫᴜᴇsᴛs:</b> <code>{req_stats['total_requests']}</code>\n"
        f"🚫 <b>Bʟᴏᴄᴋᴇᴅ IPs:</b> <code>{req_stats['blocked_ips']}</code>\n\n"
        f"🎵 <b>Cᴀᴄʜᴇᴅ Sᴏɴɢs (DB):</b> <code>{db_songs}</code>\n"
        f"⚡ <b>Iɴsᴛᴀɴᴛ Cᴀᴄʜᴇ Hɪᴛs:</b> <code>{db_hits}</code>\n"
        f"💾 <b>NVMe Fɪʟᴇs:</b> <code>{c_stats['cached_files_count']}</code>\n"
        f"📦 <b>Cᴀᴄʜᴇ Sɪᴢᴇ:</b> <code>{c_stats['cache_size_mb']} MB</code>\n"
        f"💽 <b>NVMe Fʀᴇᴇ:</b> <code>{c_stats['disk_free_gb']} GB / {c_stats['disk_total_gb']} GB</code>\n"
        f"🚀 <b>Pᴏʀᴛ:</b> <code>{PORT}</code> | <b>Bᴀsᴇ:</b> <code>{BASE_URL}</code>"
    )
    await send_msg(chat_id, text, reply_markup=get_main_keyboard())


async def handle_ips_list(chat_id: int):
    ips = controller_db.get_all_ips(limit=15)
    if not ips:
        await send_msg(chat_id, "ℹ️ Nᴏ ᴄʟɪᴇɴᴛ IPs ʀᴇᴄᴏʀᴅᴇᴅ ʏᴇᴛ.", reply_markup=get_main_keyboard())
        return

    text = "🌐 <b>Aᴄᴛɪᴠᴇ Cʟɪᴇɴᴛ IPs:</b>\nCʟɪᴄᴋ ᴏɴ ᴀɴʏ IP ʙᴇʟᴏᴡ ᴛᴏ ᴍᴀɴᴀɢᴇ:\n"
    buttons = []
    
    for r in ips:
        ip = r["ip"]
        hits = r["total_requests"]
        status = "🚫 Bʟᴏᴄᴋᴇᴅ" if r["is_blocked"] else f"✅ {hits} Hɪᴛs"
        limit_str = f"Limit: {r['daily_limit']}/day" if r["daily_limit"] > 0 else "Unlimited"
        btn_label = f"{ip} ({status} | {limit_str})"
        buttons.append([{"text": btn_label, "callback_data": f"ip_menu:{ip}"}])

    reply_markup = {"inline_keyboard": buttons}
    await send_msg(chat_id, text, reply_markup=reply_markup)


async def handle_block_manager(chat_id: int):
    ips = controller_db.get_all_ips(limit=20)
    blocked_ips = [r for r in ips if r["is_blocked"]]

    if not blocked_ips:
        text = "🚫 <b>Bʟᴏᴄᴋ Mᴀɴᴀɢᴇʀ</b>\n\nNᴏ IPs ᴀʀᴇ ᴄᴜʀʀᴇɴᴛʟʏ ʙʟᴏᴄᴋᴇᴅ. Yᴏᴜ ᴄᴀɴ ʙʟᴏᴄᴋ ᴀɴʏ IP ᴠɪᴀ <b>IPs Lɪsᴛ</b> ᴏʀ ʙʏ sᴇɴᴅɪɴɢ:\n<code>/block &lt;ip&gt;</code>"
        await send_msg(chat_id, text, reply_markup=get_main_keyboard())
        return

    text = "🚫 <b>Bʟᴏᴄᴋᴇᴅ IPs:</b>\nCʟɪᴄᴋ ᴛᴏ ᴜɴʙʟᴏᴄᴋ:\n"
    buttons = []
    for r in blocked_ips:
        ip = r["ip"]
        buttons.append([{"text": f"✅ Uɴʙʟᴏᴄᴋ {ip}", "callback_data": f"unblock:{ip}"}])

    reply_markup = {"inline_keyboard": buttons}
    await send_msg(chat_id, text, reply_markup=reply_markup)


async def handle_limit_manager(chat_id: int):
    ips = controller_db.get_all_ips(limit=15)
    text = "⏱️ <b>Lɪᴍɪᴛ Mᴀɴᴀɢᴇʀ</b>\nSᴇʟᴇᴄᴛ ᴀɴ IP ᴛᴏ sᴇᴛ ɪᴛs ᴅᴀɪʟʏ ʀᴇǫᴜᴇsᴛ ǫᴜᴏᴛᴀ:"
    buttons = []
    for r in ips:
        ip = r["ip"]
        cur_limit = r["daily_limit"] if r["daily_limit"] > 0 else "Uɴʟɪᴍɪᴛᴇᴅ"
        buttons.append([{"text": f"{ip} (Cᴜʀʀᴇɴᴛ: {cur_limit})", "callback_data": f"select_limit:{ip}"}])

    reply_markup = {"inline_keyboard": buttons}
    await send_msg(chat_id, text, reply_markup=reply_markup)


async def handle_admins_menu(chat_id: int):
    admins = controller_db.get_all_admins()
    text = "👥 <b>API Cᴏɴᴛʀᴏʟʟᴇʀ Aᴅᴍɪɴs:</b>\n\n"
    buttons = []

    for a in admins:
        u_id = a["user_id"]
        role = a["role"].upper()
        uname = f"@{a['username']}" if a["username"] else f"ID: {u_id}"
        text += f"• <b>{uname}</b> — <code>{role}</code>\n"
        if u_id != OWNER_ID:
            buttons.append([{"text": f"❌ Rᴇᴍᴏᴠᴇ {uname}", "callback_data": f"remove_admin:{u_id}"}])

    text += f"\nTᴏ ᴀᴅᴅ ᴀ ɴᴇᴡ ᴀᴅᴍɪɴ, sᴇɴᴅ:\n<code>/addadmin &lt;user_id&gt; &lt;viewer|editor&gt;</code>"
    reply_markup = {"inline_keyboard": buttons} if buttons else None
    await send_msg(chat_id, text, reply_markup=reply_markup)


async def handle_clear_old_logs(chat_id: int):
    expired = controller_db.get_expired_messages(max_age_hours=24.0)
    count = 0
    ids_to_del = []
    for m in expired:
        success = await delete_msg(m["chat_id"], m["message_id"])
        ids_to_del.append(m["id"])
        if success:
            count += 1

    controller_db.delete_tracked_messages_by_ids(ids_to_del)
    await send_msg(chat_id, f"🧹 <b>Cʟᴇᴀɴᴇᴅ {count} ᴏʟᴅ ʙᴏᴛ ᴍᴇssᴀɢᴇs</b> (older than 24 hours).", reply_markup=get_main_keyboard())


async def handle_api_endpoints(chat_id: int):
    clean_base = BASE_URL.rstrip("/")
    text = (
        f"⚡ <b>GᴀᴍᴇOᴠᴇʀ YouTube API Eɴᴅᴘᴏɪɴᴛs</b>\n\n"
        f"<i>All endpoints run with zero cookies and no web botguard. Tap any URL below to copy:</i>\n\n"
        f"🎵 <b>1. Aᴜᴅɪᴏ API (MP3 Stream)</b>\n"
        f"<code>{clean_base}/download?type=audio&amp;url=YOUR_SONG_OR_URL</code>\n"
        f"<i>Example:</i>\n<code>{clean_base}/download?type=audio&amp;url=tum+hi+ho</code>\n\n"
        f"🎬 <b>2. Vɪᴅᴇᴏ API (MP4 Stream)</b>\n"
        f"<code>{clean_base}/download?type=video&amp;quality=720&amp;url=YOUR_SONG_OR_URL</code>\n"
        f"<i>Qualities: 720, 480, 360</i>\n"
        f"<i>Example:</i>\n<code>{clean_base}/download?type=video&amp;quality=720&amp;url=fakira</code>\n\n"
        f"🔍 <b>3. Sᴇᴀʀᴄʜ API (Ultra-Fast 0.3s)</b>\n"
        f"<code>{clean_base}/search?query=YOUR_QUERY</code>\n"
        f"<i>Example:</i>\n<code>{clean_base}/search?query=fakira</code>\n\n"
        f"📑 <b>4. Pʟᴀʏʟɪsᴛ API (25 Songs + Thumbnails)</b>\n"
        f"<code>{clean_base}/playlist?url=PLAYLIST_URL</code>\n"
        f"<i>Example:</i>\n<code>{clean_base}/playlist?url=https://youtube.com/playlist?list=RDIuvVVWOsMBo</code>\n\n"
        f"💡 <i>Tᴀᴘ <b>📄 JSOɴ</b> ʙᴇʟᴏᴡ ᴛᴏ ᴠɪᴇᴡ ʀᴇᴀᴅʏ-ᴛᴏ-ᴄᴏᴘʏ ʀᴇsᴘᴏɴsᴇ ғᴏʀᴍᴀᴛs, ᴏʀ <b>Tᴇsᴛ</b> ᴛᴏ ʀᴜɴ ɪɴsᴛᴀɴᴛʟʏ!</i>"
    )
    inline_kb = {
        "inline_keyboard": [
            [{"text": "📄 Aᴜᴅɪᴏ JSOɴ", "callback_data": "sample_json:audio"}, {"text": "📄 Vɪᴅᴇᴏ JSOɴ", "callback_data": "sample_json:video"}],
            [{"text": "📄 Sᴇᴀʀᴄʜ JSOɴ", "callback_data": "sample_json:search"}, {"text": "📄 Pʟᴀʏʟɪsᴛ JSOɴ", "callback_data": "sample_json:playlist"}],
            [{"text": "🎵 Tᴇsᴛ Aᴜᴅɪᴏ", "callback_data": "test_prompt:audio"}, {"text": "🎬 Tᴇsᴛ Vɪᴅᴇᴏ", "callback_data": "test_prompt:video"}],
            [{"text": "🔍 Tᴇsᴛ Sᴇᴀʀᴄʜ", "callback_data": "test_prompt:search"}, {"text": "📑 Tᴇsᴛ Pʟᴀʏʟɪsᴛ", "callback_data": "test_prompt:playlist"}],
        ]
    }
    await send_msg(chat_id, text, reply_markup=inline_kb)


async def handle_endpoint_json_sample(chat_id: int, ep_type: str):
    clean_base = BASE_URL.rstrip("/")
    if ep_type == "audio":
        sample = {
            "status": "success",
            "id": "eJuoi13hbBc",
            "title": "Fakira - Lyrical | Student Of The Year 2 | Tiger Shroff, Tara & Ananya",
            "duration": "03:30",
            "duration_sec": 210,
            "thumbnail": "https://i.ytimg.com/vi/eJuoi13hbBc/hqdefault.jpg",
            "uploader": "Zee Music Company",
            "youtube_url": "https://www.youtube.com/watch?v=eJuoi13hbBc",
            "type": "audio",
            "quality": "320k",
            "filename": "audio_eJuoi13hbBc.mp3",
            "elapsed_sec": 0.45,
            "stream_url": f"{clean_base}/media/audio_eJuoi13hbBc.mp3",
            "developer": OWNER_HANDLE,
        }
        title = "🎵 Aᴜᴅɪᴏ API Rᴇsᴘᴏɴsᴇ JSOɴ"
        get_url = f"{clean_base}/download?type=audio&url=tum+hi+ho"
        test_cb = "quick_test:audio:fakira"
    elif ep_type == "video":
        sample = {
            "status": "success",
            "id": "eJuoi13hbBc",
            "title": "Fakira - Lyrical | Student Of The Year 2 | Tiger Shroff, Tara & Ananya",
            "duration": "03:30",
            "duration_sec": 210,
            "thumbnail": "https://i.ytimg.com/vi/eJuoi13hbBc/hqdefault.jpg",
            "uploader": "Zee Music Company",
            "youtube_url": "https://www.youtube.com/watch?v=eJuoi13hbBc",
            "type": "video",
            "quality": "720p",
            "filename": "video_eJuoi13hbBc.mp4",
            "elapsed_sec": 0.94,
            "stream_url": f"{clean_base}/media/video_eJuoi13hbBc.mp4",
            "developer": OWNER_HANDLE,
        }
        title = "🎬 Vɪᴅᴇᴏ API (720p) Rᴇsᴘᴏɴsᴇ JSOɴ"
        get_url = f"{clean_base}/download?type=video&quality=720&url=fakira"
        test_cb = "quick_test:video:fakira"
    elif ep_type == "search":
        sample = {
            "status": "success",
            "id": "eJuoi13hbBc",
            "title": "Fakira - Lyrical | Student Of The Year 2",
            "duration": "03:30",
            "duration_sec": 210,
            "thumbnail": f"{clean_base}/media/thumb_eJuoi13hbBc.jpg",
            "thumbnail_local": f"{clean_base}/media/thumb_eJuoi13hbBc.jpg",
            "thumbnail_remote": "https://i.ytimg.com/vi/eJuoi13hbBc/hqdefault.jpg",
            "uploader": "Zee Music Company",
            "youtube_url": "https://www.youtube.com/watch?v=eJuoi13hbBc",
            "results": [
                {
                    "id": "eJuoi13hbBc",
                    "title": "Fakira - Lyrical | Student Of The Year 2",
                    "duration": "03:30",
                    "duration_sec": 210,
                    "thumbnail": f"{clean_base}/media/thumb_eJuoi13hbBc.jpg",
                    "uploader": "Zee Music Company",
                    "youtube_url": "https://www.youtube.com/watch?v=eJuoi13hbBc"
                }
            ],
            "elapsed_sec": 0.32,
            "developer": OWNER_HANDLE,
        }
        title = "🔍 Sᴇᴀʀᴄʜ API Rᴇsᴘᴏɴsᴇ JSOɴ"
        get_url = f"{clean_base}/search?query=fakira"
        test_cb = "quick_test:search:fakira"
    else:
        sample = {
            "status": "success",
            "playlist_id": "RDIuvVVWOsMBo",
            "playlist_title": "YouTube Playlist",
            "total_items": 25,
            "items": [
                {
                    "index": 1,
                    "id": "IuvVVWOsMBo",
                    "title": "Ek Toh Kum Zindagani",
                    "duration": "03:45",
                    "duration_sec": 225,
                    "thumbnail": f"{clean_base}/media/thumb_IuvVVWOsMBo.jpg",
                    "thumbnail_local": f"{clean_base}/media/thumb_IuvVVWOsMBo.jpg",
                    "thumbnail_remote": "https://i.ytimg.com/vi/IuvVVWOsMBo/hqdefault.jpg",
                    "uploader": "T-Series",
                    "youtube_url": "https://www.youtube.com/watch?v=IuvVVWOsMBo"
                }
            ],
            "indexes": {
                "index_1": {
                    "index": 1,
                    "id": "IuvVVWOsMBo",
                    "title": "Ek Toh Kum Zindagani",
                    "duration": "03:45",
                    "duration_sec": 225,
                    "thumbnail": f"{clean_base}/media/thumb_IuvVVWOsMBo.jpg",
                    "uploader": "T-Series",
                    "youtube_url": "https://www.youtube.com/watch?v=IuvVVWOsMBo"
                }
            },
            "elapsed_sec": 1.15,
            "developer": OWNER_HANDLE,
        }
        title = "📑 Pʟᴀʏʟɪsᴛ API Rᴇsᴘᴏɴsᴇ JSOɴ"
        get_url = f"{clean_base}/playlist?url=https://youtube.com/playlist?list=RDIuvVVWOsMBo"
        test_cb = "quick_test:playlist:RDIuvVVWOsMBo"

    json_code = json.dumps(sample, indent=2)
    escaped_url = get_url.replace("&", "&amp;")
    text = (
        f"<b>{title}</b>\n\n"
        f"🔗 <b>GET URL (Tᴀᴘ ᴛᴏ Cᴏᴘʏ):</b>\n"
        f"<code>{escaped_url}</code>\n\n"
        f"📄 <b>Rᴇsᴘᴏɴsᴇ JSOɴ (Cᴏᴘʏᴀʙʟᴇ):</b>\n"
        f"<pre><code class=\"language-json\">{json_code}</code></pre>"
    )
    test_btn = {"text": f"🚀 Tᴇsᴛ {ep_type.capitalize()} Nᴏᴡ", "callback_data": test_cb}
    await send_msg(chat_id, text, reply_markup={"inline_keyboard": [[test_btn]]})


async def handle_test_search_menu(chat_id: int):
    clean_base = BASE_URL.rstrip("/")
    text = (
        f"🔍 <b>API Tᴇsᴛᴇʀ &amp; Sᴇᴀʀᴄʜ Rᴜɴɴᴇʀ</b>\n\n"
        f"Yᴏᴜ ᴄᴀɴ ᴛᴇsᴛ ᴀɴʏ API ʀɪɢʜᴛ ʜᴇʀᴇ ᴡɪᴛʜᴏᴜᴛ ᴏᴘᴇɴɪɴɢ Cʜʀᴏᴍᴇ!\n\n"
        f"👉 <b>Jᴜsᴛ sᴇɴᴅ ᴀɴʏ ᴏғ ᴛʜᴇ ғᴏʟʟᴏᴡɪɴɢ ɪɴ ᴄʜᴀᴛ:</b>\n"
        f"• <b>Sᴏɴɢ Nᴀᴍᴇ:</b> <code>Fakira Student Of The Year</code>\n"
        f"• <b>YᴏᴜTᴜʙᴇ Lɪɴᴋ:</b> <code>https://www.youtube.com/watch?v=eJuoi13hbBc</code>\n"
        f"• <b>Pʟᴀʏʟɪsᴛ Lɪɴᴋ:</b> <code>https://youtube.com/playlist?list=RDIuvVVWOsMBo</code>\n"
        f"• <b>API Lɪɴᴋ:</b> <code>{clean_base}/playlist?url=...</code>\n\n"
        f"⚡ <i>Oʀ ᴛᴀᴘ ᴀ ǫᴜɪᴄᴋ ᴛᴇsᴛ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴛᴏ ʀᴜɴ ᴀɴ ɪɴsᴛᴀɴᴛ ᴛᴇsᴛ:</i>"
    )
    inline_kb = {
        "inline_keyboard": [
            [{"text": "🎵 Quick Audio (Fakira)", "callback_data": "quick_test:audio:fakira"}, {"text": "🎬 Quick Video (Fakira)", "callback_data": "quick_test:video:fakira"}],
            [{"text": "🔍 Quick Search (Fakira)", "callback_data": "quick_test:search:fakira"}, {"text": "📑 Quick Playlist", "callback_data": "quick_test:playlist:RDIuvVVWOsMBo"}],
        ]
    }
    await send_msg(chat_id, text, reply_markup=inline_kb)


async def execute_api_test(chat_id: int, input_text: str, forced_mode: Optional[str] = None):
    input_text = input_text.strip()
    if not input_text:
        return

    # Send immediate Searching status message
    loading_msg_id = await send_msg(chat_id, "🔍 <b>Sᴇᴀʀᴄʜɪɴɢ... Pʟᴇᴀsᴇ ᴡᴀɪᴛ...</b>")

    api_label = "Search"
    endpoint_path = ""

    # Case 1: User pasted a full HTTP/HTTPS URL
    if input_text.startswith("http://") or input_text.startswith("https://"):
        parsed = urllib.parse.urlparse(input_text)
        if "/playlist" in parsed.path:
            api_label = "Playlist"
            endpoint_path = f"{parsed.path}?{parsed.query}"
        elif "/download" in parsed.path:
            api_label = "Video" if "type=video" in parsed.query else "Audio"
            endpoint_path = f"{parsed.path}?{parsed.query}"
        elif "/search" in parsed.path:
            api_label = "Search"
            endpoint_path = f"{parsed.path}?{parsed.query}"
        elif "youtube.com/playlist" in input_text or "list=" in input_text:
            api_label = "Playlist"
            endpoint_path = f"/playlist?url={urllib.parse.quote(input_text, safe='')}"
        else:
            if forced_mode == "video":
                api_label = "Video (720p)"
                endpoint_path = f"/download?type=video&quality=720&url={urllib.parse.quote(input_text, safe='')}"
            elif forced_mode == "audio":
                api_label = "Audio (MP3)"
                endpoint_path = f"/download?type=audio&url={urllib.parse.quote(input_text, safe='')}"
            else:
                api_label = "Search"
                endpoint_path = f"/search?query={urllib.parse.quote(input_text, safe='')}"
    else:
        # Case 2: Song query
        if forced_mode == "video":
            api_label = "Video (720p)"
            endpoint_path = f"/download?type=video&quality=720&url={urllib.parse.quote(input_text, safe='')}"
        elif forced_mode == "audio":
            api_label = "Audio (MP3)"
            endpoint_path = f"/download?type=audio&url={urllib.parse.quote(input_text, safe='')}"
        elif forced_mode == "playlist":
            api_label = "Playlist"
            endpoint_path = f"/playlist?url={urllib.parse.quote(input_text, safe='')}"
        else:
            api_label = "Search"
            endpoint_path = f"/search?query={urllib.parse.quote(input_text, safe='')}"

    local_target = f"http://127.0.0.1:{PORT}{endpoint_path}"
    data = None
    status_code = 0

    try:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(local_target, timeout=aiohttp.ClientTimeout(total=45.0)) as resp:
                    status_code = resp.status
                    data = await resp.json()
            except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError):
                remote_target = f"{BASE_URL.rstrip('/')}{endpoint_path}"
                async with session.get(remote_target, timeout=aiohttp.ClientTimeout(total=45.0)) as resp:
                    status_code = resp.status
                    data = await resp.json()

    except asyncio.TimeoutError:
        err_msg = (
            f"❌ <b>Rᴇǫᴜᴇsᴛ Tɪᴍᴇᴏᴜᴛ</b>\n\n"
            f"The API took longer than 45s to respond.\n"
            f"<b>Endpoint:</b> <code>{endpoint_path}</code>"
        )
        if loading_msg_id:
            await edit_msg(chat_id, loading_msg_id, err_msg)
        else:
            await send_msg(chat_id, err_msg)
        return
    except Exception as e:
        err_msg = f"❌ <b>Eʀʀᴏʀ:</b> <code>{str(e)}</code>"
        if loading_msg_id:
            await edit_msg(chat_id, loading_msg_id, err_msg)
        else:
            await send_msg(chat_id, err_msg)
        return

    if not data or data.get("status") == "error":
        err_detail = data.get("error", "Unknown API error") if data else "Empty response"
        err_msg = f"❌ <b>API Eʀʀᴏʀ:</b> <code>{err_detail}</code>"
        if loading_msg_id:
            await edit_msg(chat_id, loading_msg_id, err_msg)
        else:
            await send_msg(chat_id, err_msg)
        return

    # If Search or song query: Display rich 16:9 thumbnail photo card with Audio & Video download buttons
    if api_label == "Search":
        vid_id = data.get("id", "")
        title = data.get("title", "Unknown")
        dur_str = data.get("duration", "00:00")
        dur_sec = data.get("duration_sec", 0)
        uploader = data.get("uploader", "YouTube")
        yt_url = data.get("youtube_url", f"https://www.youtube.com/watch?v={vid_id}")
        thumb_url = f"https://i.ytimg.com/vi/{vid_id}/maxresdefault.jpg"

        caption = (
            f"🎵 <b>{title}</b>\n\n"
            f"⏱️ <b>Dᴜʀᴀᴛɪᴏɴ:</b> <code>{dur_str}</code> ({dur_sec}s)\n"
            f"👤 <b>Uᴘʟᴏᴀᴅᴇʀ:</b> <code>{uploader}</code>\n"
            f"🔗 <b>YᴏᴜTᴜʙᴇ:</b> {yt_url}\n"
            f"👨‍💻 <b>Dᴇᴠᴇʟᴏᴘᴇʀ:</b> {OWNER_HANDLE}\n\n"
            f"⚡ <i>Cʟɪᴄᴋ ᴀ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ &amp; ᴜᴘʟᴏᴀᴅ:</i>"
        )
        inline_buttons = [
            [
                {"text": "🎵 Aᴜᴅɪᴏ", "callback_data": f"btn_dl_audio:{vid_id}"},
                {"text": "🎬 Vɪᴅᴇᴏ", "callback_data": f"btn_ask_vq:{vid_id}"},
            ],
            [
                {"text": "📄 Vɪᴇᴡ JSOɴ", "callback_data": f"sample_json:search"},
            ],
        ]

        if loading_msg_id:
            await delete_msg(chat_id, loading_msg_id)
        await send_photo_msg(chat_id, thumb_url, caption, reply_markup={"inline_keyboard": inline_buttons}, video_id=vid_id)
        return

    # Double Response for Playlist / Direct Download API calls
    inline_buttons = []
    if api_label == "Playlist":
        card_text = (
            f"📑 <b>Pʟᴀʏʟɪsᴛ Rᴇsᴜʟᴛ: Sᴜᴄᴄᴇss</b>\n\n"
            f"📋 <b>Pʟᴀʏʟɪsᴛ:</b> <code>{data.get('playlist_title', 'YouTube Playlist')}</code>\n"
            f"🔢 <b>Tᴏᴛᴀʟ Sᴏɴɢs:</b> <code>{data.get('total_items', 0)}</code>\n"
            f"⏱️ <b>Tɪᴍᴇ:</b> <code>{data.get('elapsed_sec', 0.0)}s</code>\n"
            f"👨‍💻 <b>Dᴇᴠᴇʟᴏᴘᴇʀ:</b> <code>{data.get('developer', OWNER_HANDLE)}</code>\n"
        )
    else:
        card_text = (
            f"✅ <b>{api_label.upper()} Rᴇsᴜʟᴛ: Sᴜᴄᴄᴇss</b>\n\n"
            f"🎵 <b>Tɪᴛʟᴇ:</b> <code>{data.get('title', 'Unknown')}</code>\n"
            f"⏱️ <b>Dᴜʀᴀᴛɪᴏɴ:</b> <code>{data.get('duration', '00:00')}</code>\n"
            f"📁 <b>Fɪʟᴇ:</b> <code>{data.get('filename', '')}</code>\n"
            f"⚡ <b>Sᴘᴇᴇᴅ:</b> <code>{data.get('elapsed_sec', 0.0)}s</code>\n"
            f"🔗 <b>Sᴛʀᴇᴀᴍ URL:</b>\n<code>{data.get('stream_url', '')}</code>\n"
            f"👨‍💻 <b>Dᴇᴠᴇʟᴏᴘᴇʀ:</b> <code>{data.get('developer', OWNER_HANDLE)}</code>\n"
        )

    # Format JSON safely for Telegram
    json_str = json.dumps(data, indent=2)
    if len(json_str) > 2800:
        preview_data = dict(data)
        if "items" in preview_data and isinstance(preview_data["items"], list) and len(preview_data["items"]) > 2:
            preview_data["items"] = preview_data["items"][:2] + [f"... ({len(data['items'])} items extracted)"]
        if "indexes" in preview_data and isinstance(preview_data["indexes"], dict) and len(preview_data["indexes"]) > 2:
            preview_data["indexes"] = {f"index_{k}": preview_data["indexes"][f"index_{k}"] for k in (1, 2) if f"index_{k}" in preview_data["indexes"]}
            preview_data["indexes"]["..."] = f"(Total {data.get('total_items')} items indexed)"
        json_display = json.dumps(preview_data, indent=2)
    else:
        json_display = json_str

    # If video ID exists, deliver 16:9 photo card first
    vid_id = data.get("id")
    if api_label != "Playlist" and vid_id:
        thumb_url = f"https://i.ytimg.com/vi/{vid_id}/maxresdefault.jpg"
        if loading_msg_id:
            await delete_msg(chat_id, loading_msg_id)
            loading_msg_id = None
        await send_photo_msg(chat_id, thumb_url, card_text, video_id=vid_id)
        # Send JSON response
        await send_msg(
            chat_id,
            f"📄 <b>Rᴇsᴘᴏɴsᴇ JSOɴ (Cʜʀᴏᴍᴇ Bʀᴏᴡsᴇʀ Fᴏʀᴍᴀᴛ):</b>\n<pre><code class=\"language-json\">{json_display}</code></pre>"
        )
        return

    final_msg = (
        f"{card_text}\n"
        f"📄 <b>Rᴇsᴘᴏɴsᴇ JSOɴ (Cʜʀᴏᴍᴇ Bʀᴏᴡsᴇʀ Fᴏʀᴍᴀᴛ):</b>\n<pre><code class=\"language-json\">{json_display}</code></pre>"
    )

    reply_markup = {"inline_keyboard": inline_buttons} if inline_buttons else None
    if loading_msg_id:
        ok = await edit_msg(chat_id, loading_msg_id, final_msg, reply_markup=reply_markup)
        if not ok:
            await delete_msg(chat_id, loading_msg_id)
            await send_msg(chat_id, final_msg, reply_markup=reply_markup)
    else:
        await send_msg(chat_id, final_msg, reply_markup=reply_markup)


async def handle_callback_query(cq: dict):
    cq_id = cq["id"]
    from_user = cq["from"]
    user_id = from_user["id"]
    data = cq.get("data", "")
    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")

    # Answer callback to remove loading animation
    await call_tg("answerCallbackQuery", {"callback_query_id": cq_id})

    is_adm, role = controller_db.is_admin(user_id)
    if not is_adm:
        await send_msg(chat_id, "🚫 <b>Aᴄᴄᴇss Dᴇɴɪᴇᴅ:</b> Yᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴀɴ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴀᴅᴍɪɴ.")
        return

    # Check Viewer permissions for management tasks
    if role == "viewer" and not data.startswith(("ip_menu", "ips_list_menu", "sample_json", "test_prompt", "quick_test", "dl_direct", "btn_dl_audio", "btn_ask_vq", "dl_vid", "dl_cancel")):
        await send_msg(chat_id, "⚠️ <b>Vɪᴇᴡᴇʀ Rᴏʟᴇ:</b> Yᴏᴜ ʜᴀᴠᴇ ʀᴇᴀᴅ-ᴏɴʟʏ ᴘᴇʀᴍɪssɪᴏɴs.")
        return

    # Download & Upload interactive callbacks
    if data.startswith("btn_dl_audio:"):
        vid_id = data.split(":", 1)[1]
        asyncio.create_task(download_and_upload_audio(chat_id, vid_id))

    elif data.startswith("btn_ask_vq:"):
        vid_id = data.split(":", 1)[1]
        asyncio.create_task(ask_video_quality(chat_id, vid_id))

    elif data.startswith("dl_vid:"):
        parts = data.split(":")
        quality = parts[1]
        vid_id = parts[2]
        asyncio.create_task(download_and_upload_video(chat_id, vid_id, quality=quality))

    elif data.startswith("dl_cancel:"):
        await delete_msg(chat_id, msg.get("message_id"))

    elif data.startswith("sample_json:"):
        ep_type = data.split(":", 1)[1]
        await handle_endpoint_json_sample(chat_id, ep_type)

    elif data.startswith("test_prompt:"):
        mode = data.split(":", 1)[1]
        USER_STATES[user_id] = {"mode": mode, "expires_at": time.time() + 300}
        mode_labels = {
            "audio": "🎵 Aᴜᴅɪᴏ (MP3)",
            "video": "🎬 Vɪᴅᴇᴏ (720p)",
            "search": "🔍 Sᴇᴀʀᴄʜ (Fast Meta)",
            "playlist": "📑 Pʟᴀʏʟɪsᴛ (25 Songs)"
        }
        await send_msg(
            chat_id,
            f"🎯 <b>Mᴏᴅᴇ Sᴇʟᴇᴄᴛᴇᴅ: {mode_labels.get(mode, mode.upper())}</b>\n\n"
            f"Sᴇɴᴅ ʏᴏᴜʀ <b>sᴏɴɢ ɴᴀᴍᴇ</b> ᴏʀ <b>URL</b> ɴᴏᴡ ᴛᴏ ᴛᴇsᴛ:"
        )

    elif data.startswith("quick_test:"):
        parts = data.split(":")
        mode = parts[1]
        query = parts[2]
        if mode == "playlist":
            query = "https://youtube.com/playlist?list=RDIuvVVWOsMBo&playnext=1"
        asyncio.create_task(execute_api_test(chat_id, query, forced_mode=mode))

    elif data.startswith("dl_direct:"):
        parts = data.split(":")
        mode = parts[1]
        vid_id = parts[2]
        yt_url = f"https://www.youtube.com/watch?v=eJuoi13hbBc" if not vid_id else f"https://www.youtube.com/watch?v={vid_id}"
        asyncio.create_task(execute_api_test(chat_id, yt_url, forced_mode=mode))


    elif data.startswith("toggle_block:"):
        ip = data.split(":", 1)[1]
        ip_info = controller_db.get_ip_info(ip)
        new_state = not bool(ip_info and ip_info.get("is_blocked"))
        controller_db.set_ip_block(ip, new_state)
        status_txt = "🚫 Bʟᴏᴄᴋᴇᴅ" if new_state else "✅ Uɴʙʟᴏᴄᴋᴇᴅ"
        await send_msg(chat_id, f"🌐 IP <code>{ip}</code> ɪs ɴᴏᴡ <b>{status_txt}</b>!")
        # If toggled from inside an OSINT inspector card, re-render it
        if msg.get("message_id"):
            await handle_ip_lookup(chat_id, ip, message_id_to_edit=msg.get("message_id"))

    elif data.startswith("unblock:"):
        ip = data.split(":", 1)[1]
        controller_db.set_ip_block(ip, False)
        await send_msg(chat_id, f"✅ IP <code>{ip}</code> ʜᴀs ʙᴇᴇɴ <b>Uɴʙʟᴏᴄᴋᴇᴅ</b>!")

    elif data.startswith("ip_menu:"):
        ip = data.split(":", 1)[1]
        await handle_ip_lookup(chat_id, ip, message_id_to_edit=msg.get("message_id"))

    elif data == "ips_list_menu":
        await handle_ips_list(chat_id)

    elif data.startswith("select_limit:"):
        ip = data.split(":", 1)[1]
        text = f"⏱️ <b>Sᴇᴛ Dᴀɪʟʏ Lɪᴍɪᴛ ғᴏʀ IP:</b> <code>{ip}</code>\nCʟɪᴄᴋ ᴀ ǫᴜɪᴄᴋ ᴘʀᴇsᴇᴛ ᴏʀ ᴛʏᴘᴇ <code>/limit {ip} &lt;number&gt;</code>:"
        presets = [
            [{"text": "50 / Day", "callback_data": f"set_limit:{ip}:50"}, {"text": "100 / Day", "callback_data": f"set_limit:{ip}:100"}],
            [{"text": "500 / Day", "callback_data": f"set_limit:{ip}:500"}, {"text": "Uɴʟɪᴍɪᴛᴇᴅ", "callback_data": f"set_limit:{ip}:0"}],
        ]
        await send_msg(chat_id, text, reply_markup={"inline_keyboard": presets})

    elif data.startswith("set_limit:"):
        parts = data.split(":")
        ip = parts[1]
        limit_val = int(parts[2])
        controller_db.set_ip_limit(ip, limit_val)
        label = "Uɴʟɪᴍɪᴛᴇᴅ" if limit_val == 0 else f"{limit_val} ʀᴇǫᴜᴇsᴛs/ᴅᴀʏ"
        await send_msg(chat_id, f"⏱️ IP <code>{ip}</code> ʟɪᴍɪᴛ sᴇᴛ ᴛᴏ: <b>{label}</b>")

    elif data.startswith("remove_admin:"):
        target_uid = int(data.split(":", 1)[1])
        if user_id != OWNER_ID:
            await send_msg(chat_id, "⚠️ Oɴʟʏ ᴛʜᴇ Oᴡɴᴇʀ ᴄᴀɴ ʀᴇᴍᴏᴠᴇ ᴀᴅᴍɪɴs.")
            return
        success = controller_db.remove_admin(target_uid)
        if success:
            await send_msg(chat_id, f"✅ Aᴅᴍɪɴ <code>{target_uid}</code> ʀᴇᴍᴏᴠᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ.")


async def handle_message(msg: dict):
    from_user = msg.get("from", {})
    user_id = from_user.get("id")
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()

    if not user_id or not chat_id or not text:
        return

    is_adm, role = controller_db.is_admin(user_id)
    if not is_adm:
        await send_msg(
            chat_id,
            f"🚫 <b>Aᴄᴄᴇss Dᴇɴɪᴇᴅ</b>\nYᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴛᴏ ᴜsᴇ ᴛʜɪs ᴄᴏɴᴛʀᴏʟʟᴇʀ.\nCᴏɴᴛᴀᴄᴛ Oᴡɴᴇʀ: {OWNER_HANDLE}"
        )
        return

    # Button triggers
    if text in ("/start", "/menu", "Cʟɪᴄᴋ Oɴ"):
        welcome = (
            f"⚡ <b>GᴀᴍᴇOᴠᴇʀ API Lᴏɢs &amp; Cᴏɴᴛʀᴏʟʟᴇʀ</b>\n\n"
            f"Yᴏᴜ ᴀʀᴇ ʟᴏɢɢᴇᴅ ɪɴ ᴀs: <code>{role.upper()}</code>\n"
            f"Sᴇʟᴇᴄᴛ ᴀɴʏ ᴏᴘᴛɪᴏɴ ғʀᴏᴍ ᴛʜᴇ ᴍᴇɴᴜ ʙᴇʟᴏᴡ ᴛᴏ ᴍᴏɴɪᴛᴏʀ, ᴄᴏɴᴛʀᴏʟ, ᴏʀ ᴛᴇsᴛ ʏᴏᴜʀ API."
        )
        await send_msg(chat_id, welcome, reply_markup=get_main_keyboard(), track=False)

    elif text in ("⚡ API Eɴᴅᴘᴏɪɴᴛs", "/apis", "/endpoints"):
        await handle_api_endpoints(chat_id)

    elif text in ("🔍 Tᴇsᴛ Sᴇᴀʀᴄʜ", "/test", "/tester", "/search_menu"):
        await handle_test_search_menu(chat_id)

    elif text == "📊 Sᴛᴀᴛs":
        await handle_stats(chat_id)

    elif text == "🌐 IPs Lɪsᴛ":
        await handle_ips_list(chat_id)

    elif text == "🚫 Bʟᴏᴄᴋ Mᴀɴᴀɢᴇʀ":
        await handle_block_manager(chat_id)

    elif text == "⏱️ Lɪᴍɪᴛ Mᴀɴᴀɢᴇʀ":
        await handle_limit_manager(chat_id)

    elif text == "👥 Aᴅᴍɪɴs":
        await handle_admins_menu(chat_id)

    elif text == "🧹 Cʟᴇᴀʀ Oʟᴅ Lᴏɢs":
        await handle_clear_old_logs(chat_id)

    elif text in ("❌ Cʟᴏsᴇ Mᴇɴᴜ", "/close", "close", "Close"):
        await send_msg(
            chat_id,
            "✖️ <b>Mᴇɴᴜ Cʟᴏsᴇᴅ.</b>\nTʏᴘᴇ /start ᴏʀ /menu ᴛᴏ ᴏᴘᴇɴ ᴀᴛ ᴀɴʏ ᴛɪᴍᴇ.",
            reply_markup={"remove_keyboard": True},
            track=False
        )

    # Command: /clearcache (manual owner-triggered cleanup)
    elif text in ("/clearcache", "/prunecache"):
        if user_id != OWNER_ID:
            await send_msg(chat_id, "⚠️ Oɴʟʏ ᴛʜᴇ Oᴡɴᴇʀ ᴄᴀɴ ᴍᴀɴᴜᴀʟʟʏ ᴄʟᴇᴀʀ ᴄᴀᴄʜᴇ.")
            return
        from cache_manager import manual_clear_cache
        res = manual_clear_cache(keep_latest_gb=30.0)
        await send_msg(
            chat_id,
            f"🧹 <b>Mᴀɴᴜᴀʟ Cᴀᴄʜᴇ Cʟᴇᴀɴᴜᴘ Cᴏᴍᴘʟᴇᴛᴇ</b>\n\n"
            f"🗑️ <b>Fɪʟᴇs Dᴇʟᴇᴛᴇᴅ:</b> <code>{res['deleted_count']}</code>\n"
            f"📦 <b>Sᴘᴀᴄᴇ Fʀᴇᴇᴅ:</b> <code>{res['freed_gb']} GB</code>\n"
            f"💾 <b>Rᴇᴍᴀɪɴɪɴɢ Cᴀᴄʜᴇ:</b> <code>{res['remaining_gb']} GB</code>"
        )

    # Command: /block <ip>
    elif text.startswith("/block"):
        parts = text.split()
        if len(parts) >= 2:
            target_ip = parts[1].strip()
            controller_db.set_ip_block(target_ip, True)
            await send_msg(chat_id, f"🚫 IP <code>{target_ip}</code> ɪs ɴᴏᴡ <b>Bʟᴏᴄᴋᴇᴅ</b>!")
        else:
            await send_msg(chat_id, "Usage: <code>/block &lt;ip&gt;</code>")

    # Command: /unblock <ip>
    elif text.startswith("/unblock"):
        parts = text.split()
        if len(parts) >= 2:
            target_ip = parts[1].strip()
            controller_db.set_ip_block(target_ip, False)
            await send_msg(chat_id, f"✅ IP <code>{target_ip}</code> ɪs ɴᴏᴡ <b>Uɴʙʟᴏᴄᴋᴇᴅ</b>!")
        else:
            await send_msg(chat_id, "Usage: <code>/unblock &lt;ip&gt;</code>")

    # Command: /limit <ip> <number>
    elif text.startswith("/limit"):
        parts = text.split()
        if len(parts) >= 3 and parts[2].isdigit():
            target_ip = parts[1].strip()
            lim_val = int(parts[2])
            controller_db.set_ip_limit(target_ip, lim_val)
            await send_msg(chat_id, f"⏱️ IP <code>{target_ip}</code> ʟɪᴍɪᴛ sᴇᴛ ᴛᴏ: <code>{lim_val}</code>/day")
        else:
            await send_msg(chat_id, "Usage: <code>/limit &lt;ip&gt; &lt;number&gt;</code>")

    # Command: /ip [ip_address]
    elif text == "/ip":
        await send_msg(
            chat_id,
            "🌐 <b>IP Iɴsᴘᴇᴄᴛᴏʀ &amp; OSINT Tᴏᴏʟ:</b>\n\n"
            "Sᴇɴᴅ ᴀɴ IP ᴛᴏ ɪɴsᴘᴇᴄᴛ ʟᴏᴄᴀᴛɪᴏɴ, ISP, ʜᴏsᴛɪɴɢ/ᴘʀᴏxʏ ғʟᴀɢs &amp; API ʜɪᴛs.\n\n"
            "Usage: <code>/ip &lt;ip_address&gt;</code>\n"
            "<i>Example:</i> <code>/ip 152.55.176.130</code>"
        )

    elif text.startswith("/ip "):
        target_ip = text.split(" ", 1)[1].strip()
        asyncio.create_task(handle_ip_lookup(chat_id, target_ip))

    # Command: /addadmin <user_id> <viewer|editor>
    elif text.startswith("/addadmin"):
        if user_id != OWNER_ID:
            await send_msg(chat_id, "⚠️ Oɴʟʏ ᴛʜᴇ Oᴡɴᴇʀ ᴄᴀɴ ᴀᴅᴅ ᴀᴅᴍɪɴs.")
            return
        parts = text.split()
        if len(parts) >= 2 and parts[1].isdigit():
            new_uid = int(parts[1])
            new_role = parts[2].lower() if len(parts) >= 3 else "viewer"
            controller_db.add_admin(new_uid, new_role)
            await send_msg(chat_id, f"✅ Aᴅᴍɪɴ <code>{new_uid}</code> ᴀᴅᴅᴇᴅ ᴀs <b>{new_role.upper()}</b>!")
        else:
            await send_msg(chat_id, "Usage: <code>/addadmin &lt;user_id&gt; [viewer|editor]</code>")

    # Explicit Slash Test Commands
    elif text.startswith("/audio"):
        q = text.split(" ", 1)[1].strip() if " " in text else ""
        if q:
            asyncio.create_task(execute_api_test(chat_id, q, forced_mode="audio"))
        else:
            await send_msg(chat_id, "Usage: <code>/audio &lt;song name or URL&gt;</code>")

    elif text.startswith("/video"):
        q = text.split(" ", 1)[1].strip() if " " in text else ""
        if q:
            asyncio.create_task(execute_api_test(chat_id, q, forced_mode="video"))
        else:
            await send_msg(chat_id, "Usage: <code>/video &lt;song name or URL&gt;</code>")

    elif text.startswith("/search"):
        q = text.split(" ", 1)[1].strip() if " " in text else ""
        if q:
            asyncio.create_task(execute_api_test(chat_id, q, forced_mode="search"))
        else:
            await send_msg(chat_id, "Usage: <code>/search &lt;query&gt;</code>")

    elif text.startswith("/playlist"):
        q = text.split(" ", 1)[1].strip() if " " in text else ""
        if q:
            asyncio.create_task(execute_api_test(chat_id, q, forced_mode="playlist"))
        else:
            await send_msg(chat_id, "Usage: <code>/playlist &lt;playlist URL&gt;</code>")

    else:
        # Check if text is a raw IP address
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", text):
            asyncio.create_task(handle_ip_lookup(chat_id, text))
            return

        # Fallback: User typed a song name or pasted an API / YouTube URL
        user_state = USER_STATES.pop(user_id, None)
        mode = user_state.get("mode") if user_state else None
        asyncio.create_task(execute_api_test(chat_id, text, forced_mode=mode))


async def auto_pruner_task():
    """Periodic task running every 30 mins to purge 24h old bot messages"""
    while True:
        try:
            await asyncio.sleep(1800)
            expired = controller_db.get_expired_messages(max_age_hours=24.0)
            ids = []
            for m in expired:
                await delete_msg(m["chat_id"], m["message_id"])
                ids.append(m["id"])
            controller_db.delete_tracked_messages_by_ids(ids)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[AutoPruner] Error: {e}")


async def telegram_polling_loop():
    """Long-polling daemon for Telegram updates"""
    offset = 0
    logger.info("==================================================")
    logger.info("Starting Telegram Bot Controller (@YOUTUBE_API_LOGS_BOT)")
    logger.info(f"Owner: {OWNER_ID} ({OWNER_HANDLE})")
    logger.info("==================================================")

    # Start 24h message auto-pruner
    asyncio.create_task(auto_pruner_task())

    # Send initial boot ping to Owner
    try:
        await send_msg(
            OWNER_ID,
            f"⚡ <b>GᴀᴍᴇOᴠᴇʀ API Cᴏɴᴛʀᴏʟʟᴇʀ Bᴏᴛ Oɴʟɪɴᴇ</b>\n\n"
            f"• <b>Sᴇʀᴠᴇʀ:</b> <code>{BASE_URL}</code>\n"
            f"• <b>Pᴏʀᴛ:</b> <code>{PORT}</code>\n"
            f"• <b>Sᴛᴀᴛᴜs:</b> 🟢 Aᴄᴛɪᴠᴇ\n\n"
            f"Cʟɪᴄᴋ ᴏɴ ᴀɴʏ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴛᴏ ᴄᴏɴᴛʀᴏʟ:",
            reply_markup=get_main_keyboard(),
            track=False
        )
    except Exception as e:
        logger.warning(f"Could not send startup message: {e}")

    while True:
        try:
            url = f"{TELEGRAM_API_URL}/getUpdates"
            payload = {"offset": offset, "timeout": 20}
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=25.0)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for update in data.get("result", []):
                            offset = update["update_id"] + 1
                            if "message" in update:
                                asyncio.create_task(handle_message(update["message"]))
                            elif "callback_query" in update:
                                asyncio.create_task(handle_callback_query(update["callback_query"]))
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"[TelegramPolling] Connection note: {e}")
            await asyncio.sleep(2.0)
