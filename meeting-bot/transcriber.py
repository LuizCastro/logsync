"""
Transcriber — envia áudio para o Whisper local e retorna o transcript.
Usa a API do Whisper rodando em container separado.
"""

import logging
import os
import json
from pathlib import Path

import aiohttp

log = logging.getLogger("synapse-bot.transcriber")

WHISPER_API_URL = os.getenv("WHISPER_URL", "http://synapse-whisper:9000")


class Transcriber:
    def __init__(self, whisper_url: str = None):
        self.whisper_url = whisper_url or WHISPER_API_URL

    async def transcribe(self, audio_path: str, language: str = "pt") -> str | None:
        log.info(f"Transcribing: {audio_path}")

        try:
            url = f"{self.whisper_url}/asr"
            ext = Path(audio_path).suffix.lower()
            if ext == ".webm":
                upload_name = "audio.webm"
                content_type = "audio/webm"
            else:
                upload_name = "audio.wav"
                content_type = "audio/wav"

            with open(audio_path, "rb") as f:
                audio_bytes = f.read()

            form = aiohttp.FormData()
            form.add_field("audio_file", audio_bytes, filename=upload_name, content_type=content_type)
            form.add_field("language", language)
            form.add_field("task", "transcribe")

            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=form) as resp:
                    if resp.status == 200:
                        content_type = (resp.headers.get("Content-Type") or "").lower()
                        raw_body = await resp.text()

                        text = ""
                        if "application/json" in content_type:
                            try:
                                result = json.loads(raw_body)
                                text = (result.get("text") or "").strip()
                            except Exception:
                                text = ""
                        else:
                            # Some whisper images return plain text even on 200.
                            # Try JSON first, then treat the body as transcript text.
                            try:
                                result = json.loads(raw_body)
                                text = (result.get("text") or "").strip()
                            except Exception:
                                text = raw_body.strip()

                        log.info(f"Whisper API transcription: {len(text)} chars")
                        return text or None
                    else:
                        log.warning(f"Whisper API returned {resp.status}")
                        return None
        except Exception as e:
            log.warning(f"Whisper API failed: {e}")
            return None
