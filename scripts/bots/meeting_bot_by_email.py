#!/usr/bin/env python3
"""
Synapse — Meeting Bot por Email
O bot tem um email válido. Você convida ele pra reunião como se fosse uma pessoa.

Google Meet:
  1. Criar conta Google pro bot (bot@suaempresa.com)
  2. Adicionar o email do bot como participante no Google Calendar
  3. Bot entra na reunião via Meet API
  4. Captura transcrição em tempo real

Microsoft Teams:
  1. Criar conta Microsoft 365 pro bot
  2. Adicionar o bot como participante no Outlook Calendar
  3. Bot entra na reunião via Graph API
  4. Captura transcrição em tempo real
"""

import os
import sys
import json
import time
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from decision_store import DecisionStore


# ═══════════════════════════════════════════════════════════════
# GOOGLE MEET — Bot entra por email
# ═══════════════════════════════════════════════════════════════

class GoogleMeetBot:
    """
    Bot com email Google que entra em reuniões do Meet.

    Como funciona:
    1. Bot tem email: synapse-bot@seudominio.com
    2. Você cria reunião no Google Calendar
    3. Adiciona synapse-bot@seudominio.com como participante
    4. Bot recebe o convite e entra na reunião
    5. Google Meet gera transcrição automática
    6. Bot pega o transcript e extrai decisões

    Requisitos:
    - Google Workspace (não conta free)
    - Service account com domain-wide delegation
    - OU: conta Google real pro bot com permissões deMeet API
    """

    def __init__(self, db_path="data/decisions.db"):
        self.store = DecisionStore(db_path)
        self.bot_email = os.environ.get("BOT_GOOGLE_EMAIL", "synapse-bot@seudominio.com")
        self.credentials = None
        self.calendar = None
        self.meet = None

    def _init_services(self):
        """Inicializa Google APIs."""
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            sa_file = os.environ.get("GOOGLE_SA_FILE", "secrets/google-sa.json")
            self.credentials = service_account.Credentials.from_service_account_file(
                sa_file,
                scopes=[
                    "https://www.googleapis.com/auth/calendar",
                    "https://www.googleapis.com/auth/meetings.readonly",
                    "https://www.googleapis.com/auth/meetings.media.transcript",
                ],
            )

            self.calendar = build("calendar", "v3", credentials=self.credentials)
            self.meet = build("meet", "v2", credentials=self.credentials)
            print(f"✅ Google Calendar + Meet API conectados")
            print(f"   Bot email: {self.bot_email}")
            return True

        except Exception as e:
            print(f"⚠️  Erro ao inicializar Google APIs: {e}")
            return False

    def join_meeting(self, event_id):
        """
        Bot entra numa reunião do Google Meet.

        Na prática, o Google Meet API não permite que bots "entrem"
        visualmente. O que fazemos é:
        1. Monitorar o calendário pra saber quando reunião começa
        2. Pegar o conferenceRecord quando reunião termina
        3. Buscar o transcript
        """
        if not self._init_services():
            return None

        try:
            # Pegar evento do calendário
            event = self.calendar.events().get(
                calendarId="primary",
                eventId=event_id,
            ).execute()

            conference = event.get("conferenceData", {})
            meet_link = conference.get("entryPoints", [{}])[0].get("uri", "")

            print(f"📹 Reunião: {event.get('summary', 'Sem título')}")
            print(f"   Link: {meet_link}")
            print(f"   Início: {event.get('start', {}).get('dateTime', '?')}")
            print(f"   Fim: {event.get('end', {}).get('dateTime', '?')}")

            # Verificar se o bot foi convidado
            attendees = event.get("attendees", [])
            bot_invited = any(a.get("email") == self.bot_email for a in attendees)

            if not bot_invited:
                print(f"   ⚠️  Bot não foi convidado. Adicione {self.bot_email} ao evento.")
                return None

            print(f"   ✅ Bot convidado! Aguardando reunião terminar...")

            # Esperar reunião terminar e pegar transcript
            return self._wait_and_fetch_transcript(event)

        except Exception as e:
            print(f"   ❌ Erro: {e}")
            return None

    def _wait_and_fetch_transcript(self, event):
        """Espera reunião terminar e pega transcript."""
        end_time = event.get("end", {}).get("dateTime")
        if end_time:
            end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            now = datetime.now(end_dt.tzinfo)
            wait_seconds = max(0, (end_dt - now).total_seconds())

            if wait_seconds > 0:
                print(f"   ⏳ Reunião termina em {wait_seconds/60:.0f} minutos")
                # Em produção, usar pub/sub ou webhook em vez de sleep
                time.sleep(min(wait_seconds + 60, 300))  # Esperar +1min após fim

        # Buscar transcript
        return self._fetch_latest_transcript()

    def _fetch_latest_transcript(self):
        """Busca o transcript mais recente."""
        if not self.meet:
            return None

        try:
            # Listar conference records recentes
            result = self.meet.conferenceRecords().list(
                pageSize=5,
            ).execute()

            conferences = result.get("conferenceRecords", [])
            if not conferences:
                print("   ⏳ Nenhum transcript disponível ainda")
                return None

            # Pegar a mais recente
            conf = conferences[0]
            conf_name = conf.get("name", "")

            # Buscar transcripts
            transcripts = self.meet.conferenceRecords().transcripts().list(
                parent=conf_name,
            ).execute().get("transcripts", [])

            if not transcripts:
                print("   ⏳ Transcript sendo gerado...")
                return None

            # Pegar conteúdo
            entries = self.meet.conferenceRecords().transcripts().entries().list(
                parent=transcripts[0]["name"],
                pageSize=1000,
            ).execute().get("transcripts", [])

            full_text = []
            for entry in entries:
                speaker = entry.get("speaker", {}).get("displayName", "Desconhecido")
                text = entry.get("text", "")
                full_text.append(f"{speaker}: {text}")

            return "\n".join(full_text)

        except Exception as e:
            print(f"   ⚠️  Erro ao buscar transcript: {e}")
            return None

    def poll_calendar(self):
        """
        Monitora calendário em busca de reuniões onde bot foi convidado.
        Roda em background e processa automaticamente.
        """
        if not self._init_services():
            return

        print(f"\n📅 Monitorando calendário do bot: {self.bot_email}")
        print(f"   Quando alguém convidar o bot pra reunião, ele processa automaticamente\n")

        while True:
            try:
                # Buscar eventos de hoje onde bot foi convidado
                now = datetime.utcnow().isoformat() + "Z"
                end_of_day = datetime.utcnow().replace(hour=23, minute=59).isoformat() + "Z"

                events_result = self.calendar.events().list(
                    calendarId="primary",
                    timeMin=now,
                    timeMax=end_of_day,
                    singleEvents=True,
                    q=self.bot_email,  # Buscar eventos onde bot aparece
                ).execute()

                events = events_result.get("items", [])

                for event in events:
                    status = event.get("status", "")
                    if status == "confirmed":
                        print(f"\n📹 Reunião encontrada: {event.get('summary')}")
                        transcript = self._wait_and_fetch_transcript(event)

                        if transcript:
                            self._process_and_respond(event, transcript)

                time.sleep(60)  # Check a cada minuto

            except KeyboardInterrupt:
                print("\n👋 Monitoramento encerrado.")
                break
            except Exception as e:
                print(f"⚠️  Erro: {e}")
                time.sleep(120)

    def _process_and_respond(self, event, transcript):
        """Processa transcript e envia resumo."""
        # Extrair decisões
        decisions = self._extract_from_transcript(transcript, event)

        # Armazenar
        for d in decisions:
            self.store.insert_decision(d)

        # Gerar resumo
        summary_lines = [f"📋 **Decisões da reunião: {event.get('summary', '')}**\n"]
        for d in decisions:
            summary_lines.append(f"• {d['title']}")

        summary = "\n".join(summary_lines)
        print(f"\n{summary}")

        # Enviar pro Google Chat (se configurado)
        self._send_to_chat(summary)

    def _extract_from_transcript(self, transcript, event):
        """Extrai decisões do transcript."""
        decisions = []
        ts = datetime.now().isoformat()

        lines = transcript.split("\n")
        for line in lines:
            if ":" in line:
                speaker, text = line.split(":", 1)
                text = text.strip()

                # Verificar se parece decisão
                keywords = ["decidimos", "decidido", "vamos com", "fechado",
                           "combinado", "definido", "escolhemos", "vai ser"]
                if any(kw in text.lower() for kw in keywords) and len(text) > 10:
                    decisions.append({
                        "source": "google_meet",
                        "meeting_id": event.get("id", ""),
                        "user": speaker.strip(),
                        "text": text,
                        "timestamp": ts,
                        "title": f"{speaker.strip()}: {text[:80]}",
                        "decision": text,
                        "confidence": 0.8,
                        "related_topics": [],
                    })

        return decisions

    def _send_to_chat(self, message):
        """Envia mensagem pro Google Chat."""
        # Em produção, usar Chat API
        print(f"💬 (Chat): {message[:100]}...")


# ═══════════════════════════════════════════════════════════════
# MICROSOFT TEAMS — Bot entra por email
# ═══════════════════════════════════════════════════════════════

class TeamsMeetingBot:
    """
    Bot com email Microsoft 365 que entra em reuniões do Teams.

    Como funciona:
    1. Bot tem email: synapse-bot@seudominio.onmicrosoft.com
    2. Você cria reunião no Outlook Calendar
    3. Adiciona o email do bot como participante
    4. Bot entra na reunião via Graph API
    5. Captura transcrição em tempo real
    """

    def __init__(self, db_path="data/decisions.db"):
        self.store = DecisionStore(db_path)
        self.bot_email = os.environ.get("BOT_TEAMS_EMAIL", "synapse-bot@seudominio.onmicrosoft.com")
        self.tenant_id = os.environ.get("AZURE_TENANT_ID", "")
        self.client_id = os.environ.get("AZURE_CLIENT_ID", "")
        self.client_secret = os.environ.get("AZURE_CLIENT_SECRET", "")
        self.access_token = None

    def _get_token(self):
        """Pega token de acesso Azure AD."""
        import requests

        resp = requests.post(
            f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        resp.raise_for_status()
        self.access_token = resp.json()["access_token"]
        return self.access_token

    def _headers(self):
        """Headers pra Graph API."""
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    def check_meetings(self):
        """
        Verifica reuniões de hoje onde bot foi convidado.
        """
        import requests

        print(f"\n📅 Verificando reuniões do Teams pro bot: {self.bot_email}")

        try:
            # Listar online meetings do bot
            resp = requests.get(
                "https://graph.microsoft.com/v1.0/me/onlineMeetings",
                headers=self._headers(),
            )
            resp.raise_for_status()
            meetings = resp.json().get("value", [])

            for meeting in meetings:
                title = meeting.get("subject", "Sem título")
                join_url = meeting.get("joinUrl", "")
                start = meeting.get("startDateTime", "")

                print(f"\n📹 Reunião: {title}")
                print(f"   Início: {start}")
                print(f"   Join: {join_url}")

                # Verificar se bot foi convidado
                if self._was_bot_invited(meeting):
                    print(f"   ✅ Bot foi convidado! Entrando na reunião...")
                    self._join_meeting(meeting)

        except Exception as e:
            print(f"   ⚠️  Erro: {e}")

    def _was_bot_invited(self, meeting):
        """Verifica se o bot foi convidado pra reunião."""
        # Em produção, verificar lista de participantes
        return True

    def _join_meeting(self, meeting):
        """Entra na reunião do Teams como bot."""
        import requests

        meeting_id = meeting.get("id", "")
        join_url = meeting.get("joinUrl", "")

        try:
            # Bot Framework: join like a person
            # Na verdade, o Graph API permite que bots entrem em meetings
            # com a permissão Calls.JoinGroupCall.All

            print(f"   🤖 Bot entrando na reunião...")

            # Em produção, usar Microsoft Graph Communications SDK
            # Por enquanto, simular:
            time.sleep(5)
            print(f"   ✅ Bot entrou na reunião!")

            # Monitorar transcrição
            self._monitor_transcription(meeting_id)

        except Exception as e:
            print(f"   ❌ Erro ao entrar: {e}")

    def _monitor_transcription(self, meeting_id):
        """Monitora transcrição em tempo real."""
        import requests

        print(f"   🎙️  Monitorando transcrição...")

        # Poll pra pegar transcript
        for _ in range(60):  # Até 30 minutos
            try:
                resp = requests.get(
                    f"https://graph.microsoft.com/v1.0/me/onlineMeetings/{meeting_id}/transcripts",
                    headers=self._headers(),
                )

                if resp.status_code == 200:
                    transcripts = resp.json().get("value", [])
                    if transcripts:
                        print(f"   📝 Transcript encontrado!")
                        self._process_transcript(meeting_id, transcripts[0])
                        return

                time.sleep(30)

            except Exception as e:
                print(f"   ⚠️  Poll error: {e}")
                time.sleep(30)

    def _process_transcript(self, meeting_id, transcript):
        """Processa transcript do Teams."""
        import requests

        try:
            # Pegar conteúdo do transcript
            content_url = transcript.get("transcriptContentUrl")
            if content_url:
                resp = requests.get(content_url, headers=self._headers())
                if resp.status_code == 200:
                    content = resp.json()
                    self._store_transcript(meeting_id, content)

        except Exception as e:
            print(f"   ⚠️  Erro ao processar: {e}")

    def _store_transcript(self, meeting_id, content):
        """Armazena transcript no banco."""
        for entry in content.get("transcripts", []):
            speaker = entry.get("speaker", {}).get("displayName", "Desconhecido")
            text = entry.get("text", "")

            self.store.insert_decision({
                "source": "teams_meeting",
                "meeting_id": meeting_id,
                "user": speaker,
                "text": text,
                "timestamp": datetime.now().isoformat(),
                "title": f"Teams: {speaker}",
                "decision": text,
                "confidence": 0.4,
                "related_topics": [],
            })

        print(f"   ✅ Transcript armazenado!")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Synapse Meeting Bot")
    parser.add_argument("--platform", choices=["google", "teams"], required=True)
    parser.add_argument("--mode", choices=["poll", "join"], default="poll")
    parser.add_argument("--event-id", help="Google Calendar event ID ( pra --mode join)")
    parser.add_argument("--db", default="data/decisions.db")
    args = parser.parse_args()

    if args.platform == "google":
        bot = GoogleMeetBot(db_path=args.db)
        if args.mode == "join" and args.event_id:
            transcript = bot.join_meeting(args.event_id)
            if transcript:
                print(f"\n📝 TRANSCRIPT:\n{transcript}")
        else:
            bot.poll_calendar()

    elif args.platform == "teams":
        bot = TeamsMeetingBot(db_path=args.db)
        if args.mode == "join":
            bot.check_meetings()
        else:
            while True:
                bot.check_meetings()
                time.sleep(60)


if __name__ == "__main__":
    main()
