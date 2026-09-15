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


# Self-init on import
init_db()
