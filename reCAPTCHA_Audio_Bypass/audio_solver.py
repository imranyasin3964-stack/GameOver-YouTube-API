"""
audio_solver.py
===============
Reusable Core Engine — Google reCAPTCHA v2 Audio Challenge Solver

HOW IT WORKS:
  1. Downloads the audio MP3 from the provided URL
  2. Saves it temporarily to disk
  3. Converts MP3 → WAV using pydub (requires FFmpeg on system)
  4. Transcribes WAV using Google Speech Recognition API (FREE)
  5. Cleans up temp files and returns recognized text

USAGE (from any other script):
  from audio_solver import solve_recaptcha_audio
  text = solve_recaptcha_audio("https://...audio.mp3")
  print(text)  # e.g. "seven four two nine"

REQUIREMENTS:
  pip install pydub SpeechRecognition requests
  + FFmpeg must be installed on system (see README)
"""

import os
import uuid
import logging
import tempfile

import requests
import speech_recognition as sr
from pydub import AudioSegment

# ── Logger ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  [audio_solver] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)
# ────────────────────────────────────────────────────────────────────────────────


def solve_recaptcha_audio(audio_url: str, language: str = "en-US") -> str:
    """
    Downloads a reCAPTCHA audio challenge MP3, converts it to WAV,
    and returns the speech-recognized text string.

    Args:
        audio_url (str): Full URL to the .mp3 audio challenge file.
        language  (str): BCP-47 language code for STT (default: 'en-US').

    Returns:
        str: Recognized text (e.g. "seven four two nine") or empty string on failure.

    Raises:
        RuntimeError: If the audio could not be recognized after all retries.
    """
    # ── Temp file paths (unique per call to support concurrent use) ────────────
    tmp_dir  = tempfile.gettempdir()
    uid      = uuid.uuid4().hex[:8]
    mp3_path = os.path.join(tmp_dir, f"captcha_audio_{uid}.mp3")
    wav_path = os.path.join(tmp_dir, f"captcha_audio_{uid}.wav")

    try:
        # ── Step 1: Download MP3 ───────────────────────────────────────────────
        log.info("Downloading audio from: %s", audio_url[:80])
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            )
        }
        response = requests.get(audio_url, headers=headers, timeout=30)
        response.raise_for_status()

        with open(mp3_path, "wb") as f:
            f.write(response.content)
        log.info("MP3 downloaded: %d bytes -> %s", len(response.content), mp3_path)

        # ── Step 2: Convert MP3 → WAV ──────────────────────────────────────────
        log.info("Converting MP3 to WAV...")
        audio_segment = AudioSegment.from_mp3(mp3_path)
        # Normalize: 16kHz mono for best Google STT accuracy
        audio_segment = audio_segment.set_frame_rate(16000).set_channels(1)
        audio_segment.export(wav_path, format="wav")
        log.info("WAV ready: %s", wav_path)

        # ── Step 3: Speech Recognition ─────────────────────────────────────────
        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 300   # Lower = more sensitive
        recognizer.dynamic_energy_threshold = False

        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)

        log.info("Sending audio to Google STT (language=%s)...", language)
        recognized_text = recognizer.recognize_google(audio_data, language=language)
        log.info("Recognized text: '%s'", recognized_text)
        return recognized_text.strip().lower()

    except sr.UnknownValueError:
        log.error("Google STT could not understand the audio.")
        return ""

    except sr.RequestError as e:
        log.error("Google STT API request failed: %s", e)
        return ""

    except requests.RequestException as e:
        log.error("Failed to download audio file: %s", e)
        return ""

    except Exception as e:
        log.error("Unexpected error in audio_solver: %s", e, exc_info=True)
        return ""

    finally:
        # ── Step 4: Cleanup temp files ─────────────────────────────────────────
        for path in [mp3_path, wav_path]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                    log.info("Cleaned temp file: %s", os.path.basename(path))
                except Exception:
                    pass


# ── Quick self-test (run this file directly to test with a real URL) ──────────
if __name__ == "__main__":
    test_url = input("Paste a reCAPTCHA audio MP3 URL to test: ").strip()
    if test_url:
        result = solve_recaptcha_audio(test_url)
        print(f"\n[RESULT] Recognized text: '{result}'")
    else:
        print("No URL provided. Exiting.")
