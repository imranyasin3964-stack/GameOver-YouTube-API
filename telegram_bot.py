import asyncio
import aiohttp
import json
import logging
import time
from typing import Optional, Dict, Any, List

import controller_db
from cache_manager import get_cache_stats
from config import BASE_URL, PORT

logger = logging.getLogger("GameOverAPI.TelegramBot")

BOT_TOKEN = "8718878406:AAGOPBTJw1XQv45i5RBf01fGbpHfHbAbM5k"
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
OWNER_ID = 6805412676
OWNER_HANDLE = "@XHamsterFounders"

# Session state for interactive button input (e.g. setting custom limit)
USER_STATES: Dict[int, Dict[str, Any]] = {}


def get_main_keyboard() -> dict:
    """Persistent 4/6 Button Grid next to mic with Small-Caps design"""
    return {
        "keyboard": [
            [{"text": "📊 Sᴛᴀᴛs"}, {"text": "🌐 IPs Lɪsᴛ"}],
            [{"text": "🚫 Bʟᴏᴄᴋ Mᴀɴᴀɢᴇʀ"}, {"text": "⏱️ Lɪᴍɪᴛ Mᴀɴᴀɢᴇʀ"}],
            [{"text": "👥 Aᴅᴍɪɴs"}, {"text": "🧹 Cʟᴇᴀʀ Oʟᴅ Lᴏɢs"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
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


async def delete_msg(chat_id: int, message_id: int) -> bool:
    res = await call_tg("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    return bool(res and res.get("ok"))


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

    # Format copyable monospace JSON payload (limited to 500 chars)
    json_str = json.dumps(log_data.get("response", {}), indent=2)
    if len(json_str) > 700:
        json_str = json_str[:700] + "\n  ...\n}"

    text = (
        f"🚀 <b>Nᴇᴡ API Rᴇǫᴜᴇsᴛ</b>\n\n"
        f"🌐 <b>IP:</b> <code>{ip}</code>\n"
        f"🎵 <b>Sᴏɴɢ:</b> <code>{title}</code>\n"
        f"📁 <b>Tʏᴘᴇ:</b> <code>{m_type} ({quality})</code>\n"
        f"⚡ <b>Cᴀᴄʜᴇᴅ:</b> <code>{cached}</code> | ⏱️ <b>Tɪᴍᴇ:</b> <code>{elapsed}s</code>\n\n"
        f"📄 <b>Rᴇsᴘᴏɴsᴇ JSOɴ:</b>\n"
        f"<pre><code class=\"language-json\">{json_str}</code></pre>"
    )

    # Inline button for instant 1-click IP block
    ip_info = controller_db.get_ip_info(ip)
    is_blocked = bool(ip_info and ip_info.get("is_blocked"))
    btn_text = f"✅ Uɴʙʟᴏᴄᴋ {ip}" if is_blocked else f"🚫 Bʟᴏᴄᴋ {ip}"
    btn_cb = f"toggle_block:{ip}"

    reply_markup = {
        "inline_keyboard": [
            [{"text": btn_text, "callback_data": btn_cb}],
            [{"text": f"⏱️ Lɪᴍɪᴛ {ip}", "callback_data": f"select_limit:{ip}"}],
        ]
    }

    admins = controller_db.get_all_admins()
    for a in admins:
        try:
            await send_msg(a["user_id"], text, reply_markup=reply_markup, track=True)
        except Exception as e:
            logger.debug(f"Failed to send log to admin {a['user_id']}: {e}")


# Button click & text command handlers
async def handle_stats(chat_id: int):
    c_stats = get_cache_stats()
    req_stats = controller_db.get_total_request_stats()
    
    text = (
        f"📊 <b>GᴀᴍᴇOᴠᴇʀ API Sᴛᴀᴛɪsᴛɪᴄs</b>\n\n"
        f"🌐 <b>Tᴏᴛᴀʟ Cʟɪᴇɴᴛ IPs:</b> <code>{req_stats['total_ips']}</code>\n"
        f"🔥 <b>Tᴏᴛᴀʟ Rᴇǫᴜᴇsᴛs:</b> <code>{req_stats['total_requests']}</code>\n"
        f"🚫 <b>Bʟᴏᴄᴋᴇᴅ IPs:</b> <code>{req_stats['blocked_ips']}</code>\n\n"
        f"💾 <b>Cᴀᴄʜᴇ Fɪʟᴇs:</b> <code>{c_stats['cached_files_count']}</code>\n"
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

    # Check Viewer permissions
    if role == "viewer" and not data.startswith("ip_menu"):
        await send_msg(chat_id, "⚠️ <b>Vɪᴇᴡᴇʀ Rᴏʟᴇ:</b> Yᴏᴜ ʜᴀᴠᴇ ʀᴇᴀᴅ-ᴏɴʟʏ ᴘᴇʀᴍɪssɪᴏɴs.")
        return

    if data.startswith("toggle_block:"):
        ip = data.split(":", 1)[1]
        ip_info = controller_db.get_ip_info(ip)
        new_state = not bool(ip_info and ip_info.get("is_blocked"))
        controller_db.set_ip_block(ip, new_state)
        status_txt = "🚫 Bʟᴏᴄᴋᴇᴅ" if new_state else "✅ Uɴʙʟᴏᴄᴋᴇᴅ"
        await send_msg(chat_id, f"🌐 IP <code>{ip}</code> ɪs ɴᴏᴡ <b>{status_txt}</b>!")

    elif data.startswith("unblock:"):
        ip = data.split(":", 1)[1]
        controller_db.set_ip_block(ip, False)
        await send_msg(chat_id, f"✅ IP <code>{ip}</code> ʜᴀs ʙᴇᴇɴ <b>Uɴʙʟᴏᴄᴋᴇᴅ</b>!")

    elif data.startswith("ip_menu:"):
        ip = data.split(":", 1)[1]
        ip_info = controller_db.get_ip_info(ip) or {}
        is_bl = bool(ip_info.get("is_blocked"))
        lim = ip_info.get("daily_limit", 0)
        hits = ip_info.get("total_requests", 0)
        today = ip_info.get("today_requests", 0)

        text = (
            f"🌐 <b>IP Iɴsᴘᴇᴄᴛᴏʀ:</b> <code>{ip}</code>\n\n"
            f"• <b>Sᴛᴀᴛᴜs:</b> {'🚫 Bʟᴏᴄᴋᴇᴅ' if is_bl else '✅ Aᴄᴛɪᴠᴇ'}\n"
            f"• <b>Tᴏᴛᴀʟ Hɪᴛs:</b> <code>{hits}</code>\n"
            f"• <b>Tᴏᴅᴀʏ Hɪᴛs:</b> <code>{today}</code>\n"
            f"• <b>Dᴀɪʟʏ Lɪᴍɪᴛ:</b> <code>{lim if lim > 0 else 'Uɴʟɪᴍɪᴛᴇᴅ'}</code>\n"
        )
        block_btn = {"text": f"{'✅ Uɴʙʟᴏᴄᴋ' if is_bl else '🚫 Bʟᴏᴄᴋ'}", "callback_data": f"toggle_block:{ip}"}
        limit_btn = {"text": "⏱️ Cʜᴀɴɢᴇ Lɪᴍɪᴛ", "callback_data": f"select_limit:{ip}"}
        reply_markup = {"inline_keyboard": [[block_btn, limit_btn]]}
        await send_msg(chat_id, text, reply_markup=reply_markup)

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
            f"⚡ <b>GᴀᴍᴇOᴠᴇʀ API Lᴏɢs & Cᴏɴᴛʀᴏʟʟᴇʀ</b>\n\n"
            f"Yᴏᴜ ᴀʀᴇ ʟᴏɢɢᴇᴅ ɪɴ ᴀs: <code>{role.upper()}</code>\n"
            f"Sᴇʟᴇᴄᴛ ᴀɴʏ ᴏᴘᴛɪᴏɴ ғʀᴏᴍ ᴛʜᴇ ᴍᴇɴᴜ ʙᴇʟᴏᴡ ᴛᴏ ᴍᴏɴɪᴛᴏʀ ᴀɴᴅ ᴄᴏɴᴛʀᴏʟ ʏᴏᴜʀ API."
        )
        await send_msg(chat_id, welcome, reply_markup=get_main_keyboard(), track=False)

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
