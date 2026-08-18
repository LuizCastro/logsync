#!/usr/bin/env python3
"""
Synapse — Teste local (sem OCI)
Roda o pipeline de extração de decisões com dados sample.
Útil pra validar que o SQLite + LLM extraction funciona antes de conectar OCI.
"""

import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from decision_store import DecisionStore


# ── Sample meeting transcript ────────────────────────────────
SAMPLE_TRANSCRIPT = """
[0.0s] Carlos: Pessoal, bora alinhar o projeto novo. A gente precisa decidir a stack até sexta.
[5.2s] Maria: Eu tava pensando em Node.js pro backend, mas o João prefere Python.
[12.0s] João: Python é melhor pra ML e data pipeline. Node é bom pra API mas não pra coisa de dados.
[18.5s] Carlos: Concordo com o João. Vamos de Python pro backend.
[22.0s] Maria: Beleza, mas qual framework? FastAPI ou Django?
[26.0s] João: FastAPI. É mais leve, async nativo, e pro nosso caso não precisa de admin do Django.
[30.0s] Carlos: Decidido: Python com FastAPI. Maria, documenta isso no Notion.
[35.0s] Maria: Anotado. E o banco de dados? PostgreSQL ou MongoDB?
[40.0s] João: PostgreSQL. Nossos dados são relacionais. MongoDB não faz sentido aqui.
[44.0s] Carlos: Concordo. PostgreSQL. Próxima decisão: onde vai rodar?
[48.0s] Maria: OCI ou AWS?
[52.0s] João: OCI. O time de infra já tem conta, e o Generative AI deles é bom pra parte de IA.
[57.0s] Carlos: Fechado. Decidido: Python + FastAPI + PostgreSQL + OCI.
[62.0s] Maria: E quem faz o quê?
[65.0s] Carlos: João cuida do backend e infra OCI. Maria do frontend. Eu do produto e integrações.
[70.0s] João: combinado. Sprints de 2 semanas, review toda sexta.
[74.0s] Carlos: Última coisa: a gente vai usar n8n pra automatizar os workflows internos.
[78.0s] João: boa. Integra com OCI e é low-code. Perfeito pro time.
[82.0s] Carlos: Perfeito. Vamos com isso.
"""

SAMPLE_SLACK_MESSAGES = [
    {
        "channel": "eng",
        "user": "joao",
        "text": "Decidimos usar FastAPI pro backend. Motivo: async nativo, leve, bom pra APIs. Django seria overkill pro nosso caso.",
        "ts": "1234567890.001"
    },
    {
        "channel": "eng",
        "user": "maria",
        "text": "Pessoal, confirmado: PostgreSQL pro banco. João vai configurar o schema até terça. Alguém tem objeção?",
        "ts": "1234567890.002"
    },
    {
        "channel": "eng",
        "user": "carlos",
        "text": "Reunião de alinhamento decidido: sprints de 2 semanas, review toda sexta 15h. João backend+infra, Maria frontend, eu produto.",
        "ts": "1234567890.003"
    }
]


def simulate_extraction_meeting():
    """Simula a extração de decisões de uma transcrição de reunião."""
    print("=" * 60)
    print("📋 TESTE: Extração de Decisões — Reunião")
    print("=" * 60)

    # Decisões esperadas (ground truth)
    expected = [
        {
            "title": "Usar Python com FastAPI pro backend",
            "decision": "Backend será construído em Python com framework FastAPI.",
            "rationale": "Python melhor pra ML e dados, FastAPI é leve com async nativo. Django seria overkill.",
            "alternatives_rejected": ["Node.js — bom pra API mas não pra dados/ML", "Django — admin desnecessário, mais pesado"],
            "owner": "João",
            "action_items": ["João configurar ambiente Python+FastAPI", "Maria documentar no Notion"],
            "confidence": 0.95,
            "related_topics": ["backend", "framework", "python", "fastapi"]
        },
        {
            "title": "Usar PostgreSQL como banco de dados",
            "decision": "Banco de dados será PostgreSQL.",
            "rationale": "Dados são relacionais, MongoDB não faz sentido pro nosso caso.",
            "alternatives_rejected": ["MongoDB — dados não são documentos, são relacionais"],
            "owner": None,
            "action_items": ["João configurar schema PostgreSQL até terça"],
            "confidence": 0.93,
            "related_topics": ["database", "postgresql", "backend"]
        },
        {
            "title": "Rodar infraestrutura na OCI",
            "decision": "Infestrutura cloud será na Oracle Cloud Infrastructure (OCI).",
            "rationale": "Time de infra já tem conta OCI, e o Generative AI deles é bom pra parte de IA.",
            "alternatives_rejected": ["AWS — time não tem conta configurada"],
            "owner": "João",
            "action_items": ["João provisionar recursos OCI"],
            "confidence": 0.90,
            "related_topics": ["cloud", "infra", "oci", "oracle"]
        },
        {
            "title": "Definir papéis e sprints",
            "decision": "Sprints de 2 semanas com review toda sexta. João backend+infra, Maria frontend, Carlos produto.",
            "rationale": "Alinhamento de responsabilidades e ritmo de trabalho.",
            "alternatives_rejected": [],
            "owner": "Carlos",
            "action_items": ["Iniciar sprint 1 na segunda", "Carlos criar board no Jira"],
            "confidence": 0.92,
            "related_topics": ["processo", "sprint", "roles"]
        }
    ]

    # Inicializar store
    store = DecisionStore("data/test_decisions.db")

    # Simular inserção
    print(f"\nTranscrição: {len(SAMPLE_TRANSCRIPT)} chars, ~80s de reunião")
    print(f"Decisões esperadas: {len(expected)}")

    inserted_ids = []
    for d in expected:
        d["source"] = "meeting"
        d["meeting_id"] = "test-sprint-001"
        did = store.insert_decision(d)
        inserted_ids.append(did)
        print(f"  ✅ [{did}] {d['title']} (conf={d['confidence']})")

    # Inserir action items
    action_count = 0
    for d in expected:
        for item in d.get("action_items", []):
            store.insert_action_item({
                "decision_id": inserted_ids[expected.index(d)],
                "decision_title": d["title"],
                "action": item,
                "owner": d.get("owner"),
                "priority": "high" if d["confidence"] >= 0.9 else "medium",
            })
            action_count += 1

    print(f"\n📊 Resultados:")
    stats = store.get_stats()
    print(f"  Decisões: {stats['total_decisions']}")
    print(f"  Action items pendentes: {stats['pending_actions']}")
    print(f"  Owners ativos: {stats['active_owners']}")

    # Teste de busca
    print(f"\n🔍 Busca por 'PostgreSQL':")
    results = store.search_decisions("PostgreSQL")
    for r in results:
        print(f"  → {r['title']}: {r['decision']}")

    # Daily brief
    print(f"\n📰 Daily Brief:")
    brief = store.generate_daily_brief()
    print(f"  {brief['summary']}")
    for d in brief["decisions"]:
        print(f"  • {d['title']} (owner: {d['owner']})")

    store.close()
    return len(inserted_ids), action_count


def simulate_extraction_slack():
    """Simula extração de decisões de mensagens Slack."""
    print("\n" + "=" * 60)
    print("💬 TESTE: Extração de Decisões — Slack")
    print("=" * 60)

    store = DecisionStore("data/test_decisions.db")

    expected_slack = [
        {
            "title": "Usar FastAPI pro backend (confirmado no Slack)",
            "decision": "Confirmado: FastAPI é o framework escolhido.",
            "rationale": "Async nativo, leve, bom pra APIs.",
            "alternatives_rejected": ["Django — overkill"],
            "owner": "João",
            "action_items": [],
            "confidence": 0.88,
            "related_topics": ["fastapi", "backend", "framework"]
        },
        {
            "title": "Schema PostgreSQL até terça",
            "decision": "João vai configurar o schema do PostgreSQL até terça-feira.",
            "rationale": "Confirmado pelo time, sem objeções.",
            "alternatives_rejected": [],
            "owner": "João",
            "action_items": ["Configurar schema PostgreSQL até terça"],
            "confidence": 0.85,
            "related_topics": ["postgresql", "database", "schema"]
        }
    ]

    for d in expected_slack:
        d["source"] = "slack"
        d["channel"] = "eng"
        did = store.insert_decision(d)
        print(f"  ✅ [{did}] {d['title']} (conf={d['confidence']})")

    stats = store.get_stats()
    print(f"\n📊 Total agora: {stats['total_decisions']} decisões, {stats['pending_actions']} actions")

    store.close()
    return len(expected_slack)


def main():
    print("🧠 Synapse — Teste de Extração de Decisões")
    print("   (sem OCI, sem LLM — valida store + schema)\n")

    n_meeting, n_actions = simulate_extraction_meeting()
    n_slack = simulate_extraction_slack()

    print("\n" + "=" * 60)
    print("✅ TODOS OS TESTES PASSARAM")
    print("=" * 60)
    print(f"\nTotal: {n_meeting + n_slack} decisões inseridas, {n_actions} action items")
    print(f"Banco: data/test_decisions.db")
    print(f"\nPróximo passo: conectar OCI GenAI pra extração real via LLM")


if __name__ == "__main__":
    main()
