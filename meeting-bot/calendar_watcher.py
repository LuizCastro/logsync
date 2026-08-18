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

CALENDAR_PROVIDER = os.getenv("CALENDAR_PROVIDER", "hotmail").strip().lower()
BOT_EMAIL = os.getenv("BOT_EMAIL") or os.getenv("GOOGLE_BOT_EMAIL", "")
BOT_PASSWORD = os.getenv("BOT_PASSWORD") or os.getenv("GOOGLE_BOT_PASSWORD", "")
PROCESSED_DIR = Path("/app/data/processed")
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

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
            await self.page.goto("https://login.live.com/", wait_until="domcontentloaded", timeout=60000)
            await self.page.wait_for_timeout(5000)

            email_input = self.page.locator('input[type="email"]')
            await email_input.wait_for(timeout=15000)
            await email_input.fill(BOT_EMAIL)
            # The Outlook login screen can change element IDs frequently.
            # Press Enter first, then try known submit button selectors.
            await email_input.press("Enter")
            await self._click_first_available([
                "#idSIButton9",
                'button[type="submit"]',
                'input[type="submit"]',
            ])
            await self.page.wait_for_timeout(5000)

            pw_input = self.page.locator('input[type="password"]')
            await pw_input.wait_for(timeout=15000)
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
            log.error(f"Login error: {e}")
            return False

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
