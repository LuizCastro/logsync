"""
Transcriber — envia áudio para o Whisper local e retorna o transcript.
Usa a API do Whisper rodando em container separado.
"""

import logging
import os
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
            with open(audio_path, "rb") as f:
                audio_bytes = f.read()

            form = aiohttp.FormData()
            form.add_field("audio_file", audio_bytes, filename="audio.wav", content_type="audio/wav")
            form.add_field("language", language)
            form.add_field("task", "transcribe")

            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=form) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        text = result.get("text", "")
                        log.info(f"Whisper API transcription: {len(text)} chars")
                        return text
                    else:
                        log.warning(f"Whisper API returned {resp.status}")
                        return None
        except Exception as e:
            log.warning(f"Whisper API failed: {e}")
            return None
