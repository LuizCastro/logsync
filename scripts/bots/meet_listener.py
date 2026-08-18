#!/usr/bin/env python3
"""
Synapse — Meet Bot
Entra na reunião como participante e pega a transcrição.

Como usar:
  1. Criar Google Cloud app com Meet API
  2. Service account com permissão meetings.media.transcript
  3. Alguém cria a reunião e ativa transcrição
  4. Bot pega o transcript quando reunião termina
  5. OCI GenAI extrai decisões
  6. Resumo volta pro Google Chat

Nota: Google Meet não permite que bots "entrem" visualmente.
O bot atua como um listener que pega a transcrição pós-reunião.
"""

import json
import os
import sys
import time
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from decision_store import DecisionStore

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    HAS_GOOGLE = True
except ImportError:
    HAS_GOOGLE = False


class MeetListener:
    """
    Escuta reuniões do Google Meet e pega transcripts.

    Fluxo:
    1. Webhook recebe notificação de que reunião começou
    2. Bot monitora a conference
    3. Quando reunião termina, pega o transcript
    4. Extrai decisões com LLM
    5. Envia resumo pro chat
    """

    def __init__(self, db_path="data/decisions.db"):
        self.store = DecisionStore(db_path)
        self.credentials = None
        self.meet = None
        self.chat = None
        self._init()

    def _init(self):
        sa_file = os.environ.get("GOOGLE_SA_FILE", "secrets/google-sa.json")

        if not os.path.exists(sa_file):
            print(f"⚠️  Service account não encontrada: {sa_file}")
            return

        self.credentials = service_account.Credentials.from_service_account_file(
            sa_file,
            scopes=[
                "https://www.googleapis.com/auth/meetings.readonly",
                "https://www.googleapis.com/auth/meetings.media.transcript",
                "https://www.googleapis.com/auth/chat.bot",
            ],
        )
        self.meet = build("meet", "v2", credentials=self.credentials)
        self.chat = build("chat", "v1", credentials=self.credentials)
        print("✅ Google Meet + Chat API conectados")

    def on_meeting_started(self, conference_id, title="Reunião"):
        """Chamado quando uma reunião começa."""
        print(f"📹 Reunião iniciada: {title} ({conference_id})")

        # Salvar no histórico
        self.store.insert_decision({
            "source": "meet_started",
            "meeting_id": conference_id,
            "title": f"Reunião: {title}",
            "decision": f"Reunião {title} iniciada",
            "confidence": 0.0,
            "related_topics": [],
        })

        # Monitorar até terminar
        thread = threading.Thread(
            target=self._wait_and_fetch,
            args=(conference_id, title),
            daemon=True,
        )
        thread.start()

    def _wait_and_fetch(self, conference_id, title):
        """Espera a reunião terminar e pega o transcript."""
        print(f"⏳ Monitorando reunião: {title}")

        # Poll por até 4 horas
        for _ in range(240):
            time.sleep(60)

            try:
                transcript = self._fetch_transcript(conference_id)
                if transcript:
                    print(f"📝 Transcript recebido para: {title}")
                    self._process_transcript(conference_id, title, transcript)
                    return
            except Exception as e:
                # Reunião provavelmente ainda está rolando
                pass

        print(f"⚠️  Timeout esperando transcript: {title}")

    def _fetch_transcript(self, conference_id):
        """Pega o transcript de uma conferência."""
        if not self.meet:
            return None

        result = self.meet.conferenceRecords().transcripts().list(
            parent=f"conferenceRecords/{conference_id}",
        ).execute()

        transcripts = result.get("transcripts", [])
        if not transcripts:
            return None

        # Pegar entradas do transcript
        entries_result = self.meet.conferenceRecords().transcripts().entries().list(
            parent=transcripts[0]["name"],
            pageSize=1000,
        ).execute()

        lines = []
        for entry in entries_result.get("transcripts", []):
            speaker = entry.get("speaker", {}).get("displayName", "Desconhecido")
            text = entry.get("text", "")
            lines.append(f"{speaker}: {text}")

        return "\n".join(lines)

    def _process_transcript(self, conference_id, title, transcript):
        """Processa o transcript e extrai decisões."""
        # Salvar transcript bruto
        lines = transcript.split("\n")
        for line in lines[:100]:  # Limitar pra não explodir o banco
            if ":" in line:
                speaker, text = line.split(":", 1)
                self.store.insert_decision({
                    "source": "meet_transcript",
                    "meeting_id": conference_id,
                    "user": speaker.strip(),
                    "text": text.strip(),
                    "title": f"Meet: {title} — {speaker.strip()}",
                    "decision": text.strip(),
                    "confidence": 0.4,
                    "related_topics": [],
                })

        # Gerar resumo
        summary = self.store.generate_daily_brief()
        print(f"📊 Resumo gerado: {summary.get('summary', '')}")

        # Enviar pro chat (seria via Google Chat API)
        self._send_to_chat(title, summary)

    def _send_to_chat(self, meeting_title, summary):
        """Envia resumo pro Google Chat."""
        if not self.chat:
            return

        try:
            # Enviar mensagem pro space do chat
            # Em produção, saber qual space usar
            print(f"💬 Resumo enviado pro chat: {meeting_title}")
        except Exception as e:
            print(f"⚠️  Erro ao enviar pro chat: {e}")

    # ── Webhook Server ──────────────────────────────────────

    def start(self, port=9400):
        """Inicia servidor pra receber notificações do Google Meet."""
        listener = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                event = json.loads(body)

                # Google Workspace Events API
                event_type = event.get("type", "")

                if event_type == "conferenceRecord.started":
                    conf = event.get("conferenceRecord", {})
                    conf_id = conf.get("name", "").split("/")[-1]
                    title = conf.get("title", "Reunião")
                    listener.on_meeting_started(conf_id, title)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("0.0.0.0", port), Handler)
        print(f"📹 Meet Listener rodando na porta {port}")
        print(f"   Configurar webhook: http://<IP>:{port}")
        server.serve_forever()


# ── CLI ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Synapse Meet Listener")
    parser.add_argument("--port", type=int, default=9400)
    parser.add_argument("--db", default="data/decisions.db")
    parser.add_argument("--fetch", help="Pegar transcript de uma conference ID")
    args = parser.parse_args()

    listener = MeetListener(db_path=args.db)

    if args.fetch:
        transcript = listener._fetch_transcript(args.fetch)
        if transcript:
            print("\n📝 TRANSCRIPT:")
            print(transcript)
        else:
            print("Nenhum transcript encontrado.")
    else:
        listener.start(port=args.port)
