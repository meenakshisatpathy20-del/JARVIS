#!/usr/bin/env bash
# One-command setup for a fresh Ubuntu VM (Oracle Cloud, AWS, Azure, GCP...).
#   curl -fsSL https://raw.githubusercontent.com/meenakshisatpathy20-del/J.A.R.V.I.S/main/scripts/vm_setup.sh | bash
# Installs Docker, opens the demo port in the VM firewall, clones the repo, creates .env with random
# secrets and starts the hosted demo (dashboard + lab + chaos behind one proxy) on port 8000.
# Safe to run again: it updates the code and restarts the demo.
set -euo pipefail
REPO=${REPO:-https://github.com/meenakshisatpathy20-del/J.A.R.V.I.S.git}
DIR=${DIR:-$HOME/JARVIS}
PORT=${PUBLIC_HTTP_PORT:-8000}
say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

say "Installing Docker and git (if missing)"
command -v git >/dev/null || { sudo apt-get update -y && sudo apt-get install -y git; }
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker

say "Opening TCP $PORT in the VM firewall"
# Oracle's Ubuntu images ship iptables rules that reject new inbound connections.
if ! sudo iptables -C INPUT -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null; then
  sudo iptables -I INPUT 1 -p tcp --dport "$PORT" -j ACCEPT
fi
command -v netfilter-persistent >/dev/null && sudo netfilter-persistent save >/dev/null 2>&1 || true

say "Adding swap if the VM has less than 3 GB of RAM"
if [ "$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)" -lt 3000 ] && ! sudo swapon --show | grep -q swapfile; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

say "Getting the code into $DIR"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone "$REPO" "$DIR"; fi
cd "$DIR"

if [ ! -f .env ]; then
  say "Creating .env with random secrets"
  for k in POSTGRES_PASSWORD APP_DB_PASSWORD CHAOS_DB_PASSWORD CHAOS_TOKEN INGEST_TOKEN DIAGNOSIS_SIGNING_KEY; do
    echo "$k=$(openssl rand -hex 16)"
  done > .env
  printf 'GEMINI_API_KEY=\nSITE_ADDRESS=:80\nPUBLIC_HTTP_PORT=%s\n' "$PORT" >> .env
fi

say "Building and starting J.A.R.V.I.S. (first time: 3-6 minutes)"
sudo docker compose -f docker-compose.yml -f docker-compose.deploy.yml up -d --build

say "Waiting for J.A.R.V.I.S. to answer"
for _ in $(seq 1 60); do curl -fs "http://localhost:$PORT/api/config" >/dev/null && break; sleep 3; done
IP=$(curl -fs --max-time 5 https://api.ipify.org || echo "<your-VM-public-IP>")
say "Done. Live demo: http://$IP:$PORT/   Blind test lab: http://$IP:$PORT/lab"
echo "Wait ~60 s for warm-up before breaking anything. If the page does not load from your laptop,"
echo "open TCP $PORT in the cloud firewall (Oracle: VCN > Security List > Add Ingress Rule)."
