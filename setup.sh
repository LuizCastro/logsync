#!/bin/bash
# Synapse — Setup completo
# Uso: ./setup.sh [--docker] [--oci]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "🧠 Synapse — Setup"
echo "==================="

# ── 1. Python deps ──────────────────────────────────────
echo ""
echo "[1/5] Instalando dependências Python..."
pip3 install -q -r requirements.txt 2>/dev/null || pip install -q -r requirements.txt

# ── 2. Criar diretório de dados ─────────────────────────
echo "[2/5] Criando diretório de dados..."
mkdir -p data/oci-docs

# ── 3. Rodar teste local ────────────────────────────────
echo "[3/5] Rodando teste local (SQLite)..."
python3 scripts/test_local.py

# ── 4. Docker (opcional) ────────────────────────────────
if [[ "$1" == "--docker" ]]; then
    echo ""
    echo "[4/5] Subindo n8n via Docker..."
    if [[ -f .env ]]; then
        docker-compose up -d
        echo "  ✓ n8n rodando em http://localhost:5678"
        echo "  ✓ Login: synapse / synapse2024"
    else
        echo "  ⚠️  Copie .env.example pra .env e preencha os valores OCI"
        echo "     cp .env.example .env"
    fi
else
    echo "[4/5] Docker ignorado (use --docker pra ativar)"
fi

# ── 5. OCI setup (opcional) ─────────────────────────────
if [[ "$1" == "--oci" ]] || [[ "$2" == "--oci" ]]; then
    echo ""
    echo "[5/5] Configurando recursos OCI..."
    if command -v oci &> /dev/null; then
        echo "  OCI CLI encontrado. Rodando setup..."
        python3 scripts/setup_oci.py \
            --compartment-id "${OCI_COMPARTMENT_ID:-}" \
            --region "${OCI_REGION:-sa-saopaulo-1}"
    else
        echo "  ⚠️  OCI CLI não encontrado. Instale: pip install oci"
    fi
else
    echo "[5/5] OCI ignorado (use --oci pra ativar)"
fi

echo ""
echo "==================="
echo "✅ Setup completo!"
echo "==================="
echo ""
echo "Arquivos criados:"
echo "  data/test_decisions.db  — banco SQLite de teste"
echo "  data/decisions.db       — banco SQLite principal"
echo ""
echo "Próximos passos:"
echo "  1. Importar workflow no n8n: workflows/synapse-oci.json"
echo "  2. Configurar credencial OCI no n8n"
echo "  3. Testar: curl -X POST http://localhost:5678/webhook/synapse-meeting ..."
echo ""
echo "Dashboard: abra dashboard/index.html no navegador"
