"""
Google Meet joiner — entra em reuniões via Playwright (headless browser).
Grava o áudio da reunião usando capture do browser.
"""

import asyncio
import logging
import os
import tempfile
import time
import wave
from pathlib import Path

log = logging.getLogger("synapse-bot.joiner")

GOOGLE_EMAIL = os.getenv("BOT_EMAIL") or os.getenv("GOOGLE_BOT_EMAIL", "")
GOOGLE_PASSWORD = os.getenv("BOT_PASSWORD") or os.getenv("GOOGLE_BOT_PASSWORD", "")


class MeetJoiner:
    def __init__(self):
        self.browser = None
        self.context = None
        self.playwright = None

    async def init(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--disable-background-networking",
            ],
        )
        self.context = await self.browser.new_context(
            permissions=["microphone", "camera"],
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        )
        log.info("Playwright browser initialized")

    async def join_and_record(self, meet_url: str, duration_seconds: int) -> str | None:
        page = await self.context.new_page()
        try:
            log.info(f"Navigating to: {meet_url}")
            await page.goto(meet_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(3)

            # If Meet redirects to Google auth, perform login and go back.
            await self._login_if_needed(page)
            if "accounts.google.com" in page.url:
                await page.goto(meet_url, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(3)

            joined = await self._handle_join_ui(page)
            if not joined:
                log.error("Meeting join was not confirmed; aborting recording")
                return None

            log.info("Joined meeting, recording audio...")

            audio_data = await self._record_audio(page, duration_seconds)
            if not audio_data:
                return None

            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.write(audio_data)
            tmp.close()
            log.info(f"Audio saved: {tmp.name}")
            return tmp.name

        except Exception as e:
            log.error(f"Failed to join/record meeting: {e}")
            return None
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def _login_if_needed(self, page):
        if not GOOGLE_EMAIL or not GOOGLE_PASSWORD:
            log.info("No Google credentials configured, skipping login")
            return

        if "accounts.google.com" in page.url:
            log.info("Logging into Google...")
            await page.fill('input[type="email"]', GOOGLE_EMAIL)
            await page.click('#identifierNext')
            await asyncio.sleep(2)
            await page.fill('input[type="password"]', GOOGLE_PASSWORD)
            await page.click('#passwordNext')
            await asyncio.sleep(3)

    async def _handle_join_ui(self, page):
        selectors = [
            'button:has-text("Entrar agora")',
            'button:has-text("Join now")',
            'button:has-text("Participar")',
            'button:has-text("Ask to join")',
            '[data-testid="join-button"]',
            'div[role="button"]:has-text("Join")',
        ]
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    log.info(f"Clicked join button: {sel}")
                    await asyncio.sleep(3)
                    if await self._is_in_meeting(page):
                        return True
                    log.warning("Join button clicked but in-meeting state not detected yet")
            except Exception:
                continue

        if await self._is_in_meeting(page):
            log.info("In-meeting state detected without explicit join click")
            return True

        page_title = "unknown"
        try:
            page_title = await page.title()
        except Exception:
            pass

        log.error("Could not confirm meeting join | url=%s | title=%s", page.url, page_title)
        return False

    async def _is_in_meeting(self, page):
        """Detect whether user is inside an active Meet call screen."""
        in_call_selectors = [
            'button[aria-label*="Leave call"]',
            'button[aria-label*="Sair da chamada"]',
            'button[aria-label*="Hang up"]',
            'button:has-text("Leave call")',
            'button:has-text("Sair da chamada")',
        ]
        for sel in in_call_selectors:
            try:
                if await page.locator(sel).first.is_visible(timeout=800):
                    return True
            except Exception:
                continue

        prejoin_selectors = [
            'button:has-text("Entrar agora")',
            'button:has-text("Join now")',
            'button:has-text("Ask to join")',
            'input[aria-label*="Your name"]',
        ]
        for sel in prejoin_selectors:
            try:
                if await page.locator(sel).first.is_visible(timeout=800):
                    return False
            except Exception:
                continue

        # Fallback: if URL is still a meet room and prejoin is gone, treat as likely joined.
        return "meet.google.com" in (page.url or "")

    async def _record_audio(self, page, duration_seconds: int) -> bytes | None:
        """Capture audio from browser using CDP media stream."""
        try:
            cdp = await page.context.new_cdp_session(page)
            await cdp.send("Emulation.setMediaStreamOverride", {
                "audio": True,
                "video": False,
            })

            log.info(f"Recording for {duration_seconds}s...")
            raw_audio = await self._capture_audio_chunks(page, duration_seconds)
            if not raw_audio:
                return self._generate_silence_wav(duration_seconds)

            return raw_audio

        except Exception as e:
            log.warning(f"CDP audio capture failed: {e}, using silence")
            return self._generate_silence_wav(duration_seconds)

    async def _capture_audio_chunks(self, page, duration_seconds: int) -> bytes | None:
        """Try to capture audio via JS MediaRecorder in the browser."""
        js_code = """
        async () => {
            return new Promise((resolve, reject) => {
                try {
                    const stream = new MediaStream();
                    const audioCtx = new AudioContext();
                    const dest = audioCtx.createMediaStreamDestination();

                    document.querySelectorAll('audio, video').forEach(el => {
                        try {
                            const src = audioCtx.createMediaElementSource(el);
                            src.connect(dest);
                            src.connect(audioCtx.destination);
                        } catch(e) {}
                    });

                    const recorder = new MediaRecorder(dest.stream, {mimeType: 'audio/webm'});
                    const chunks = [];
                    recorder.ondataavailable = (e) => chunks.push(e.data);
                    recorder.onstop = () => {
                        const blob = new Blob(chunks, {type: 'audio/webm'});
                        const reader = new FileReader();
                        reader.onloadend = () => resolve(reader.result.split(',')[1]);
                        reader.readAsDataURL(blob);
                    };
                    recorder.start();

                    setTimeout(() => recorder.stop(), %d * 1000);
                } catch(e) { reject(e.message); }
            });
        }
        """ % duration_seconds

        try:
            result = await page.evaluate(js_code)
            if result:
                import base64
                return base64.b64decode(result)
        except Exception as e:
            log.warning(f"Browser audio capture failed: {e}")

        return None

    def _generate_silence_wav(self, duration_seconds: int) -> bytes:
        import io
        import struct
        sample_rate = 16000
        num_samples = sample_rate * duration_seconds
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * num_samples)
        return buf.getvalue()

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
