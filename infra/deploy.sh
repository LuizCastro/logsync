#!/bin/bash
# Synapse — Deploy to OCI
# This script provisions infrastructure and deploys the Synapse stack.
#
# Usage:
#   ./deploy.sh                    # Full deploy (infra + code)
#   ./deploy.sh --infra-only       # Only provision OCI infrastructure
#   ./deploy.sh --code-only        # Only deploy code to existing instance
#   ./deploy.sh --destroy          # Destroy all OCI resources

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TERRAFORM_DIR="$SCRIPT_DIR/terraform"
SYNAPSE_DIR="$SCRIPT_DIR/.."

echo "🧠 Synapse — OCI Deploy"
echo "========================"

# ── Check prerequisites ─────────────────────────────────
check_prerequisites() {
    echo "[*] Checking prerequisites..."

    if ! command -v terraform &> /dev/null; then
        echo "  ❌ Terraform not found. Install: https://developer.hashicorp.com/terraform/install"
        exit 1
    fi

    if ! command -v oci &> /dev/null; then
        echo "  ⚠️  OCI CLI not found. Install: pip install oci"
        echo "     Continuing with Terraform only..."
    fi

    if [ ! -f "$TERRAFORM_DIR/terraform.tfvars" ]; then
        echo "  ❌ terraform.tfvars not found."
        echo "     Copy: cp terraform.tfvars.example terraform.tfvars"
        echo "     Then fill in your OCI credentials."
        exit 1
    fi

    echo "  ✓ Prerequisites OK"
}

# ── Provision Infrastructure ─────────────────────────────
provision_infra() {
    echo ""
    echo "[1/3] Provisioning OCI infrastructure..."
    cd "$TERRAFORM_DIR"

    terraform init
    terraform plan -out=tfplan
    echo ""
    echo "  Review the plan above. Continue? (y/n)"
    read -r confirm
    if [ "$confirm" != "y" ]; then
        echo "  Aborted."
        exit 0
    fi

    terraform apply tfplan

    # Save outputs
    terraform output -json > "$SCRIPT_DIR/outputs.json"
    echo "  ✓ Infrastructure provisioned"

    # Get public IP
    PUBLIC_IP=$(terraform output -raw instance_public_ip)
    SSH_KEY=$(terraform output -raw ssh_command | sed 's/ssh -i .* ssh/ssh/')
    echo "  ✓ Instance IP: $PUBLIC_IP"
    echo "  ✓ SSH: $SSH_KEY"
}

# ── Deploy Code ──────────────────────────────────────────
deploy_code() {
    echo ""
    echo "[2/3] Deploying Synapse code..."

    PUBLIC_IP=$(cd "$TERRAFORM_DIR" && terraform output -raw instance_public_ip 2>/dev/null)
    if [ -z "$PUBLIC_IP" ]; then
        echo "  ❌ Could not get instance IP. Run --infra-only first."
        exit 1
    fi

    PRIVATE_KEY=$(cd "$TERRAFORM_DIR" && terraform output -raw ssh_command 2>/dev/null | grep -oP '(?<=-i )\S+')
    SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

    echo "  → Connecting to $PUBLIC_IP..."

    # Upload code
    echo "  → Uploading Synapse code..."
    scp $SSH_OPTS -i "$PRIVATE_KEY" -r "$SYNAPSE_DIR/scripts" ubuntu@$PUBLIC_IP:/home/ubuntu/synapse/
    scp $SSH_OPTS -i "$PRIVATE_KEY" -r "$SYNAPSE_DIR/workflows" ubuntu@$PUBLIC_IP:/home/ubuntu/synapse/
    scp $SSH_OPTS -i "$PRIVATE_KEY" -r "$SYNAPSE_DIR/dashboard" ubuntu@$PUBLIC_IP:/home/ubuntu/synapse/
    scp $SSH_OPTS -i "$PRIVATE_KEY" -r "$SYNAPSE_DIR/prompts" ubuntu@$PUBLIC_IP:/home/ubuntu/synapse/
    scp $SSH_OPTS -i "$PRIVATE_KEY" "$SYNAPSE_DIR/requirements.txt" ubuntu@$PUBLIC_IP:/home/ubuntu/synapse/
    scp $SSH_OPTS -i "$PRIVATE_KEY" "$SYNAPSE_DIR/docker-compose.yml" ubuntu@$PUBLIC_IP:/home/ubuntu/synapse/
    scp $SSH_OPTS -i "$PRIVATE_KEY" "$SYNAPSE_DIR/Dockerfile" ubuntu@$PUBLIC_IP:/home/ubuntu/synapse/

    echo "  ✓ Code uploaded"

    # Install Python deps
    echo "  → Installing Python dependencies..."
    ssh $SSH_OPTS -i "$PRIVATE_KEY" ubuntu@$PUBLIC_IP "cd /home/ubuntu/synapse && pip3 install -r requirements.txt"

    # Run initial test
    echo "  → Running test..."
    ssh $SSH_OPTS -i "$PRIVATE_KEY" ubuntu@$PUBLIC_IP "cd /home/ubuntu/synapse && python3 scripts/test_local.py"

    echo "  ✓ Code deployed"
}

# ── Start Services ───────────────────────────────────────
start_services() {
    echo ""
    echo "[3/3] Starting services..."

    PUBLIC_IP=$(cd "$TERRAFORM_DIR" && terraform output -raw instance_public_ip 2>/dev/null)
    PRIVATE_KEY=$(cd "$TERRAFORM_DIR" && terraform output -raw ssh_command 2>/dev/null | grep -oP '(?<=-i )\S+')
    SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

    # Start via management script
    ssh $SSH_OPTS -i "$PRIVATE_KEY" ubuntu@$PUBLIC_IP "/home/ubuntu/synapse/manage.sh start"

    echo ""
    echo "  ✓ Services started"
    echo ""
    echo "========================"
    echo "✅ Synapse deployed!"
    echo "========================"
    echo ""
    echo "  n8n:     http://$PUBLIC_IP:5678"
    echo "  API:     http://$PUBLIC_IP:8080"
    echo "  SSH:     ssh -i $PRIVATE_KEY ubuntu@$PUBLIC_IP"
    echo ""
    echo "  Login:   synapse / synapse2024"
    echo ""
    echo "  Next steps:"
    echo "  1. Open n8n, install community node 'n8n-nodes-oci-generative-ai'"
    echo "  2. Import workflow: workflows/synapse-oci.json"
    echo "  3. Configure OCI credentials in n8n"
    echo "  4. Test: curl -X POST http://$PUBLIC_IP:5678/webhook/synapse-meeting ..."
}

# ── Destroy Infrastructure ───────────────────────────────
destroy_infra() {
    echo ""
    echo "⚠️  DESTROYING all OCI resources!"
    echo "  This will delete the compute instance and all data."
    echo "  Continue? (yes/no)"
    read -r confirm
    if [ "$confirm" != "yes" ]; then
        echo "  Aborted."
        exit 0
    fi

    cd "$TERRAFORM_DIR"
    terraform destroy -auto-approve
    echo "  ✓ Resources destroyed"
}

# ── Main ─────────────────────────────────────────────────
case "${1:-}" in
    --infra-only)
        check_prerequisites
        provision_infra
        ;;
    --code-only)
        deploy_code
        start_services
        ;;
    --destroy)
        destroy_infra
        ;;
    *)
        check_prerequisites
        provision_infra
        deploy_code
        start_services
        ;;
esac
