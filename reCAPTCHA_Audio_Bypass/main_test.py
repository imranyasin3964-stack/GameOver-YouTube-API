"""
main_test.py
============
Playwright Test Script — Google reCAPTCHA v2 Audio Bypass Demo

TARGET:   https://www.google.com/recaptcha/api2/demo
APPROACH: Audio Challenge → Google STT (FREE) → Type Text → Submit

HOW TO RUN:
  1. pip install -r requirements.txt
  2. playwright install chromium
  3. Install FFmpeg (see instructions at bottom of this file)
  4. python main_test.py

IMPORTS audio_solver.py from same folder (no external dependency needed)
"""

import asyncio
import os
import sys
import logging
from datetime import datetime

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ── Import our reusable Audio Solver Engine ────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audio_solver import solve_recaptcha_audio
# ────────────────────────────────────────────────────────────────────────────────

# ── Configuration ────────────────────────────────────────────────────────────
TARGET_URL      = "https://www.google.com/recaptcha/api2/demo"
SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))
SCREENSHOTS_DIR = os.path.join(SCRIPT_DIR, "screenshots")
LOG_FILE        = os.path.join(SCRIPT_DIR, "bypass_log.txt")
# ─────────────────────────────────────────────────────────────────────────────

# ── Logging setup ─────────────────────────────────────────────────────────────
class AsciiSafeFormatter(logging.Formatter):
    def format(self, record):
        msg = super().format(record)
        return msg.encode("ascii", "replace").decode("ascii")

file_handler    = logging.FileHandler(LOG_FILE, encoding="utf-8")
console_handler = logging.StreamHandler(sys.stdout)

fmt = "%(asctime)s  %(levelname)-8s  %(message)s"
date_fmt = "%Y-%m-%d %H:%M:%S"
file_handler.setFormatter(logging.Formatter(fmt, date_fmt))
console_handler.setFormatter(AsciiSafeFormatter(fmt, date_fmt))

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers.clear()
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

log = logging.getLogger("main_test")
# ─────────────────────────────────────────────────────────────────────────────


def save_screenshot(page, tag: str) -> str:
    """Save a timestamped screenshot to the screenshots/ folder."""
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename  = f"recaptcha_{tag}_{timestamp}.png"
    path      = os.path.join(SCREENSHOTS_DIR, filename)
    try:
        asyncio.get_event_loop().run_until_complete(page.screenshot(path=path))
    except Exception:
        pass
    return filename


async def bypass_recaptcha_audio(page) -> bool:
    """
    Core bypass logic:
      1. Switch to reCAPTCHA checkbox iframe → Click 'I'm not a robot'
      2. Switch to challenge iframe → Click Audio Challenge button
      3. Extract audio MP3 URL → Pass to audio_solver → Get text
      4. Type recognized text → Click 'Verify'
    Returns True on success, False on failure.
    """

    # ── Step A: Switch to main reCAPTCHA checkbox iframe ──────────────────────
    log.info("[A] reCAPTCHA checkbox iframe dhundh raha hai...")
    
    try:
        # Wait for iframe to appear
        await page.wait_for_selector(
            "iframe[src*='recaptcha'][src*='anchor']",
            timeout=15_000
        )
    except PWTimeout:
        log.error("    reCAPTCHA iframe nahi mila page par!")
        return False

    # Get the anchor iframe
    captcha_frame = None
    for frame in page.frames:
        if "anchor" in frame.url and "recaptcha" in frame.url:
            captcha_frame = frame
            log.info("    Checkbox iframe found: %s...", frame.url[:60])
            break

    if captcha_frame is None:
        log.error("    reCAPTCHA anchor frame switch fail!")
        return False

    # ── Step B: Click "I'm not a robot" checkbox ──────────────────────────────
    log.info("[B] 'I am not a robot' checkbox click kar raha hai...")
    checkbox = captcha_frame.locator("#recaptcha-anchor")
    await checkbox.wait_for(state="visible", timeout=10_000)
    await checkbox.click()
    log.info("    Checkbox clicked!")

    # Wait for challenge iframe to load
    await asyncio.sleep(2.5)

    # ── Step C: Switch to challenge iframe ────────────────────────────────────
    log.info("[C] Challenge iframe dhundh raha hai...")
    challenge_frame = None
    for _ in range(10):
        for frame in page.frames:
            if "bframe" in frame.url and "recaptcha" in frame.url:
                challenge_frame = frame
                break
        if challenge_frame:
            log.info("    Challenge iframe found: %s...", challenge_frame.url[:60])
            break
        await asyncio.sleep(0.8)

    if challenge_frame is None:
        log.info("    Challenge iframe nahi aaya — reCAPTCHA already passed ya IP trusted hai!")
        return True  # reCAPTCHA auto-passed (no challenge needed)

    # ── Step D: Click Audio Challenge button (headphone icon) ─────────────────
    log.info("[D] Audio challenge button (headphones) click kar raha hai...")
    try:
        audio_btn = challenge_frame.locator("#recaptcha-audio-button")
        await audio_btn.wait_for(state="visible", timeout=10_000)
        await audio_btn.click()
        log.info("    Audio challenge button clicked!")
        await asyncio.sleep(2)
    except PWTimeout:
        log.error("    Audio button nahi mila! May be image challenge. Trying once more...")
        try:
            audio_btn = challenge_frame.locator("button#recaptcha-audio-button")
            await audio_btn.click(force=True)
            await asyncio.sleep(2)
        except Exception as e:
            log.error("    Audio button click fail: %s", e)
            return False

    # ── Step E: Audio Recognition & Verification Retry Loop (Max 3 Attempts) ─
    for attempt_idx in range(1, 4):
        log.info("[E-Attempt %d/3] Audio MP3 URL extract kar raha hai...", attempt_idx)
        audio_url = None

        for attempt in range(5):
            try:
                audio_link = challenge_frame.locator(".rc-audiochallenge-tdownload-link")
                if await audio_link.is_visible():
                    audio_url = await audio_link.get_attribute("href")
                    if audio_url:
                        log.info("    Audio URL found: %s...", audio_url[:80])
                        break
            except Exception:
                pass

            try:
                audio_src = challenge_frame.locator("audio source")
                href = await audio_src.get_attribute("src")
                if href:
                    audio_url = href
                    log.info("    Audio URL (fallback): %s...", audio_url[:80])
                    break
            except Exception:
                pass

            await asyncio.sleep(1)

        if not audio_url:
            log.error("    Audio MP3 URL extract karna fail ho gaya!")
            return False

        # ── Step F: Pass URL to audio_solver → Get recognized text ────────────
        log.info("[F-Attempt %d/3] audio_solver.py ko audio URL de raha hai...", attempt_idx)
        recognized_text = solve_recaptcha_audio(audio_url)

        if not recognized_text:
            log.warning("    Attempt %d: Audio text recognize nahi hua. Retrying new audio...", attempt_idx)
            try:
                reload_btn = challenge_frame.locator("#recaptcha-reload-button")
                if await reload_btn.is_visible():
                    await reload_btn.click()
                    log.info("    Reload audio button clicked!")
                    await asyncio.sleep(2.5)
            except Exception:
                pass
            continue

        log.info("    Recognized: '%s'", recognized_text)

        # ── Step G: Type text into CAPTCHA input field ────────────────────────
        log.info("[G] Recognized text CAPTCHA input mein type kar raha hai...")
        try:
            audio_input = challenge_frame.locator("#audio-response")
            await audio_input.wait_for(state="visible", timeout=8_000)
            await audio_input.fill(recognized_text)
            log.info("    Text typed: '%s'", recognized_text)
        except Exception as e:
            log.error("    CAPTCHA input fill fail: %s", e)
            return False

        # ── Step H: Click "Verify" button ─────────────────────────────────────
        log.info("[H] 'Verify' button click kar raha hai...")
        try:
            verify_btn = challenge_frame.locator("#recaptcha-verify-button")
            await verify_btn.wait_for(state="visible", timeout=8_000)
            await verify_btn.click()
            log.info("    'Verify' clicked!")
            await asyncio.sleep(2.5)
        except Exception as e:
            log.error("    Verify button click fail: %s", e)
            return False

        # Check if error message appeared on captcha ("Multiple matching words", "Multiple correct solutions required", etc.)
        try:
            error_msg = challenge_frame.locator(".rc-audiochallenge-error-message").first
            if await error_msg.is_visible():
                err_txt = await error_msg.inner_text()
                log.warning("    Captcha feedback: '%s'. Fetching next audio clip...", err_txt.strip())
                try:
                    # Clear previous input
                    await audio_input.fill("")
                except Exception:
                    pass
                await asyncio.sleep(2)
                continue
        except Exception:
            pass

        return True

    return False


async def main():
    """Main Playwright test entry point."""
    log.info("=" * 60)
    log.info("  reCAPTCHA v2 Audio Bypass — Test")
    log.info("  Time: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("  Target: %s", TARGET_URL)
    log.info("=" * 60)

    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,  # Visible browser so we can watch it work!
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            ]
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        
        # Stealth: remove webdriver flag
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )

        page = await context.new_page()

        try:
            # ── Step 1: Navigate to target ────────────────────────────────────
            log.info("[1/3] Target URL par ja raha hai: %s", TARGET_URL)
            await page.goto(TARGET_URL, wait_until="networkidle", timeout=30_000)
            log.info("      Page loaded: %s", page.url)
            await asyncio.sleep(1.5)

            # ── Step 2: Run audio bypass ──────────────────────────────────────
            log.info("[2/3] reCAPTCHA Audio Bypass shuru kar raha hai...")
            success = await bypass_recaptcha_audio(page)

            if not success:
                log.error("[FAIL] reCAPTCHA bypass fail ho gaya!")
                ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                await page.screenshot(path=os.path.join(SCREENSHOTS_DIR, f"fail_{ts}.png"))
                await browser.close()
                return

            log.info("[SUCCESS] reCAPTCHA bypass successful!")

            # ── Step 3: Click Submit on demo page ─────────────────────────────
            log.info("[3/3] Demo page par 'Submit' button click kar raha hai...")
            await asyncio.sleep(1.5)

            try:
                submit_btn = page.locator("#recaptcha-demo-submit")
                if await submit_btn.is_visible():
                    await submit_btn.click()
                    log.info("      'Submit' button clicked!")
                    await asyncio.sleep(2)
                else:
                    log.warning("      Submit button visible nahi, trying force click...")
                    await submit_btn.click(force=True)
            except Exception as e:
                log.warning("      Submit button error: %s", e)

            # ── Step 4: Screenshot proof ──────────────────────────────────────
            await asyncio.sleep(2)
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            shot_name = f"bypass_success_{ts}.png"
            shot_path = os.path.join(SCREENSHOTS_DIR, shot_name)
            await page.screenshot(path=shot_path)
            log.info("      Screenshot saved: screenshots/%s", shot_name)

            log.info("=" * 60)
            log.info("  [FINAL RESULT] BYPASS SUCCESSFUL!")
            log.info("  Proof screenshot: screenshots/%s", shot_name)
            log.info("=" * 60)

        except Exception as e:
            log.error("[ERROR] %s", e, exc_info=True)
            try:
                ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                await page.screenshot(path=os.path.join(SCREENSHOTS_DIR, f"error_{ts}.png"))
            except Exception:
                pass
        finally:
            await asyncio.sleep(3)  # Browser thoda ruko taake result dekha ja sake
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
