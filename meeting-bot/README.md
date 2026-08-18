# Synapse Meeting Bot

Bot que entra automaticamente em reuniões Google Meet, grava áudio e transcreve via Whisper.

## Como funciona

```
Google Calendar (convite) → Bot detecta → Entra no Meet → Grava áudio → Whisper transcreve → n8n processa
```

## Configuração

### 1. Criar conta Google para o bot

1. Crie uma conta Gmail: `synapse-bot@gmail.com`
2. Se usar 2FA, gere uma [App Password](https://myaccount.google.com/apppasswords)
3. Configure no `.env`:

```bash
cp .env.example .env
# Edite com suas credenciais
```

### 2. (Opcional) Service Account Google

Para acesso ao Calendar API sem login manual:

1. Acesse [Google Cloud Console](https://console.cloud.google.com)
2. Crie um projeto ou selecione um existente
3. Ative a **Google Calendar API**
4. Crie uma **Service Account**
5. Baixe o JSON e salve em `credentials/google-service-account.json`
6. Compartilhe o calendário com o email da service account

### 3. Iniciar

```bash
docker-compose up -d meeting-bot
```

## Uso

1. Convide `synapse-bot@gmail.com` para uma reunião Google Meet
2. O bot detecta o convite automaticamente (a cada 3 minutos)
3. Entra na reunião e grava o áudio
4. Transcreve via Whisper
5. Envia o transcript para o n8n webhook

## Variáveis de Ambiente

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `GOOGLE_BOT_EMAIL` | Email do bot | - |
| `GOOGLE_BOT_PASSWORD` | Senha/App Password | - |
| `GOOGLE_CREDENTIALS_FILE` | Caminho para service account JSON | `/app/credentials/google-service-account.json` |
| `GOOGLE_CALENDAR_ID` | ID do calendário | `primary` |
| `N8N_WEBHOOK_URL` | URL do webhook n8n | `http://synapse-n8n:5678/webhook/synapse-meeting` |
| `WHISPER_URL` | URL do Whisper API | `http://synapse-whisper:9000` |
| `CHECK_INTERVAL_MINUTES` | Intervalo de checagem | `3` |
| `RECORDING_DURATION_SECONDS` | Duração máxima gravação | `1800` (30min) |

## Arquitetura

```
┌─────────────────┐     ┌──────────────┐     ┌──────────────┐
│  Google Calendar │────▶│  Meeting Bot │────▶│   Whisper    │
│  (convites)     │     │  (Playwright)│     │  (transcrição)│
└─────────────────┘     └──────────────┘     └──────────────┘
                              │                      │
                              ▼                      ▼
                        ┌──────────────┐     ┌──────────────┐
                        │   n8n Webhook │◀────│   Transcript  │
                        │  (extração)  │     │              │
                        └──────────────┘     └──────────────┘
```

## Limitações

- Funciona apenas com **Google Meet** (outras plataformas requerem integração específica)
- Requer conta Google funcional para o bot
- Gravação limitada a 30 minutos por reunião (configurável)
- Sem GPU, transcrição pode ser lenta (use modelo `tiny` ou `base` para mais velocidade)

## Troubleshooting

### Bot não entra na reunião

1. Verifique se o email do bot está correto no `.env`
2. Verifique os logs: `docker logs synapse-meeting-bot`
3. Certifique-se de que o bot foi convidado para a reunião

### Transcrição falha

1. Verifique se o Whisper está rodando: `docker ps | grep whisper`
2. Teste manualmente: `curl http://localhost:9000/health`
3. Verifique os logs: `docker logs synapse-whisper`

### Bot não detecta convites

1. Verifique as credenciais do Google Calendar
2. Verifique se a API está ativada no Google Cloud Console
3. Verifique os logs: `docker logs synapse-meeting-bot`
