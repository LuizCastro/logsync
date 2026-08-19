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

GOOGLE_EMAIL = os.getenv("GOOGLE_BOT_EMAIL") or os.getenv("BOT_EMAIL", "")
GOOGLE_PASSWORD = os.getenv("GOOGLE_BOT_PASSWORD") or os.getenv("BOT_PASSWORD", "")
GOOGLE_STORAGE_STATE_PATH = os.getenv("GOOGLE_STORAGE_STATE_PATH", "/app/credentials/google-storage-state.json")
SAVE_GOOGLE_STATE = os.getenv("SAVE_GOOGLE_STATE", "true").strip().lower() in {"1", "true", "yes", "on"}
MEET_ANONYMOUS_MODE = os.getenv("MEET_ANONYMOUS_MODE", "true").strip().lower() in {"1", "true", "yes", "on"}
MEET_GUEST_NAME = os.getenv("MEET_GUEST_NAME", "LogSync Bot")
MEET_APPROVAL_WAIT_SECONDS = int(os.getenv("MEET_APPROVAL_WAIT_SECONDS", "90"))
DEBUG_DIR = Path("/app/data/debug")
DEBUG_DIR.mkdir(parents=True, exist_ok=True)


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
        context_kwargs = {
            "permissions": ["microphone", "camera"],
            "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        }
        if GOOGLE_STORAGE_STATE_PATH and Path(GOOGLE_STORAGE_STATE_PATH).exists():
            context_kwargs["storage_state"] = GOOGLE_STORAGE_STATE_PATH
            log.info("Loaded Google storage state from %s", GOOGLE_STORAGE_STATE_PATH)

        self.context = await self.browser.new_context(**context_kwargs)
        log.info("Playwright browser initialized")

    async def join_and_record(self, meet_url: str, duration_seconds: int, max_duration_seconds: int = 14400) -> str | None:
        page = await self.context.new_page()
        try:
            if not MEET_ANONYMOUS_MODE:
                await self._ensure_google_session(page)
            else:
                log.info("Using anonymous Meet mode (host approval expected)")

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

            if duration_seconds <= 0:
                log.info(
                    "Joined meeting, recording until end (max=%ss)...",
                    max_duration_seconds,
                )
            else:
                log.info("Joined meeting, recording audio...")

            audio_data = await self._record_audio(page, duration_seconds, max_duration_seconds)
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
            await self._prepare_google_signin(page)

            email = await self._wait_for_first_visible(page, [
                'input[type="email"]',
                'input[name="identifier"]',
                '#identifierId',
            ], timeout_ms=18000)
            if email is not None:
                await email.fill(GOOGLE_EMAIL)
                await self._click_first_available(page, [
                    '#identifierNext',
                    'button:has-text("Next")',
                    'div[role="button"]:has-text("Next")',
                ])
                await asyncio.sleep(2)
            else:
                # Account chooser screen: click matching account or switch account.
                await self._click_first_available(page, [
                    f'text={GOOGLE_EMAIL}',
                    'text=Use another account',
                    'text=Usar outra conta',
                ])

            await self._click_first_available(page, [
                'text=Try another way',
                'text=Use your password',
                'text=Enter your password',
                'text=Tentar de outra forma',
                'text=Use sua senha',
                'text=Digite sua senha',
            ])

            pw = await self._wait_for_first_visible(page, [
                'input[type="password"]',
                'input[name="Passwd"]',
            ], timeout_ms=22000)
            if pw is None:
                await self._dump_google_login_debug(page, "password-not-found")
                raise RuntimeError("google password input not found")

            await pw.fill(GOOGLE_PASSWORD)
            await self._click_first_available(page, [
                '#passwordNext',
                'button:has-text("Next")',
                'div[role="button"]:has-text("Next")',
            ])
            await asyncio.sleep(5)

            if "challenge" in (page.url or ""):
                log.warning("Google account requires additional verification challenge")
                await self._dump_google_login_debug(page, "challenge")

    async def _prepare_google_signin(self, page):
        await self._click_first_available(page, [
            'text=Use another account',
            'text=Usar outra conta',
            'text=Sign in with another account',
        ])

    async def _wait_for_first_visible(self, page, selectors, timeout_ms=15000):
        end = time.time() + (timeout_ms / 1000)
        while time.time() < end:
            for sel in selectors:
                try:
                    cand = page.locator(sel).first
                    if await cand.is_visible(timeout=500):
                        return cand
                except Exception:
                    continue
            await asyncio.sleep(0.3)
        return None

    async def _click_first_available(self, page, selectors):
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=800):
                    await btn.click()
                    return True
            except Exception:
                continue
        return False

    async def _dump_google_login_debug(self, page, reason):
        ts = int(time.time())
        base = DEBUG_DIR / f"google-login-{reason}-{ts}"
        try:
            await page.screenshot(path=str(base.with_suffix('.png')), full_page=True)
        except Exception:
            pass
        snippet = ""
        try:
            snippet = (await page.inner_text("body")).replace("\n", " ")[:700]
        except Exception:
            snippet = "(body unavailable)"
        title = "unknown"
        try:
            title = await page.title()
        except Exception:
            pass
        log.warning("Google login debug saved: %s.* | title=%s | url=%s | text=%s", base, title, page.url, snippet)

    async def _ensure_google_session(self, page):
        """Authenticate at Google first so Meet links don't get blocked as anonymous."""
        if not GOOGLE_EMAIL or not GOOGLE_PASSWORD:
            log.warning("Google credentials not configured; meeting may be blocked as anonymous")
            return

        try:
            await page.goto("https://accounts.google.com/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            if "accounts.google.com" in (page.url or ""):
                await self._login_if_needed(page)

            # Touch Meet homepage to finalize session cookies before room navigation.
            await page.goto("https://meet.google.com/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            if SAVE_GOOGLE_STATE and GOOGLE_STORAGE_STATE_PATH:
                try:
                    out = Path(GOOGLE_STORAGE_STATE_PATH)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    await page.context.storage_state(path=str(out))
                    log.info("Saved Google storage state to %s", out)
                except Exception as save_err:
                    log.warning("Failed to save Google storage state: %s", save_err)

            log.info("Google session prepared for Meet access")
        except Exception as e:
            log.warning(f"Failed to prepare Google session: {e}")

    async def _handle_join_ui(self, page):
        selectors = [
            'button:has-text("Entrar agora")',
            'button:has-text("Join now")',
            'button:has-text("Participar")',
            'button:has-text("Ask to join")',
            'button:has-text("Pedir para participar")',
            'button:has-text("Solicitar para entrar")',
            '[data-testid="join-button"]',
            'div[role="button"]:has-text("Join")',
        ]

        # If pre-join asks for guest name, provide one to enable join request.
        await self._fill_guest_name_if_needed(page)

        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    log.info(f"Clicked join button: {sel}")
                    await asyncio.sleep(2)
                    if await self._wait_for_join_result(page, timeout_seconds=12):
                        return True
                    if await self._is_waiting_room(page):
                        log.warning("Join request sent; waiting for host approval")
                        approved = await self._wait_for_host_approval(page, MEET_APPROVAL_WAIT_SECONDS)
                        if approved:
                            return True
                        await self._dump_join_debug(page, "waiting-room-timeout")
                        return False
                    log.warning("Join button clicked but in-meeting state not detected")
            except Exception:
                continue

        if await self._wait_for_join_result(page, timeout_seconds=8):
            log.info("In-meeting state detected without explicit join click")
            return True

        if await self._is_waiting_room(page):
            log.warning("Still in waiting room; waiting for host approval")
            approved = await self._wait_for_host_approval(page, MEET_APPROVAL_WAIT_SECONDS)
            if approved:
                return True
            await self._dump_join_debug(page, "waiting-room-no-click-timeout")
            return False

        page_title = "unknown"
        try:
            page_title = await page.title()
        except Exception:
            pass

        await self._dump_join_debug(page, "join-not-confirmed")
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
        return False

    async def _wait_for_join_result(self, page, timeout_seconds=10):
        end = time.time() + timeout_seconds
        while time.time() < end:
            if await self._is_in_meeting(page):
                return True
            if await self._is_waiting_room(page):
                return False
            await asyncio.sleep(0.5)
        return await self._is_in_meeting(page)

    async def _is_waiting_room(self, page):
        waiting_markers = [
            'text=Someone in the call will let you in soon',
            'text=Alguém na chamada permitirá sua entrada em instantes',
            'text=Asking to join',
            'text=Pedindo para entrar',
            'text=Requested to join',
            'text=Solicitação enviada',
            'text=You can\'t join this call',
            'text=Você não pode entrar nesta chamada',
        ]
        for sel in waiting_markers:
            try:
                if await page.locator(sel).first.is_visible(timeout=500):
                    return True
            except Exception:
                continue
        return False

    async def _fill_guest_name_if_needed(self, page):
        name_inputs = [
            'input[aria-label*="Your name"]',
            'input[aria-label*="Seu nome"]',
            'input[placeholder*="name"]',
            'input[placeholder*="nome"]',
        ]
        for sel in name_inputs:
            try:
                field = page.locator(sel).first
                if await field.is_visible(timeout=600):
                    await field.fill(MEET_GUEST_NAME)
                    return True
            except Exception:
                continue
        return False

    async def _wait_for_host_approval(self, page, timeout_seconds):
        end = time.time() + max(5, timeout_seconds)
        while time.time() < end:
            if await self._is_in_meeting(page):
                log.info("Host approved join request")
                return True
            if await self._is_hard_blocked(page):
                return False
            await asyncio.sleep(1)
        return await self._is_in_meeting(page)

    async def _is_hard_blocked(self, page):
        blocked_markers = [
            'text=You can\'t join this video call',
            'text=Você não pode entrar nesta chamada de vídeo',
            'text=Returning to home screen',
            'text=Voltando para a tela inicial',
        ]
        for sel in blocked_markers:
            try:
                if await page.locator(sel).first.is_visible(timeout=500):
                    return True
            except Exception:
                continue
        return False

    async def _dump_join_debug(self, page, reason):
        ts = int(time.time())
        base = DEBUG_DIR / f"meet-{reason}-{ts}"
        try:
            await page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
        except Exception:
            pass

        try:
            title = await page.title()
        except Exception:
            title = "unknown"

        snippet = ""
        try:
            snippet = (await page.inner_text("body")).replace("\n", " ")[:700]
        except Exception:
            snippet = "(body text unavailable)"

        log.warning("Join debug saved: %s.* | title=%s | url=%s | text=%s", base, title, page.url, snippet)

    async def _record_audio(self, page, duration_seconds: int, max_duration_seconds: int = 14400) -> bytes | None:
        """Capture audio from browser using CDP media stream."""
        try:
            cdp = await page.context.new_cdp_session(page)
            await cdp.send("Emulation.setMediaStreamOverride", {
                "audio": True,
                "video": False,
            })

            if duration_seconds <= 0:
                log.info(f"Recording until meeting end (max {max_duration_seconds}s)...")
                raw_audio = await self._capture_audio_until_meeting_end(page, max_duration_seconds)
            else:
                log.info(f"Recording for {duration_seconds}s...")
                raw_audio = await self._capture_audio_chunks(page, duration_seconds)

            if not raw_audio:
                fallback_seconds = duration_seconds if duration_seconds > 0 else min(max_duration_seconds, 60)
                return self._generate_silence_wav(fallback_seconds)

            return raw_audio

        except Exception as e:
            log.warning(f"CDP audio capture failed: {e}, using silence")
            fallback_seconds = duration_seconds if duration_seconds > 0 else min(max_duration_seconds, 60)
            return self._generate_silence_wav(fallback_seconds)

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

    async def _capture_audio_until_meeting_end(self, page, max_duration_seconds: int) -> bytes | None:
        """Record audio and stop when the meeting appears to end, with a max cap."""
        js_code = """
        async () => {
            return new Promise((resolve, reject) => {
                try {
                    const inCallSelectors = [
                        'button[aria-label*="Leave call"]',
                        'button[aria-label*="Sair da chamada"]',
                        'button[aria-label*="Hang up"]'
                    ];

                    const isInCall = () => inCallSelectors.some((sel) => !!document.querySelector(sel));

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

                    recorder.ondataavailable = (e) => {
                        if (e && e.data && e.data.size > 0) chunks.push(e.data);
                    };

                    recorder.onstop = () => {
                        try {
                            const blob = new Blob(chunks, {type: 'audio/webm'});
                            const reader = new FileReader();
                            reader.onloadend = () => resolve(reader.result.split(',')[1]);
                            reader.readAsDataURL(blob);
                        } catch (err) {
                            reject(String(err));
                        }
                    };

                    recorder.start(1000);

                    const maxMs = %d * 1000;
                    const start = Date.now();
                    let missingChecks = 0;

                    const iv = setInterval(() => {
                        const elapsed = Date.now() - start;

                        if (isInCall()) {
                            missingChecks = 0;
                        } else {
                            missingChecks += 1;
                        }

                        if (elapsed >= maxMs || missingChecks >= 3) {
                            clearInterval(iv);
                            if (recorder.state !== 'inactive') recorder.stop();
                        }
                    }, 2000);
                } catch(e) { reject(e.message || String(e)); }
            });
        }
        """ % max_duration_seconds

        try:
            result = await page.evaluate(js_code)
            if result:
                import base64
                return base64.b64decode(result)
        except Exception as e:
            log.warning(f"Browser audio capture-until-end failed: {e}")

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
