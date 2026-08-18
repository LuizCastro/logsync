#!/usr/bin/env python3
"""
Synapse — Email Bot
Extrai decisões de emails com notas de reunião.

Como usar:
  1. Criar email (ex: synapse@suaempresa.com)
  2. Configurar credenciais no .env
  3. Rodar: python3 email_bot.py
  4. Encaminhar notas de reunião pro email
  5. Bot responde com decisões extraídas

Funciona com:
  - Gmail (IMAP + SMTP)
  - Outlook (IMAP + SMTP)
  - Qualquer provedor IMAP
"""

import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import sys
import re
import time
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from decision_store import DecisionStore


# ── Config ───────────────────────────────────────────────────
IMAP_SERVER = os.environ.get("IMAP_SERVER", "imap.gmail.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
EMAIL_USER = os.environ.get("SYNAPSE_EMAIL", "")
EMAIL_PASS = os.environ.get("SYNAPSE_EMAIL_PASS", "")  # App password pro Gmail
CHECK_INTERVAL = int(os.environ.get("EMAIL_CHECK_INTERVAL", "30"))  # segundos

# Palavras que indicam que o email contém decisões
DECISION_MARKERS = [
    "decidimos", "decidido", "decisão", "vamos com", "fechado",
    "combinado", "definido", "escolhemos", "a opção é", "vai ser",
    "vamos usar", "próximos passos", "action items", "tarefas",
    "responsável", "prazo", "concluir até", "entregar até",
]


class EmailBot:
    """
    Bot que lê emails e extrai decisões de reuniões.

    Fluxo:
    1. Monitora inbox via IMAP
    2. Quando email chega com conteúdo de reunião
    3. Extrai decisões e action items
    4. Responde com resumo estruturado
    5. Armazena no SQLite
    """

    def __init__(self, db_path="data/decisions.db"):
        self.store = DecisionStore(db_path)
        self.imap = None
        self.running = False

    def connect(self):
        """Conecta ao servidor de email."""
        if not EMAIL_USER or not EMAIL_PASS:
            print("⚠️  Credenciais de email não configuradas.")
            print("   Set SYNAPSE_EMAIL e SYNAPSE_EMAIL_PASS no .env")
            print()
            print("   Gmail: usar App Password (não a senha normal)")
            print("   https://myaccount.google.com/apppasswords")
            return False

        try:
            self.imap = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
            self.imap.login(EMAIL_USER, EMAIL_PASS)
            self.imap.select("INBOX")
            print(f"✅ Conectado a {EMAIL_USER} via {IMAP_SERVER}")
            return True
        except Exception as e:
            print(f"❌ Erro ao conectar: {e}")
            return False

    def check_emails(self):
        """Verifica emails novos e processa."""
        if not self.imap:
            return

        try:
            # Buscar emails não lidos
            status, messages = self.imap.search(None, "UNSEEN")
            if status != "OK":
                return

            msg_ids = messages[0].split()
            if not msg_ids:
                return

            print(f"📬 {len(msg_ids)} email(s) novo(s)")

            for msg_id in msg_ids:
                self._process_email(msg_id)

        except Exception as e:
            print(f"⚠️  Erro ao verificar emails: {e}")
            # Reconectar
            try:
                self.imap.logout()
            except:
                pass
            self.connect()

    def _process_email(self, msg_id):
        """Processa um email individual."""
        try:
            status, data = self.imap.fetch(msg_id, "(RFC822)")
            if status != "OK":
                return

            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)

            subject = msg["Subject"] or "(sem assunto)"
            sender = msg["From"] or "desconhecido"
            date = msg["Date"] or ""
            body = self._get_body(msg)

            print(f"\n📧 De: {sender}")
            print(f"   Assunto: {subject}")
            print(f"   Tamanho: {len(body)} chars")

            # Verificar se parece nota de reunião
            if not self._looks_like_meeting_notes(subject, body):
                print(f"   ⏭️  Não parece nota de reunião, ignorando")
                return

            # Extrair decisões
            decisions = self._extract_decisions(subject, sender, body)

            if decisions:
                # Armazenar
                for d in decisions:
                    self.store.insert_decision(d)

                # Responder com resumo
                self._reply_with_summary(msg, decisions)
                print(f"   ✅ {len(decisions)} decisão(ões) extraída(s)")
            else:
                print(f"   ⚠️  Nenhuma decisão encontrada no email")

        except Exception as e:
            print(f"   ❌ Erro ao processar email: {e}")

    def _get_body(self, msg):
        """Extrai o corpo do email (texto puro)."""
        body = ""

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    body = part.get_payload(decode=True).decode(errors="ignore")
                    break
                elif content_type == "text/html" and not body:
                    # Fallback: pegar HTML e simplificar
                    html = part.get_payload(decode=True).decode(errors="ignore")
                    body = re.sub(r"<[^>]+>", " ", html)
                    body = re.sub(r"\s+", " ", body).strip()
        else:
            body = msg.get_payload(decode=True).decode(errors="ignore")

        return body

    def _looks_like_meeting_notes(self, subject, body):
        """Verifica se o email parece conter notas de reunião."""
        text = (subject + " " + body).lower()

        # Marcadores no assunto
        subject_markers = ["reunião", "meeting", "notas", "minutes", "resumo",
                          "decisões", "pauta", "agenda", "sprint", "standup"]
        if any(m in subject.lower() for m in subject_markers):
            return True

        # Marcadores no corpo
        body_markers = ["participantes", "presentes", "pauta", "decisões",
                       "próximos passos", "action items", "tarefas",
                       "responsável", "prazo", "concluir"]
        if sum(1 for m in body_markers if m in text) >= 2:
            return True

        # Muitos marcadores de decisão
        decision_count = sum(1 for m in DECISION_MARKERS if m in text)
        if decision_count >= 3:
            return True

        return False

    def _extract_decisions(self, subject, sender, body):
        """Extrai decisões do conteúdo do email."""
        decisions = []
        ts = datetime.now().isoformat()

        # Dividir o corpo em linhas
        lines = body.split("\n")

        # Estratégia simples: procurar linhas com marcadores de decisão
        current_section = ""
        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue

            # Detectar seções
            lower = line_stripped.lower()
            if any(s in lower for s in ["decisões", "decisão", "decidimos"]):
                current_section = "decisions"
            elif any(s in lower for s in ["próximos passos", "action items", "tarefas"]):
                current_section = "actions"
            elif any(s in lower for s in ["participantes", "presentes"]):
                current_section = "attendees"

            # Extrair decisões
            if current_section == "decisions" or any(m in lower for m in DECISION_MARKERS):
                if len(line_stripped) > 10:  # Ignorar linhas muito curtas
                    decisions.append({
                        "source": "email",
                        "channel": subject,
                        "user": sender.split("<")[0].strip() if "<" in sender else sender,
                        "text": line_stripped,
                        "timestamp": ts,
                        "title": line_stripped[:80],
                        "decision": line_stripped,
                        "confidence": 0.7,
                        "related_topics": [],
                    })

        # Se não encontrou decisões específicas, pegar o corpo inteiro
        if not decisions and len(body) > 50:
            # Dividir em parágrafos e processar cada um
            paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
            for para in paragraphs[:10]:  # Limitar a 10 parágrafos
                if any(m in para.lower() for m in DECISION_MARKERS):
                    decisions.append({
                        "source": "email",
                        "channel": subject,
                        "user": sender.split("<")[0].strip() if "<" in sender else sender,
                        "text": para[:500],
                        "timestamp": ts,
                        "title": para[:80],
                        "decision": para[:500],
                        "confidence": 0.6,
                        "related_topics": [],
                    })

        return decisions

    def _reply_with_summary(self, original_msg, decisions):
        """Responde ao email com um resumo das decisões."""
        try:
            # Criar resposta
            reply = MIMEMultipart()
            reply["From"] = EMAIL_USER
            reply["To"] = original_msg["From"]
            reply["Subject"] = f"Re: {original_msg['Subject']} — Decisões Extraídas"

            # Corpo da resposta
            lines = [
                "Olá!\n",
                "O Synapse processou as notas da reunião e extraiu as seguintes decisões:\n",
            ]

            for i, d in enumerate(decisions, 1):
                lines.append(f"{i}. {d['title']}")
                lines.append(f"   Decisão: {d['decision'][:200]}")
                lines.append(f"   Confiança: {d['confidence']:.0%}")
                lines.append("")

            # Action items pendentes
            pending = self.store.get_pending_actions()
            if pending:
                lines.append("\n📌 Action Items Pendentes:")
                for a in pending[:5]:
                    lines.append(f"  → {a['action']} (owner: {a.get('owner', '?')})")

            lines.append("\n---")
            lines.append("Enviado por Synapse — Decision Intelligence Agent")

            reply.attach(MIMEText("\n".join(lines), "plain", "utf-8"))

            # Enviar
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(EMAIL_USER, EMAIL_PASS)
                server.send_message(reply)

            print(f"   📤 Resposta enviada para {original_msg['From']}")

        except Exception as e:
            print(f"   ⚠️  Erro ao enviar resposta: {e}")

    def process_manual(self, text, subject="Nota de Reunião", sender="manual"):
        """Processa texto manualmente (sem email)."""
        print(f"\n📝 Processando: {subject}")
        decisions = self._extract_decisions(subject, sender, text)

        if decisions:
            for d in decisions:
                self.store.insert_decision(d)
            print(f"   ✅ {len(decisions)} decisão(ões) extraída(s)")
        else:
            print(f"   ⚠️  Nenhuma decisão encontrada")

        return decisions

    def run(self):
        """Loop principal: monitora inbox."""
        if not self.connect():
            return

        self.running = True
        print(f"\n🤖 Synapse Email Bot rodando!")
        print(f"   Monitorando: {EMAIL_USER}")
        print(f"   Intervalo: {CHECK_INTERVAL}s")
        print(f"   Banco: data/decisions.db")
        print(f"\n   Encaminhe notas de reunião pra {EMAIL_USER}")
        print(f"   Ctrl+C pra parar\n")

        while self.running:
            try:
                self.check_emails()
                time.sleep(CHECK_INTERVAL)
            except KeyboardInterrupt:
                print("\n👋 Bot encerrado.")
                self.running = False
            except Exception as e:
                print(f"⚠️  Erro: {e}")
                time.sleep(60)


# ── CLI ──────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Synapse Email Bot")
    parser.add_argument("--text", help="Processar texto direto (sem email)")
    parser.add_argument("--subject", default="Nota de Reunião")
    parser.add_argument("--db", default="data/decisions.db")
    args = parser.parse_args()

    bot = EmailBot(db_path=args.db)

    if args.text:
        # Modo manual: processar texto
        decisions = bot.process_manual(args.text, args.subject)
        if decisions:
            print("\n📋 Decisões extraídas:")
            for d in decisions:
                print(f"  • {d['title']}")
    else:
        # Modo normal: monitorar email
        bot.run()


if __name__ == "__main__":
    main()
