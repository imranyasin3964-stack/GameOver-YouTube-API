import sqlite3
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

DB_PATH = Path(__file__).resolve().parent / "controller.sqlite3"
DEFAULT_OWNER_ID = 6805412676


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initializes the database schema and seeds the default owner."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # IP Tracking & Management Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ip_records (
                ip TEXT PRIMARY KEY,
                total_requests INTEGER DEFAULT 0,
                today_requests INTEGER DEFAULT 0,
                daily_limit INTEGER DEFAULT 0, -- 0 means unlimited
                is_blocked INTEGER DEFAULT 0,
                last_seen REAL DEFAULT 0,
                last_reset_day TEXT DEFAULT ''
            )
        """)

        # Admin Roles Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL, -- 'owner', 'editor', 'viewer'
                username TEXT DEFAULT '',
                added_at REAL DEFAULT 0
            )
        """)

        # Message tracking for 24h auto-pruning
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                created_at REAL NOT NULL
            )
        """)

        # Media Cache Table for instant 0.001s response
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS media_cache (
                video_id TEXT PRIMARY KEY,
                title TEXT,
                duration TEXT,
                duration_sec INTEGER,
                thumbnail TEXT,
                uploader TEXT,
                youtube_url TEXT,
                audio_file TEXT,
                video_file TEXT,
                created_at REAL,
                last_accessed REAL,
                hit_count INTEGER DEFAULT 1
            )
        """)

        # Search Query Mapping Cache (song name -> video_id)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS query_cache (
                query_text TEXT PRIMARY KEY,
                video_id TEXT NOT NULL,
                created_at REAL
            )
        """)

        # Seed Owner
        cursor.execute("SELECT user_id FROM admins WHERE user_id = ?", (DEFAULT_OWNER_ID,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO admins (user_id, role, username, added_at) VALUES (?, 'owner', 'XHamsterFounders', ?)",
                (DEFAULT_OWNER_ID, time.time())
            )

        conn.commit()


def get_current_day() -> str:
    return time.strftime("%Y-%m-%d")


def check_and_increment_ip(ip: str) -> Tuple[bool, bool, str]:
    """
    Checks IP block status and daily limit.
    Returns: (allowed: bool, is_blocked: bool, message: str)
    """
    now = time.time()
    today = get_current_day()
    
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM ip_records WHERE ip = ?", (ip,))
        row = cursor.fetchone()

        if not row:
            cursor.execute(
                "INSERT INTO ip_records (ip, total_requests, today_requests, daily_limit, is_blocked, last_seen, last_reset_day) "
                "VALUES (?, 1, 1, 0, 0, ?, ?)",
                (ip, now, today)
            )
            conn.commit()
            return True, False, "OK"

        if row["is_blocked"]:
            return False, True, "Your IP has been blocked by administrator. Contact Owner: @XHamsterFounders"

        # Check day rollover
        today_reqs = row["today_requests"]
        if row["last_reset_day"] != today:
            today_reqs = 0

        limit = row["daily_limit"]
        if limit > 0 and today_reqs >= limit:
            return False, False, f"Daily request limit reached ({limit} requests). Contact Owner: @XHamsterFounders to upgrade."

        cursor.execute(
            "UPDATE ip_records SET total_requests = total_requests + 1, today_requests = ?, last_seen = ?, last_reset_day = ? "
            "WHERE ip = ?",
            (today_reqs + 1, now, today, ip)
        )
        conn.commit()
        return True, False, "OK"


def set_ip_block(ip: str, blocked: bool) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        now = time.time()
        today = get_current_day()
        cursor.execute(
            "INSERT INTO ip_records (ip, total_requests, today_requests, daily_limit, is_blocked, last_seen, last_reset_day) "
            "VALUES (?, 0, 0, 0, ?, ?, ?) "
            "ON CONFLICT(ip) DO UPDATE SET is_blocked = excluded.is_blocked",
            (ip, 1 if blocked else 0, now, today)
        )
        conn.commit()
        return True


def set_ip_limit(ip: str, limit: int) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        now = time.time()
        today = get_current_day()
        cursor.execute(
            "INSERT INTO ip_records (ip, total_requests, today_requests, daily_limit, is_blocked, last_seen, last_reset_day) "
            "VALUES (?, 0, 0, ?, 0, ?, ?) "
            "ON CONFLICT(ip) DO UPDATE SET daily_limit = excluded.daily_limit",
            (ip, limit, now, today)
        )
        conn.commit()
        return True


def get_ip_info(ip: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM ip_records WHERE ip = ?", (ip,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_all_ips(limit: int = 50) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM ip_records ORDER BY last_seen DESC LIMIT ?", (limit,))
        return [dict(r) for r in cursor.fetchall()]


def get_total_request_stats() -> Dict[str, Any]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as total_ips, SUM(total_requests) as total_requests, SUM(is_blocked) as blocked_ips FROM ip_records")
        row = cursor.fetchone()
        return {
            "total_ips": row["total_ips"] or 0,
            "total_requests": row["total_requests"] or 0,
            "blocked_ips": row["blocked_ips"] or 0,
        }


# Admin Management
def is_admin(user_id: int) -> Tuple[bool, str]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT role FROM admins WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return True, row["role"]
        return False, ""


def add_admin(user_id: int, role: str, username: str = "") -> bool:
    if role not in ("editor", "viewer"):
        role = "viewer"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO admins (user_id, role, username, added_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET role = excluded.role, username = excluded.username",
            (user_id, role, username, time.time())
        )
        conn.commit()
        return True


def remove_admin(user_id: int) -> bool:
    if user_id == DEFAULT_OWNER_ID:
        return False  # Cannot remove owner
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        conn.commit()
        return True


def get_all_admins() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM admins ORDER BY added_at ASC")
        return [dict(r) for r in cursor.fetchall()]


# 24h Message tracking
def track_bot_message(chat_id: int, message_id: int):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bot_messages (chat_id, message_id, created_at) VALUES (?, ?, ?)",
            (chat_id, message_id, time.time())
        )
        conn.commit()


def get_expired_messages(max_age_hours: float = 24.0) -> List[Dict[str, Any]]:
    cutoff = time.time() - (max_age_hours * 3600)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bot_messages WHERE created_at < ?", (cutoff,))
        return [dict(r) for r in cursor.fetchall()]


def delete_tracked_messages_by_ids(ids: List[int]):
    if not ids:
        return
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"DELETE FROM bot_messages WHERE id IN ({','.join(['?']*len(ids))})", ids)
        conn.commit()


# Media & Search Query Cache Operations for Instant Sub-Millisecond Hits
def save_media_cache(
    video_id: str,
    title: str,
    duration: str = "03:30",
    duration_sec: int = 210,
    thumbnail: str = "",
    uploader: str = "YouTube",
    youtube_url: str = "",
    audio_file: Optional[str] = None,
    video_file: Optional[str] = None,
):
    """Saves or updates media cache in SQLite database for permanent instant retrieval."""
    now = time.time()
    if not youtube_url:
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    if not thumbnail:
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM media_cache WHERE video_id = ?", (video_id,))
        row = cursor.fetchone()
        if row:
            aud = audio_file or row["audio_file"]
            vid = video_file or row["video_file"]
            t = title or row["title"]
            th = thumbnail or row["thumbnail"]
            up = uploader or row["uploader"]
            yt = youtube_url or row["youtube_url"]
            dur = duration or row["duration"]
            dur_s = duration_sec if duration_sec > 0 else row["duration_sec"]
            cursor.execute(
                "UPDATE media_cache SET title = ?, duration = ?, duration_sec = ?, "
                "thumbnail = ?, uploader = ?, youtube_url = ?, audio_file = ?, video_file = ?, last_accessed = ? "
                "WHERE video_id = ?",
                (t, dur, dur_s, th, up, yt, aud, vid, now, video_id)
            )
        else:
            cursor.execute(
                "INSERT INTO media_cache "
                "(video_id, title, duration, duration_sec, thumbnail, uploader, youtube_url, audio_file, video_file, created_at, last_accessed, hit_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (video_id, title, duration, duration_sec, thumbnail, uploader, youtube_url, audio_file, video_file, now, now)
            )
        conn.commit()


def get_media_cache(video_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves media metadata from SQLite cache and increments hit counter."""
    now = time.time()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM media_cache WHERE video_id = ?", (video_id,))
        row = cursor.fetchone()
        if row:
            cursor.execute(
                "UPDATE media_cache SET hit_count = hit_count + 1, last_accessed = ? WHERE video_id = ?",
                (now, video_id)
            )
            conn.commit()
            return dict(row)
        return None


def save_query_mapping(query: str, video_id: str):
    """Maps a search query or song title (e.g. 'fakira') to its resolved video_id."""
    q_norm = query.strip().lower()
    if not q_norm or len(q_norm) < 2:
        return
    now = time.time()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO query_cache (query_text, video_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(query_text) DO UPDATE SET video_id = excluded.video_id, created_at = excluded.created_at",
            (q_norm, video_id, now)
        )
        conn.commit()


def get_video_id_by_query(query: str) -> Optional[str]:
    """Looks up whether a song name / query has already been resolved to a video_id."""
    q_norm = query.strip().lower()
    if not q_norm:
        return None
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT video_id FROM query_cache WHERE query_text = ?", (q_norm,))
        row = cursor.fetchone()
        return row["video_id"] if row else None


def get_cached_songs_count() -> Tuple[int, int]:
    """Returns (total_cached_songs, total_cache_hits)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as total, SUM(hit_count) as hits FROM media_cache")
        row = cursor.fetchone()
        total = row["total"] or 0
        hits = row["hits"] or 0
        return total, hits


def scan_and_index_cache_directory(cache_dir: Path):
    """
    Scans the NVMe cache directory on startup.
    Registers any existing audio_*.mp3 and video_*.mp4 files into SQLite media_cache.
    Ensures pre-existing downloaded songs are instantly available without re-downloading!
    """
    if not cache_dir.exists():
        return
    now = time.time()
    count = 0
    with get_connection() as conn:
        cursor = conn.cursor()
        for entry in cache_dir.iterdir():
            if not entry.is_file() or entry.stat().st_size < 1024:
                continue
            name = entry.name
            vid = None
            media_type = None
            if name.startswith("audio_"):
                parts = name.rsplit(".", 1)
                if len(parts) == 2 and parts[1].lower() in ("opus", "mp3", "m4a", "flac", "wav"):
                    vid = parts[0][6:]
                    media_type = "audio"
            elif name.startswith("video_"):
                parts = name.rsplit(".", 1)
                if len(parts) == 2 and parts[1].lower() in ("mp4", "mkv", "webm"):
                    vid = parts[0][6:]
                    media_type = "video"

            if vid and len(vid) >= 6:
                cursor.execute("SELECT * FROM media_cache WHERE video_id = ?", (vid,))
                existing = cursor.fetchone()
                if not existing:
                    cursor.execute(
                        "INSERT INTO media_cache "
                        "(video_id, title, duration, duration_sec, thumbnail, uploader, youtube_url, audio_file, video_file, created_at, last_accessed, hit_count) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                        (
                            vid,
                            f"YouTube {media_type.capitalize()} ({vid})",
                            "03:30",
                            210,
                            f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                            "YouTube",
                            f"https://www.youtube.com/watch?v={vid}",
                            name if media_type == "audio" else None,
                            name if media_type == "video" else None,
                            now,
                            now
                        )
                    )
                    count += 1
                else:
                    if media_type == "audio":
                        # If opus, prioritize setting as main audio file
                        if name.endswith(".opus") or not existing["audio_file"]:
                            cursor.execute("UPDATE media_cache SET audio_file = ? WHERE video_id = ?", (name, vid))
                    elif media_type == "video" and not existing["video_file"]:
                        cursor.execute("UPDATE media_cache SET video_file = ? WHERE video_id = ?", (name, vid))
        conn.commit()


# Self-init on import
init_db()
