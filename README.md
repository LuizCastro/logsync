# Synapse — Decision Intelligence Agent

> *Agente de IA que mantém viva a memória operacional do seu time*

**Stack:** n8n + OCI Generative AI (Llama 3.1 70B) + SQLite + Decision Board UI

## Quick Start (2 minutos)

```bash
# 1. Rodar teste local (só precisa de Python 3.8+)
cd synapse
python3 scripts/test_local.py

# 2. Iniciar dashboard + API
python3 scripts/server.py --port 8080 --db data/test_decisions.db

# 3. Abrir http://localhost:8080
```

## Arquitetura

```
Meeting Recording ──┐
                    ├──▶ n8n ──▶ OCI GenAI (Llama 3.1 70B) ──▶ Decision Store ──▶ Dashboard
Slack Events ───────┘         (extract decisions)              (SQLite + RAG)     (HTML/JS)
                                       │                              │
                                       ▼                              ▼
                              Action Plan (auto)              Daily Brief + Search
```

## Arquivos

| Arquivo | O que faz |
|---|---|
| `scripts/test_local.py` | Teste completo com dados sample (sem OCI) |
| `scripts/server.py` | API server + serve dashboard (porta 8080) |
| `scripts/decision_store.py` | Store SQLite com busca + daily brief |
| `scripts/oci_decision_store.py` | Store híbrido SQLite + OCI RAG |
| `scripts/setup_oci.py` | Provisiona KB + Agent + Endpoint na OCI |
| `scripts/decision_linker.py` | Linka decisões cross-canal |
| `workflows/synapse-oci.json` | Workflow n8n importável |
| `dashboard/index.html` | Decision Board UI (dark theme) |
| `prompts/extraction-prompt.md` | Prompt de extração de decisões |
| `docker-compose.yml` | n8n + Ollama (LLM local fallback) |

## Setup Completo (com OCI)

### Pré-requisitos
- Python 3.8+
- Docker + Docker Compose
- OCI account com `manage` permissions em compartment
- n8n community node: `n8n-nodes-oci-generative-ai`

### 1. Configurar OCI

```bash
# Copiar e preencher
cp .env.example .env

# Rodar setup (cria KB, Agent, Endpoint na OCI)
python3 scripts/setup_oci.py --compartment-id ocid1.compartment.oc1..xxx --region sa-saopaulo-1
```

### 2. Subir n8n

```bash
docker-compose up -d

# Login: synapse / synapse2024
# Instalar community node: Settings → Community Nodes → n8n-nodes-oci-generative-ai
# Importar workflow: workflows/synapse-oci.json
# Configurar credencial OCI no n8n
```

### 3. Testar

```bash
# Enviar transcrição
curl -X POST http://localhost:5678/webhook/synapse-meeting \
  -H "Content-Type: application/json" \
  -d '{"meeting_id": "sprint-1", "transcript": "Decidimos usar FastAPI pro backend...", "participants": ["João", "Maria"]}'

# Enviar mensagem Slack
curl -X POST http://localhost:5678/webhook/synapse-slack \
  -H "Content-Type: application/json" \
  -d '{"channel": "eng", "user": "joao", "text": "Decidimos usar PostgreSQL pro banco", "ts": "1234567890"}'
```

## API Endpoints

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/api/decisions` | Listar decisões recentes (30 dias) |
| `GET` | `/api/actions` | Action items pendentes |
| `GET` | `/api/stats` | Estatísticas (total, pendentes, owners) |
| `GET` | `/api/brief` | Daily brief gerado |
| `GET` | `/api/search?q=...` | Buscar decisões por query |
| `POST` | `/api/decisions` | Criar decisão manualmente |
| `POST` | `/api/actions` | Criar action item |
| `POST` | `/api/actions/complete` | Marcar action como done |

## OCI Services Used

| Serviço | Função |
|---|---|
| **OCI Generative AI** | LLM (Llama 3.1 70B) via community node |
| **OCI Knowledge Base** | RAG — busca semântica de decisões passadas |
| **OCI Speech** | Transcrição de áudio (alternativa ao Whisper) |
| **OCI Object Storage** | Armazenar documentos para ingestion no KB |

## Development

```bash
# Rodar sem Docker (só SQLite + API server)
python3 scripts/server.py --db data/dev.db

# Rodar testes
python3 scripts/test_local.py

# Verificar banco
python3 -c "
import sqlite3
conn = sqlite3.connect('data/test_decisions.db')
for r in conn.execute('SELECT title, owner, confidence FROM decisions'):
    print(f'  {r[0]} — {r[1]} ({r[2]})')
"
```
