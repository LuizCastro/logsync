#!/usr/bin/env python3
"""
Synapse — Chat Bot
Funciona como uma "pessoa" no chat do time.

Como usar:
  1. Criar app no Slack/Teams/Google Chat
  2. Adicionar o bot no canal
  3. Bot escuta TUDO que o time fala
  4. Quando detecta uma decisão, extrai e armazena
  5. Periodicamente posta resumo: "Decisões de hoje"

Suporta: Slack (Socket Mode), Google Chat (webhook), Teams (Bot Framework)
"""

import json
import os
import sys
import re
import time
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from decision_store import DecisionStore


# ── Config ───────────────────────────────────────────────────
PLATFORM = os.environ.get("SYNAPSE_PLATFORM", "slack")  # slack | google | teams
PORT = int(os.environ.get("CHAT_BOT_PORT", "9200"))

# Palavras que indicam que uma decisão foi tomada
DECISION_KEYWORDS = [
    "decidimos", "decidido", "decisão", "vamos com", "fechado",
    "combinado", "ok", "bora", "pode ser", "feito", "definido",
    "escolhemos", "a opção é", "a resposta é", "vai ser",
    "vamos usar", "a escolha", "tá", "beleza", "combinado",
]


class ChatBot:
    """
    Bot que participa do chat como mais uma pessoa.

    - Escuta todas as mensagens
    - Detecta decisões por palavras-chave
    - Usa LLM pra confirmar e extrair
    - Armazena no SQLite
    - Posta resumo quando pedem
    """

    def __init__(self, db_path="data/decisions.db"):
        self.store = DecisionStore(db_path)
        self.channel_history = {}  # channel_id -> [messages]

    def on_message(self, channel, user, text, ts=None):
        """Chamado quando uma mensagem chega do chat."""
        # Ignorar mensagens do próprio bot
        if user in ("synapse", "Synapse", "bot"):
            return

        # Salvar no histórico
        if channel not in self.channel_history:
            self.channel_history[channel] = []
        self.channel_history[channel].append({
            "user": user,
            "text": text,
            "ts": ts or datetime.now().isoformat(),
        })

        # Manter só as últimas 50 mensagens por canal
        self.channel_history[channel] = self.channel_history[channel][-50:]

        # Detectar se é uma decisão
        if self._looks_like_decision(text):
            self._process_decision(channel, user, text)

        # Detectar pedido de resumo
        if self._is_summary_request(text):
            return self._get_summary(channel)

        return None

    def _looks_like_decision(self, text):
        """Verifica se a mensagem parece uma decisão."""
        text_lower = text.lower()
        return any(kw in text_lower for kw in DECISION_KEYWORDS)

    def _process_decision(self, channel, user, text):
        """Processa uma possível decisão."""
        # Extrair contexto das últimas 5 mensagens
        context = self.channel_history.get(channel, [])[-5:]
        context_text = "\n".join([f"{m['user']}: {m['text']}" for m in context])

        # Armazenar com confidence baixa (LLM vai confirmar depois)
        decision_id = self.store.insert_decision({
            "source": "chat",
            "channel": channel,
            "user": user,
            "text": text,
            "context": context_text,
            "title": f"Decisão de {user}",
            "decision": text,
            "confidence": 0.6,
            "related_topics": [],
        })

        print(f"📋 Decisão detectada [{decision_id[:8]}]: {user}: {text[:60]}")

        # Reagir na mensagem (depende da plataforma)
        return {
            "action": "react",
            "emoji": "✅",
            "decision_id": decision_id,
        }

    def _is_summary_request(self, text):
        """Verifica se alguém pediu um resumo."""
        triggers = [
            "resumo", "decisões", "o que foi decidido",
            "o que decidimos", "quais decisões", "síntese",
            "me conta as decisões", "lista de decisões",
        ]
        return any(t in text.lower() for t in triggers)

    def _get_summary(self, channel):
        """Retorna resumo das decisões recentes."""
        decisions = self.store.get_recent_decisions(days=1, limit=10)

        if not decisions:
            return {"text": "📋 Nenhuma decisão registrada hoje."}

        lines = ["📋 **Decisões de hoje:**\n"]
        for d in decisions:
            owner = d.get('owner') or d.get('user', '?')
            lines.append(f"• **{d['title']}** — {d['decision']} _(owner: {owner})_")

        pending = self.store.get_pending_actions()
        if pending:
            lines.append(f"\n📌 **{len(pending)} action items pendentes**")

        return {"text": "\n".join(lines)}

    def get_periodic_summary(self):
        """Gera resumo periódico (pode ser chamado por cron/n8n)."""
        decisions = self.store.get_recent_decisions(days=1)
        actions = self.store.get_pending_actions()

        if not decisions:
            return None

        lines = ["⏰ **Resumo automático — Synapse**\n"]
        lines.append(f"📊 {len(decisions)} decisões capturadas hoje\n")

        for d in decisions:
            lines.append(f"• {d['title']}: {d['decision']}")

        if actions:
            lines.append(f"\n📌 {len(actions)} action items pendentes:")
            for a in actions[:5]:
                lines.append(f"  → {a['action']} (owner: {a.get('owner', '?')})")

        return "\n".join(lines)


# ── Slack Socket Mode ────────────────────────────────────────

class SlackBot(ChatBot):
    """Bot Slack usando Socket Mode (sem URL pública)."""

    def __init__(self, db_path="data/decisions.db"):
        super().__init__(db_path)
        self.app_token = os.environ.get("SLACK_APP_TOKEN", "")
        self.bot_token = os.environ.get("SLACK_BOT_TOKEN", "")

    def start(self):
        """Inicia o bot Slack."""
        if not self.app_token or not self.bot_token:
            print("⚠️  Slack tokens não configurados.")
            print("   Set SLACK_APP_TOKEN e SLACK_BOT_TOKEN")
            print("   Como criar: https://api.slack.com/apps")
            print()
            print("   1. Criar app em https://api.slack.com/apps")
            print("   2. Ativar Socket Mode")
            print("   3. Adicionar Event Subscriptions: message.channels, message.im")
            print("   4. Instalar no workspace")
            print("   5. Copiar tokens")
            return

        try:
            from slack_sdk.socket_mode import SocketModeClient
            from slack_sdk.web import WebClient
            from slack_sdk.socket_mode.request import SocketModeRequest
            from slack_sdk.socket_mode.response import SocketModeResponse
        except ImportError:
            print("❌ Instale: pip install slack-sdk")
            return

        client = SocketModeClient(
            app_token=self.app_token,
            web_client=WebClient(token=self.bot_token),
        )

        def handle(client, req: SocketModeRequest):
            if req.type == "events_api":
                response = SocketModeResponse(envelope_id=req.envelope_id)
                client.send_socket_mode_response(response)

                event = req.payload.get("event", {})
                if event.get("type") == "message":
                    channel = event.get("channel", "")
                    user = event.get("user", "")
                    text = event.get("text", "")

                    result = self.on_message(channel, user, text)

                    if result and result.get("action") == "react":
                        try:
                            client.web_client.reactions_add(
                                name=result["emoji"],
                                channel=channel,
                                timestamp=event.get("ts", ""),
                            )
                        except Exception:
                            pass

                    if result and result.get("text"):
                        client.web_client.chat_postMessage(
                            channel=channel,
                            text=result["text"],
                        )

        client.socket_mode_request_listeners.append(handle)
        client.connect()
        print("🤖 Slack Bot conectado via Socket Mode!")
        print("   Escutando mensagens em tempo real...")

        from threading import Event
        Event().wait()


# ── Google Chat ──────────────────────────────────────────────

class GoogleChatBot(ChatBot):
    """Bot Google Chat via webhook."""

    def start(self, port=9200):
        bot = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                event = json.loads(body)

                if event.get("type") == "MESSAGE":
                    msg = event.get("message", {})
                    sender = event.get("sender", {}).get("displayName", "unknown")
                    space = event.get("space", {}).get("name", "")
                    text = msg.get("text", "")

                    result = bot.on_message(space, sender, text)
                    response = result if result else {}
                else:
                    response = {}

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("0.0.0.0", port), Handler)
        print(f"🤖 Google Chat Bot rodando na porta {port}")
        server.serve_forever()


# ── Teams Bot ────────────────────────────────────────────────

class TeamsChatBot(ChatBot):
    """Bot Microsoft Teams via Bot Framework."""

    def start(self, port=9300):
        bot = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                activity = json.loads(body)

                if activity.get("type") == "message":
                    channel = activity.get("channelId", "")
                    user = activity.get("from", {}).get("name", "unknown")
                    text = activity.get("text", "")

                    result = bot.on_message(channel, user, text)
                    response = result if result else {"status": "ok"}
                else:
                    response = {"status": "ok"}

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("0.0.0.0", port), Handler)
        print(f"🤖 Teams Bot rodando na porta {port}")
        server.serve_forever()


# ── Main ─────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Synapse Chat Bot")
    parser.add_argument("--platform", choices=["slack", "google", "teams"], default=PLATFORM)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--db", default="data/decisions.db")
    parser.add_argument("--summary", action="store_true", help="Gerar resumo periódico")
    args = parser.parse_args()

    if args.summary:
        bot = ChatBot(db_path=args.db)
        summary = bot.get_periodic_summary()
        if summary:
            print(summary)
        else:
            print("Nenhuma decisão registrada hoje.")
        return

    if args.platform == "slack":
        bot = SlackBot(db_path=args.db)
        bot.start()
    elif args.platform == "google":
        bot = GoogleChatBot(db_path=args.db)
        bot.start(port=args.port)
    elif args.platform == "teams":
        bot = TeamsChatBot(db_path=args.db)
        bot.start(port=args.port)


if __name__ == "__main__":
    main()
