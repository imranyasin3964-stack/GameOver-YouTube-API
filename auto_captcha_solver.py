import os
import sys
import time
import asyncio
import logging
from pathlib import Path
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AutoCaptchaSolver")

BASE_DIR = Path(__file__).resolve().parent
COOKIE_FILE = BASE_DIR / "cookies.txt"

# Import reusable Audio Solver
BYPASS_DIR = BASE_DIR / "reCAPTCHA_Audio_Bypass"
sys.path.insert(0, str(BYPASS_DIR))
try:
    from audio_solver import solve_recaptcha_audio
except ImportError:
    logger.error("Could not import audio_solver from reCAPTCHA_Audio_Bypass directory.")


async def bypass_google_recaptcha(target_url: str = "https://www.youtube.com") -> bool:
    """
    Navigates to target_url. If Google reCAPTCHA / sorry page is detected:
    automatically uses the audio challenge solver to bypass it,
    then exports fresh unblocked cookies to cookies.txt.
    """
    logger.info(f"Navigating to {target_url} to check for reCAPTCHA...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            ]
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        page = await context.new_page()

        try:
            await page.goto(target_url, wait_until="networkidle", timeout=30000)
            current_url = page.url
            logger.info(f"Current page URL: {current_url}")

            is_sorry = "google.com/sorry" in current_url
            has_recaptcha = any("recaptcha" in f.url for f in page.frames)

            if is_sorry or has_recaptcha:
                logger.info("[CAPTCHA DETECTED] Google reCAPTCHA detected! Starting audio bypass...")

                anchor_frame = None
                for _ in range(5):
                    for frame in page.frames:
                        if "anchor" in frame.url and "recaptcha" in frame.url:
                            anchor_frame = frame
                            break
                    if anchor_frame:
                        break
                    await asyncio.sleep(1)

                if not anchor_frame:
                    logger.info("No reCAPTCHA anchor iframe detected on page, skipping bypass.")
                else:
                    # Click "I'm not a robot"
                    checkbox = anchor_frame.locator("#recaptcha-anchor")
                    await checkbox.wait_for(state="visible", timeout=10000)
                    await checkbox.click()
                    logger.info("Clicked 'I am not a robot' checkbox...")
                    await asyncio.sleep(2.5)

                # Look for challenge iframe
                challenge_frame = None
                for _ in range(10):
                    for frame in page.frames:
                        if "bframe" in frame.url and "recaptcha" in frame.url:
                            challenge_frame = frame
                            break
                    if challenge_frame:
                        break
                    await asyncio.sleep(0.8)

                if challenge_frame:
                    logger.info("Challenge frame appeared. Clicking Audio Challenge button...")
                    audio_btn = challenge_frame.locator("#recaptcha-audio-button")
                    await audio_btn.wait_for(state="visible", timeout=10000)
                    await audio_btn.click()
                    await asyncio.sleep(2)

                    # Solve audio challenge (up to 3 attempts)
                    for attempt in range(1, 4):
                        audio_url = None
                        for _ in range(5):
                            try:
                                link = challenge_frame.locator(".rc-audiochallenge-tdownload-link")
                                if await link.is_visible():
                                    audio_url = await link.get_attribute("href")
                                    if audio_url:
                                        break
                            except Exception:
                                pass

                            try:
                                src = challenge_frame.locator("audio source")
                                href = await src.get_attribute("src")
                                if href:
                                    audio_url = href
                                    break
                            except Exception:
                                pass
                            await asyncio.sleep(1)

                        if not audio_url:
                            logger.error("Could not find audio challenge MP3 URL!")
                            return False

                        logger.info(f"[Attempt {attempt}/3] Solving audio challenge with Google STT...")
                        text = solve_recaptcha_audio(audio_url)
                        if not text:
                            logger.warning("Audio text not recognized. Retrying new audio clip...")
                            try:
                                reload_btn = challenge_frame.locator("#recaptcha-reload-button")
                                if await reload_btn.is_visible():
                                    await reload_btn.click()
                                    await asyncio.sleep(2.5)
                            except Exception:
                                pass
                            continue

                        logger.info(f"Recognized text: '{text}'. Filling input field...")
                        input_field = challenge_frame.locator("#audio-response")
                        await input_field.fill(text)

                        verify_btn = challenge_frame.locator("#recaptcha-verify-button")
                        await verify_btn.click()
                        logger.info("Clicked Verify button!")
                        await asyncio.sleep(3)
                        break

                # If on sorry page, submit form
                if "google.com/sorry" in page.url:
                    try:
                        submit_btn = page.locator("input[type='submit']")
                        if await submit_btn.is_visible():
                            await submit_btn.click()
                            await asyncio.sleep(3)
                    except Exception:
                        pass

                # Export unblocked cookies ONLY if captcha was present and solved
                cookies = await context.cookies()
                lines = ["# Netscape HTTP Cookie File\n", "# Generated by GameOver reCAPTCHA Auto-Bypass\n"]
                saved_count = 0
                for c in cookies:
                    domain = c.get("domain", "")
                    if not ("youtube.com" in domain or "google.com" in domain):
                        continue
                    flag = "TRUE" if domain.startswith(".") else "FALSE"
                    path = c.get("path", "/")
                    secure = "TRUE" if c.get("secure", False) else "FALSE"
                    expires = int(c.get("expires", 0))
                    if expires <= 0:
                        expires = int(time.time() + 3600 * 24 * 365)
                    name = c.get("name", "")
                    value = c.get("value", "")
                    lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
                    saved_count += 1

                if saved_count > 0:
                    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                        f.writelines(lines)
                    logger.info(f"[SUCCESS] Solved challenge and saved {saved_count} unblocked cookies to {COOKIE_FILE.name}!")
                    return True
            else:
                logger.info("No reCAPTCHA or sorry block detected on page. Existing cookies preserved.")
                return True

        except Exception as e:
            logger.error(f"Error during reCAPTCHA bypass: {e}", exc_info=True)
            return False
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(bypass_google_recaptcha())
