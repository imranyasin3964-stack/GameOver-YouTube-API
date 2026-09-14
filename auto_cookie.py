import os
import sys
import time
import logging
import shutil
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("CookieUpdater")

BASE_DIR = Path(__file__).resolve().parent
COOKIE_FILE = BASE_DIR / "cookies.txt"

def is_target_domain(domain: str) -> bool:
    d = domain.lower()
    return "youtube.com" in d or "google.com" in d


def extract_and_save_cookies(browser_name: str = "chrome") -> bool:
    """
    Extracts cookies from the specified browser (chrome, chromium, brave, firefox)
    and saves only YouTube & Google cookies in Netscape format to cookies.txt.
    """
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except ImportError:
        logger.error("yt-dlp is not installed in this python environment.")
        return False

    logger.info(f"Attempting to extract cookies from {browser_name}...")
    
    browsers_to_try = [browser_name]
    for b in ["chrome", "chromium", "firefox"]:
        if b not in browsers_to_try:
            browsers_to_try.append(b)
            
    success = False
    for b in browsers_to_try:
        try:
            logger.info(f"Extracting cookies from '{b}'...")
            jar = extract_cookies_from_browser(b)
            if jar and len(jar) > 0:
                temp_file = BASE_DIR / "cookies.txt.tmp"
                jar.save(str(temp_file), ignore_discard=True, ignore_expires=True)
                
                # Filter to only YouTube and Google domains
                if temp_file.exists() and temp_file.stat().st_size > 0:
                    with open(temp_file, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    
                    filtered_lines = []
                    for line in lines:
                        if line.startswith("#"):
                            filtered_lines.append(line)
                        elif line.strip():
                            parts = line.split("\t")
                            if len(parts) > 0 and is_target_domain(parts[0]):
                                filtered_lines.append(line)

                    with open(temp_file, "w", encoding="utf-8") as f:
                        f.writelines(filtered_lines)

                    shutil.move(str(temp_file), str(COOKIE_FILE))
                    logger.info(f"[SUCCESS] Extracted and filtered {len(filtered_lines)} YouTube/Google cookies from '{b}' into {COOKIE_FILE.name}!")
                    success = True
                    break
        except Exception as e:
            logger.warning(f"Failed to extract from '{b}': {e}")
            
    if not success:
        logger.error("Could not extract cookies from any browser. Make sure you are logged into YouTube in Chrome on this machine.")
        return False
        
    return True


def check_cookie_health(timeout_sec: int = 8) -> bool:
    """
    Tests if cookies.txt is currently healthy and functioning with YouTube.
    Returns True if valid, False if blocked, expired, or missing.
    """
    if not COOKIE_FILE.exists() or COOKIE_FILE.stat().st_size == 0:
        logger.warning("[Cookie Health] cookies.txt is missing or empty!")
        return False

    try:
        from yt_dlp import YoutubeDL
        opts = {
            "cookiefile": str(COOKIE_FILE),
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": timeout_sec,
            "extract_flat": True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "ios", "tv"]
                }
            },
            "js_runtimes": {
                "node": {},
                "deno": {},
            },
        }
        with YoutubeDL(opts) as ydl:
            # Query a reliable public track metadata
            ydl.extract_info("https://www.youtube.com/watch?v=sK7riqg2mr4", download=False)
        logger.info("[Cookie Health] Cookie health is 100% HEALTHY and ACTIVE! ✅")
        return True
    except Exception as err:
        logger.warning(f"[Cookie Health] Cookie check failed: {err}")
        return False


def ensure_healthy_cookies() -> bool:
    """
    Checks if cookies.txt exists. If user already uploaded cookies.txt, NEVER overwrite it!
    Only extracts from Chrome if cookies.txt is completely missing or empty.
    """
    if COOKIE_FILE.exists() and COOKIE_FILE.stat().st_size > 100:
        logger.info("[Cookie Health] Using existing cookies.txt uploaded by user.")
        return True

    logger.warning("[Cookie Health] cookies.txt is missing. Attempting fallback extraction...")
    extract_and_save_cookies("chrome")
    return check_cookie_health()


def run_periodic_updater(interval_minutes: int = 20, health_check_minutes: int = 5):
    """
    Continuously monitors cookie health every `health_check_minutes`.
    Refreshes cookies if health drops, or every `interval_minutes` automatically.
    """
    logger.info(f"Starting Cookie Health Monitor (Health check every {health_check_minutes}m, Refresh every {interval_minutes}m)...")
    last_full_refresh = 0.0

    while True:
        now = time.time()
        # Full refresh if interval exceeded
        if (now - last_full_refresh) >= (interval_minutes * 60):
            logger.info("[Cookie Daemon] Periodic full refresh triggered...")
            extract_and_save_cookies("chrome")
            last_full_refresh = time.time()
        else:
            # Quick health check
            healthy = check_cookie_health()
            if not healthy:
                logger.warning("[Cookie Daemon] Cookie health dropped! Extracting fresh cookies immediately...")
                extract_and_save_cookies("chrome")
                last_full_refresh = time.time()

        time.sleep(health_check_minutes * 60)


if __name__ == "__main__":
    interval = 20
    if len(sys.argv) > 1:
        if sys.argv[1] == "--once":
            success = extract_and_save_cookies("chrome")
            sys.exit(0 if success else 1)
        elif sys.argv[1] == "--health":
            healthy = check_cookie_health()
            sys.exit(0 if healthy else 1)
        try:
            interval = int(sys.argv[1])
        except ValueError:
            pass

    run_periodic_updater(interval)
