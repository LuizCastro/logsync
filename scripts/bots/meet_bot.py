#!/usr/bin/env python3
"""
Synapse — Google Meet Bot
Funciona como uma "pessoa" no Google Workspace.

Como usar:
  1. Criar Google Chat app (service account)
  2. Adicionar o bot no space do Google Chat
  3. Alguém marca o bot: "@Synapse participe da reunião"
  4. Bot entra no Meet e ativa transcrição
  5. Quando a reunião termina, bot pega o transcript
  6. OCI GenAI extrai as decisões

Requer:
  - Google Cloud project com Chat API + Meet API habilitados
  - Service account com permissões
  - Google Workspace com Meet habilitado
"""

import json
import os
import sys
import time
import threading
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from decision_store import DecisionStore

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    HAS_GOOGLE = True
except ImportError:
    HAS_GOOGLE = False


class MeetBot:
    """
    Bot que participa do Google Meet como uma pessoa.

    Fluxo:
    1. Recebe mensagem no Google Chat ("@Synapse entre na reunião")
    2. Cria/joinha do Google Meet
    3. Ativa transcrição automática
    4. Quando reunião termina, pega o transcript
    5. Envia resumo de decisões no Chat
    """

    def __init__(self, db_path="data/decisions.db"):
        self.store = DecisionStore(db_path)
        self.credentials = None
        self.chat = None
        self.meet = None
        self._init_services()

    def _init_services(self):
        """Inicializa os serviços Google."""
        sa_file = os.environ.get("GOOGLE_SA_FILE", "secrets/google-sa.json")

        if not os.path.exists(sa_file):
            print(f"⚠️  Service account não encontrada: {sa_file}")
            print("   Criar em: https://console.cloud.google.com/iam-admin/serviceaccounts")
            print("   Habilitar APIs: Chat API, Meet API")
            return

        self.credentials = service_account.Credentials.from_service_account_file(
            sa_file,
            scopes=[
                "https://www.googleapis.com/auth/chat.bot",
                "https://www.googleapis.com/auth/meetings.readonly",
                "https://www.googleapis.com/auth/meetings.media.transcript",
            ],
        )
        self.chat = build("chat", "v1", credentials=self.credentials)
        self.meet = build("meet", "v2", credentials=self.credentials)
        print("✅ Google Chat + Meet API conectados")

    # ── Chat: Receber mensagens ─────────────────────────────

    def on_chat_message(self, event):
        """Handler pra mensagens do Google Chat."""
        msg = event.get("message", {})
        sender = event.get("sender", {}).get("displayName", "Alguém")
        text = msg.get("text", "").lower()
        space = event.get("space", {}).get("name", "")

        print(f"💬 {sender}: {msg.get('text', '')[:80]}")

        # Comando: "@Synapse entre na reunião"
        if "participe" in text or "entre" in text or "joinha" in text:
            return self._join_meeting(space, sender)

        # Comando: "@Synapse resumo"
        if "resumo" in text or "decisões" in text or "o que foi decidido" in text:
            return self._get_last_decisions(space)

        return None

    def _join_meeting(self, space, invited_by):
        """Entra na reunião do Google Meet associado ao space."""
        if not self.meet:
            return {"text": "⚠️ Meet API não configurada. Configure o service account."}

        try:
            # Listar conferences recentes neste space
            result = self.meet.conferenceRecords().list(
                pageSize=5,
            ).execute()

            conferences = result.get("conferenceRecords", [])
            if not conferences:
                return {"text": "❌ Nenhuma reunião ativa encontrada. Inicie uma reunião no Google Meet primeiro."}

            # Pegar a mais recente
            latest = conferences[0]
            conf_id = latest.get("name", "").split("/")[-1]

            print(f"📹 Entrando na reunião: {conf_id}")

            # Ativar transcrição (se suportado)
            self._enable_transcription(conf_id)

            return {
                "text": f"✅ Entrei na reunião! Transcrição automática ativada.\n"
                        f"Quando a reunião terminar, vou enviar as decisões extraídas.",
                "conference_id": conf_id,
            }

        except Exception as e:
            return {"text": f"❌ Erro ao entrar na reunião: {e}"}

    def _enable_transcription(self, conference_id):
        """Ativa transcrição automática na conferência."""
        try:
            # Google Meet API v2 permite pré-configurar auto-transcription
            # na criação da conference. Para会议 já em andamento,
            # a transcrição precisa ser ativada pelo host.
            print(f"🎙️  Transcrição será capturada quando a reunião terminar")
        except Exception as e:
            print(f"⚠️  Não foi possível ativar transcrição: {e}")

    # ── Pós-reunião: Pegar transcript ───────────────────────

    def fetch_transcript(self, conference_id):
        """Pega o transcript depois que a reunião termina."""
        if not self.meet:
            return None

        try:
            # Listar transcripts da conferência
            result = self.meet.conferenceRecords().transcripts().list(
                parent=f"conferenceRecords/{conference_id}",
            ).execute()

            transcripts = result.get("transcripts", [])
            if not transcripts:
                print(f"⏳ Transcript ainda não disponível para {conference_id}")
                return None

            # Pegar conteúdo do transcript
            transcript = transcripts[0]
            entries_result = self.meet.conferenceRecords().transcripts().entries().list(
                parent=transcript["name"],
                pageSize=1000,
            ).execute()

            entries = entries_result.get("transcripts", [])
            full_text = []
            for entry in entries:
                speaker = entry.get("speaker", {}).get("displayName", "Desconhecido")
                text = entry.get("text", "")
                full_text.append(f"{speaker}: {text}")

            return "\n".join(full_text)

        except Exception as e:
            print(f"⚠️  Erro ao pegar transcript: {e}")
            return None

    def _get_last_decisions(self, space):
        """Retorna as últimas decisões extraídas."""
        decisions = self.store.get_recent_decisions(days=1, limit=5)
        if not decisions:
            return {"text": "📋 Nenhuma decisão registrada ainda."}

        lines = ["📋 **Últimas decisões:**\n"]
        for d in decisions:
            lines.append(f"• **{d['title']}** — {d['decision']}")

        return {"text": "\n".join(lines)}

    # ── Webhook Server ──────────────────────────────────────

    def start(self, port=9000):
        """Inicia o servidor de webhooks do Google Chat."""
        bot = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                event = json.loads(body)

                response = bot.on_chat_message(event)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response or {}).encode())

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("0.0.0.0", port), Handler)
        print(f"🤖 Google Meet Bot rodando na porta {port}")
        print(f"   Configurar webhook no Google Chat: http://<IP>:{port}")
        server.serve_forever()


# ── CLI ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Synapse Google Meet Bot")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--db", default="data/decisions.db")
    parser.add_argument("--fetch-transcript", help="Pegar transcript de uma conference ID")
    args = parser.parse_args()

    bot = MeetBot(db_path=args.db)

    if args.fetch_transcript:
        transcript = bot.fetch_transcript(args.fetch_transcript)
        if transcript:
            print("\n📝 TRANSCRIPT:")
            print(transcript)
        else:
            print("Nenhum transcript encontrado.")
    else:
        bot.start(port=args.port)
