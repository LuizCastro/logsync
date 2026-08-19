"""
Calendar watcher — monitora inbox do Outlook/Hotmail via Playwright.
Detecta convites de reunião nos e-mails e extrai URLs de meet.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("synapse-bot.calendar")


def _clean_env(value):
    if not value:
        return ""
    return value.strip().strip('"').strip("'")

CALENDAR_PROVIDER = os.getenv("CALENDAR_PROVIDER", "hotmail").strip().lower()
BOT_EMAIL = _clean_env(os.getenv("BOT_EMAIL") or os.getenv("GOOGLE_BOT_EMAIL", ""))
BOT_PASSWORD = _clean_env(os.getenv("BOT_PASSWORD") or os.getenv("GOOGLE_BOT_PASSWORD", ""))
PROCESSED_DIR = Path("/app/data/processed")
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
DEBUG_DIR = Path("/app/data/debug")
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

MEETING_URLS = re.compile(
    r'https://teams\.microsoft\.com/l/meetup-join/[^\s"\'<>]+|'
    r'https://[^\s"\'<>]*\.zoom\.us/[^\s"\'<>]+|'
    r'https://meet\.google\.com/[a-z0-9-]+|'
    r'https://[^\s"\'<>]*look\.live\.com[^\s"\'<>]*|'
    r'https://[^\s"\'<>]*teams\.live\.com[^\s"\'<>]+'
)


class CalendarWatcher:
    def __init__(self):
        self.browser = None
        self.page = None
        self.logged_in = False

    async def init(self, playwright):
        self.browser = await playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
        )
        self.page = await context.new_page()

    async def login(self):
        if self.logged_in:
            return True
        if not BOT_EMAIL or not BOT_PASSWORD:
            log.warning("No email credentials configured")
            return False

        if CALENDAR_PROVIDER not in {"hotmail", "outlook", "live"}:
            log.warning(
                "CALENDAR_PROVIDER=%s is not explicitly supported by this watcher. "
                "Proceeding with Outlook login flow.",
                CALENDAR_PROVIDER,
            )

        try:
            log.info("Navigating to Outlook login (provider=%s)...", CALENDAR_PROVIDER)
            await self.page.goto("https://login.live.com/login.srf", wait_until="domcontentloaded", timeout=60000)
            await self.page.wait_for_timeout(5000)

            await self._prepare_microsoft_login_screen()

            email_input = await self._wait_for_first_visible([
                'input[type="email"]',
                'input[name="loginfmt"]',
                '#i0116',
            ], timeout_ms=20000)
            if email_input is None:
                raise RuntimeError("email input not found")

            await email_input.fill(BOT_EMAIL)
            advanced = await self._advance_after_email()
            if not advanced:
                if await self._is_account_not_found_state():
                    await self._dump_login_debug("account-not-found")
                    raise RuntimeError(
                        "microsoft account not found for configured BOT_EMAIL; check exact email in .env"
                    )

            pw_input = await self._wait_for_first_visible([
                '#i0118',
                'input[name="passwd"]',
                'input[type="password"]',
            ], timeout_ms=25000)
            if pw_input is None:
                await self._dump_login_debug("password-not-found")
                raise RuntimeError("password input not found")

            await pw_input.fill(BOT_PASSWORD)
            await pw_input.press("Enter")
            await self._click_first_available([
                "#idSIButton9",
                'button[type="submit"]',
                'input[type="submit"]',
            ])
            await self.page.wait_for_timeout(8000)

            try:
                stay = self.page.locator('#idSIButton9')
                text = await stay.inner_text()
                if "yes" in text.lower() or "sim" in text.lower():
                    await stay.click()
                    await self.page.wait_for_timeout(3000)
            except Exception:
                pass

            url = self.page.url
            if "login" not in url:
                self.logged_in = True
                log.info(f"Login successful, URL: {url}")
                return True
            else:
                log.error(f"Login failed, still on: {url}")
                return False

        except Exception as e:
            log.error("Login error: %s | url=%s", e, self.page.url if self.page else "unknown")
            return False

    async def _prepare_microsoft_login_screen(self):
        """Handle account picker or alternate entry links before typing email."""
        # Cookie/privacy banners can block interactions in headless mode.
        await self._click_first_available([
            '#wcpConsentBannerCtrl button[type="submit"]',
            'button:has-text("Accept")',
            'button:has-text("Aceitar")',
        ])

        await self._click_first_available([
            'text=Use another account',
            'text=Usar outra conta',
            'text=Use a different account',
            'text=Sign in with another account',
        ])

    async def _advance_after_email(self):
        """Try multiple submit paths until password screen is available."""
        password_selectors = ['#i0118', 'input[name="passwd"]', 'input[type="password"]']

        for _ in range(4):
            if await self._is_any_visible(password_selectors):
                return True

            # In some flows, selecting the account tile is required after email step.
            await self._click_first_available([
                f'text={BOT_EMAIL}',
                '[data-test-id="account"]',
                '[role="button"][data-report-event*="Signin"]',
            ])

            # Some Microsoft account flows require switching to password-based auth.
            await self._click_first_available([
                'text=Sign-in options',
                'text=Opções de entrada',
                'text=Use your password',
                'text=Usar sua senha',
                'text=Use password instead',
                'text=Usar senha',
            ])

            # Enter first, then click likely submit controls.
            try:
                email_input = self.page.locator('#i0116, input[name="loginfmt"], input[type="email"]').first
                if await email_input.is_visible(timeout=600):
                    await email_input.press("Enter")
            except Exception:
                pass

            await self._click_first_available([
                '#idSIButton9',
                'input[type="submit"]',
                'button[type="submit"]',
                'button:has-text("Next")',
                'button:has-text("Próximo")',
                'text=Next',
                'text=Próximo',
                'text=Use password instead',
                'text=Usar senha',
            ])

            # Some flows return to account picker and require selecting "use another account" again.
            await self._click_first_available([
                'text=Use another account',
                'text=Usar outra conta',
            ])

            try:
                await self.page.keyboard.press("Enter")
            except Exception:
                pass

            await self.page.wait_for_timeout(1800)

        return await self._is_any_visible(password_selectors)

    async def _is_account_not_found_state(self):
        """Detect when Microsoft rejects the account identifier before password step."""
        try:
            body_text = (await self.page.inner_text("body")).lower()
        except Exception:
            return False

        markers = [
            "we couldn't find a microsoft account",
            "não foi possível encontrar uma conta microsoft",
            "couldn't find a microsoft account",
        ]
        return any(m in body_text for m in markers)

    async def _is_any_visible(self, selectors):
        for sel in selectors:
            try:
                if await self.page.locator(sel).first.is_visible(timeout=500):
                    return True
            except Exception:
                continue
        return False

    async def _dump_login_debug(self, reason):
        """Persist page artifacts to diagnose login flow changes on Microsoft pages."""
        ts = int(time.time())
        base = DEBUG_DIR / f"login-{reason}-{ts}"
        try:
            await self.page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
        except Exception:
            pass

        try:
            html = await self.page.content()
            base.with_suffix(".html").write_text(html, encoding="utf-8")
        except Exception:
            pass

        try:
            title = await self.page.title()
        except Exception:
            title = "unknown"

        snippet = ""
        try:
            body_text = await self.page.inner_text("body")
            snippet = re.sub(r"\s+", " ", body_text)[:800]
        except Exception:
            snippet = "(body text unavailable)"

        log.error("Login debug dump saved: %s.* | title=%s | text_snippet=%s", base, title, snippet)

    async def _wait_for_first_visible(self, selectors, timeout_ms=15000):
        """Return the first selector that becomes visible within timeout."""
        deadline = time.time() + (timeout_ms / 1000)
        while time.time() < deadline:
            for sel in selectors:
                try:
                    candidate = self.page.locator(sel).first
                    if await candidate.is_visible(timeout=500):
                        return candidate
                except Exception:
                    continue
            await self.page.wait_for_timeout(300)
        return None

    async def _click_first_available(self, selectors):
        """Try to click the first visible submit-like element, if present."""
        for sel in selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=1200):
                    await btn.click()
                    return True
            except Exception:
                continue
        return False

    async def get_upcoming_meetings(self, minutes_ahead=15):
        manual = self._check_pending_file()
        if manual:
            return manual

        if not await self.login():
            return []

        try:
            log.info("Checking inbox for meeting invites...")
            await self.page.goto("https://outlook.live.com/mail/0/inbox", wait_until="domcontentloaded", timeout=60000)
            await self.page.wait_for_timeout(8000)
            log.info(f"Inbox page: {self.page.url}")

            meetings = await self._scan_inbox()
            return meetings

        except Exception as e:
            log.error(f"Inbox check error: {e}")
            return []

    def _check_pending_file(self):
        pending_file = Path("/app/data/pending_meetings.json")
        if pending_file.exists():
            try:
                meetings = json.loads(pending_file.read_text())
                pending_file.unlink()
                if meetings:
                    log.info(f"Processing {len(meetings)} manual meeting(s)")
                    return meetings
            except Exception as e:
                log.error(f"Failed to read pending: {e}")
        return []

    async def _scan_inbox(self):
        meetings = []
        try:
            page_text = await self.page.inner_text("body")

            invites = re.findall(
                r'(?i)(invite|convite|meeting|reunião|join|entrar).*?(https?://[^\s]+)',
                page_text
            )

            for prefix, url in invites:
                if not MEETING_URLS.search(url):
                    continue
                event_id = str(hash(url))[:16]
                processed = PROCESSED_DIR / f"{event_id}.txt"
                if processed.exists():
                    continue

                meetings.append({
                    "id": event_id,
                    "summary": prefix.strip()[:100],
                    "meet_url": url.split()[0],
                    "participants": [],
                    "start": datetime.now(timezone.utc).isoformat(),
                })
                processed.write_text(datetime.now().isoformat())
                log.info(f"Found meeting invite: {url.split()[0]}")

            if not meetings:
                all_urls = MEETING_URLS.findall(page_text)
                for url in all_urls:
                    event_id = str(hash(url))[:16]
                    processed = PROCESSED_DIR / f"{event_id}.txt"
                    if processed.exists():
                        continue
                    meetings.append({
                        "id": event_id,
                        "summary": "Meeting from email",
                        "meet_url": url,
                        "participants": [],
                        "start": datetime.now(timezone.utc).isoformat(),
                    })
                    processed.write_text(datetime.now().isoformat())
                    log.info(f"Found meet URL: {url}")

        except Exception as e:
            log.error(f"Inbox scan error: {e}")

        return meetings
