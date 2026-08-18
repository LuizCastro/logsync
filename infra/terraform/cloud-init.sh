#!/bin/bash
# Cloud-init script for Synapse server
# Runs on first boot of the OCI compute instance

set -e

echo "=== Synapse Server Setup ==="
echo "Started at $(date)"

# ── Update system ────────────────────────────────────────
apt-get update -qq
apt-get upgrade -y -qq

# ── Install dependencies ────────────────────────────────
apt-get install -y -qq \
  curl \
  wget \
  git \
  sqlite3 \
  python3-pip \
  docker.io \
  docker-compose \
  jq

# ── Enable Docker ───────────────────────────────────────
systemctl enable docker
systemctl start docker
usermod -aG docker ubuntu

# ── Install OCI CLI ─────────────────────────────────────
cd /tmp
curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh -o oci-install.sh
bash oci-install.sh --accept-all-defaults
echo 'export PATH="$HOME/bin:$PATH"' >> /home/ubuntu/.bashrc

# ── Create Synapse directory ────────────────────────────
mkdir -p /home/ubuntu/synapse
mkdir -p /home/ubuntu/synapse/data
mkdir -p /home/ubuntu/synapse/data/oci-docs

# ── Clone Synapse (or copy from local) ─────────────────
# Option 1: Git clone (if repo is available)
# git clone https://github.com/your-org/synapse.git /home/ubuntu/synapse

# Option 2: SCP from local machine (use deploy script)
# scp -r ./synapse/* ubuntu@<IP>:/home/ubuntu/synapse/

# ── Install Python dependencies ─────────────────────────
pip3 install oci requests

# ── Create docker-compose.yml for n8n ───────────────────
cat > /home/ubuntu/synapse/docker-compose.yml << 'DOCKER_EOF'
version: "3.8"

services:
  n8n:
    image: n8nio/n8n:latest
    container_name: synapse-n8n
    restart: unless-stopped
    ports:
      - "5678:5678"
    environment:
      - N8N_BASIC_AUTH_ACTIVE=true
      - N8N_BASIC_AUTH_USER=$${N8N_USER:-synapse}
      - N8N_BASIC_AUTH_PASSWORD=$${N8N_PASSWORD:-synapse2024}
      - GENERIC_TIMEZONE=America/Sao_Paulo
      - TZ=America/Sao_Paulo
      - WEBHOOK_URL=http://localhost:5678
    volumes:
      - n8n_data:/home/node/.n8n
    networks:
      - synapse

  api:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: synapse-api
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
    networks:
      - synapse

volumes:
  n8n_data:

networks:
  synapse:
    driver: bridge
DOCKER_EOF

# ── Create Dockerfile for API server ────────────────────
cat > /home/ubuntu/synapse/Dockerfile << 'DOCKERFILE_EOF'
FROM python:3.8-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ ./scripts/
COPY dashboard/ ./dashboard/

RUN mkdir -p /app/data

EXPOSE 8080

CMD ["python3", "scripts/server.py", "--port", "8080", "--host", "0.0.0.0", "--db", "data/decisions.db"]
DOCKERFILE_EOF

# ── Create requirements.txt ─────────────────────────────
cat > /home/ubuntu/synapse/requirements.txt << 'REQ_EOF'
oci>=2.90.0
requests>=2.28.0
REQ_EOF

# ── Setup environment variables ─────────────────────────
cat > /home/ubuntu/synapse/.env << 'ENV_EOF'
N8N_USER=synapse
N8N_PASSWORD=synapse2024
OCI_REGION=sa-saopaulo-1
OCI_TENANCY=
OCI_USER=
OCI_FINGERPRINT=
OCI_KEY_FILE=/root/.oci/oci_api_key.pem
OCI_COMPARTMENT_ID=
ENV_EOF

# ── Create systemd service for Synapse API ──────────────
cat > /etc/systemd/system/synapse-api.service << 'SERVICE_EOF'
[Unit]
Description=Synapse Decision Intelligence API
After=network.target docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/ubuntu/synapse
ExecStart=/usr/bin/docker-compose up -d
ExecStop=/usr/bin/docker-compose down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
SERVICE_EOF

systemctl daemon-reload
systemctl enable synapse-api

# ── Setup Ollama (local LLM fallback) ──────────────────
echo "Installing Ollama..."
curl -fsSL https://ollama.ai/install.sh | sh

# Pull Llama 3.1 8B (smaller model for fallback)
ollama pull llama3.1:8b

# ── Create management script ────────────────────────────
cat > /home/ubuntu/synapse/manage.sh << 'MANAGE_EOF'
#!/bin/bash
# Synapse management script

case "$1" in
  start)
    echo "Starting Synapse..."
    cd /home/ubuntu/synapse
    docker-compose up -d
    systemctl start synapse-api
    echo "✓ n8n: http://$(curl -s ifconfig.me):5678"
    echo "✓ API: http://$(curl -s ifconfig.me):8080"
    ;;
  stop)
    echo "Stopping Synapse..."
    cd /home/ubuntu/synapse
    docker-compose down
    systemctl stop synapse-api
    ;;
  restart)
    $0 stop
    sleep 2
    $0 start
    ;;
  status)
    echo "=== Synapse Status ==="
    docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    echo ""
    echo "=== API Health ==="
    curl -s http://localhost:8080/api/stats | jq . 2>/dev/null || echo "API not responding"
    ;;
  logs)
    docker-compose logs -f
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs}"
    ;;
esac
MANAGE_EOF

chmod +x /home/ubuntu/synapse/manage.sh

# ── Open firewall ports ─────────────────────────────────
ufw allow 22/tcp
ufw allow 5678/tcp
ufw allow 8080/tcp
ufw --force enable

# ── Set permissions ─────────────────────────────────────
chown -R ubuntu:ubuntu /home/ubuntu/synapse

echo "=== Setup Complete ==="
echo "Finished at $(date)"
echo ""
echo "Next steps:"
echo "  1. SSH into the instance"
echo "  2. Copy your Synapse code: scp -r ./synapse/* ubuntu@<IP>:/home/ubuntu/synapse/"
echo "  3. Configure OCI credentials: nano /home/ubuntu/synapse/.env"
echo "  4. Start services: /home/ubuntu/synapse/manage.sh start"
echo ""
echo "n8n will be available at: http://$(curl -s ifconfig.me 2>/dev/null || echo '<PUBLIC_IP>'):5678"
echo "API will be available at: http://$(curl -s ifconfig.me 2>/dev/null || echo '<PUBLIC_IP>'):8080"
