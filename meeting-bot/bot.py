#!/usr/bin/env python3
"""
Synapse Meeting Bot — Monitora Outlook/Hotmail, entra em reuniões, transcreve.
Fluxo: Playwright monitora calendário → Bot entra no Meet → Grava → Whisper → n8n
"""

import asyncio
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from calendar_watcher import CalendarWatcher
from meet_joiner import MeetJoiner
from transcriber import Transcriber

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("synapse-bot")

N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "http://synapse-n8n:5678/webhook/synapse-meeting")
WHISPER_URL = os.getenv("WHISPER_URL", "http://synapse-whisper:9000")
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "3"))
RECORDING_DURATION = int(os.getenv("RECORDING_DURATION_SECONDS", "600"))
WEBHOOK_PORT = int(os.getenv("BOT_WEBHOOK_PORT", "9001"))
CALENDAR_ENABLED = os.getenv("CALENDAR_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
RECORD_UNTIL_END = os.getenv("RECORD_UNTIL_END", "true").strip().lower() in {"1", "true", "yes", "on"}
MAX_RECORDING_SECONDS = int(os.getenv("MAX_RECORDING_SECONDS", "14400"))


class MeetingBot:
    def __init__(self):
        self.calendar = CalendarWatcher()
        self.joiner = MeetJoiner()
        self.transcriber = Transcriber(WHISPER_URL)
        self.processed_events = set()

    async def start(self):
        log.info("Synapse Meeting Bot starting...")

        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()

        await self.calendar.init(self.playwright)
        await self.joiner.init()

        if CALENDAR_ENABLED:
            asyncio.create_task(self._calendar_loop())
        else:
            log.warning("Calendar watcher disabled by CALENDAR_ENABLED=false; manual /join mode only")

        app = web.Application()
        app.router.add_post("/join", self.handle_join)
        app.router.add_get("/health", self.handle_health)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
        await site.start()

        log.info(f"Bot webhook on port {WEBHOOK_PORT}")
        if CALENDAR_ENABLED:
            log.info(f"Calendar check every {CHECK_INTERVAL_MINUTES} min")
            log.info("Invite the bot email to a meeting and it will join automatically!")
        else:
            log.info("Calendar auto-join disabled; use POST /join for manual meeting capture")

        await asyncio.Event().wait()

    async def handle_join(self, request):
        try:
            data = await request.json()
            meet_url = data.get("url") or data.get("meet_url")
            if not meet_url:
                return web.json_response({"error": "Missing 'url'"}, status=400)

            meeting_id = data.get("meeting_id", f"manual_{int(time.time())}")
            participants = data.get("participants", [])
            title = data.get("title", "Manual Meeting")
            duration = data.get("duration_seconds")

            log.info(f"Manual join: {meet_url}")
            asyncio.create_task(self._handle_meeting({
                "id": meeting_id,
                "summary": title,
                "meet_url": meet_url,
                "participants": participants,
            }, duration))

            return web.json_response({
                "status": "joining",
                "meeting_id": meeting_id,
            })

        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_health(self, request):
        return web.json_response({
            "status": "ok",
            "bot": "synapse-meeting-bot",
            "calendar": "outlook-playwright" if CALENDAR_ENABLED else "disabled",
        })

    async def _calendar_loop(self):
        while True:
            try:
                await self.check_calendar()
            except Exception as e:
                log.error(f"Calendar loop error: {e}")
            await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)

    async def check_calendar(self):
        try:
            meetings = await self.calendar.get_upcoming_meetings(minutes_ahead=15)
            for meeting in meetings:
                mid = meeting.get("id")
                if mid in self.processed_events:
                    continue
                self.processed_events.add(mid)
                asyncio.create_task(self._handle_meeting(meeting))
        except Exception as e:
            log.error(f"Calendar check failed: {e}")

    async def _handle_meeting(self, meeting, duration=None):
        if duration is None:
            duration = 0 if RECORD_UNTIL_END else RECORDING_DURATION
        else:
            try:
                duration = int(duration)
            except (TypeError, ValueError):
                duration = RECORDING_DURATION

        summary = meeting.get("summary", "Untitled")
        meet_url = meeting.get("meet_url")
        meeting_id = meeting.get("id", "unknown")
        participants = meeting.get("participants", [])

        log.info(f"Joining: {summary}")

        audio_path = await self.joiner.join_and_record(
            meet_url,
            duration_seconds=duration,
            max_duration_seconds=MAX_RECORDING_SECONDS,
        )
        if not audio_path:
            log.error(f"Failed to record: {summary}")
            return

        transcript = await self.transcriber.transcribe(audio_path)
        captions_text = self._read_captions_sidecar(audio_path)

        if self._is_low_quality_transcript(transcript) and captions_text:
            log.warning("Whisper transcript is low quality; using Meet captions fallback")
            transcript = captions_text

        if not transcript:
            log.warning(
                "Transcription unavailable for '%s'; sending fallback transcript to keep pipeline running",
                summary,
            )
            transcript = (
                f"No transcript captured for meeting '{summary}'. "
                f"Recorded at {datetime.utcnow().isoformat()}."
            )

        await self._send_to_n8n({
            "meeting_id": meeting_id,
            "transcript": transcript,
            "participants": participants,
            "title": summary,
            "source": "meeting_bot",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })

        try:
            os.unlink(audio_path)
        except OSError:
            pass
        captions_path = f"{audio_path}.captions.txt"
        try:
            os.unlink(captions_path)
        except OSError:
            pass
        log.info(f"Done: {summary}")

    def _read_captions_sidecar(self, audio_path: str) -> str | None:
        path = Path(f"{audio_path}.captions.txt")
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8").strip()
            return text or None
        except Exception:
            return None

    def _is_low_quality_transcript(self, text: str | None) -> bool:
        if not text:
            return True
        tokens = [t for t in text.lower().split() if t.isalpha()]
        if len(tokens) < 4:
            return True
        unique = set(tokens)
        if len(unique) <= 2:
            return True
        return False

    async def _send_to_n8n(self, payload):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(N8N_WEBHOOK_URL, json=payload) as resp:
                    result = await resp.json()
                    log.info(f"Sent to n8n: {result}")
        except Exception as e:
            log.error(f"Failed to send to n8n: {e}")


async def main():
    bot = MeetingBot()
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
