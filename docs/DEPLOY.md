# Deploying the hosted demo

J.A.R.V.I.S. is 7 containers (Postgres, 3 services, load generator, chaos, J.A.R.V.I.S.) plus a Caddy
reverse proxy, so it needs a machine that runs Docker (static hosts such as Vercel or Netlify cannot).
The deploy setup serves everything on **one URL**:

| Path | Goes to |
|---|---|
| `/` | Dashboard |
| `/lab` | Blind test lab |
| `/chaos/*` | Chaos controller (called by the lab page in the browser) |

J.A.R.V.I.S. and chaos still share no Docker network; the proxy sits on two separate edge networks.
The hidden truth stays protected by signed commit-then-reveal.

Recommended size: **2 vCPU, 4 GB RAM** (works on 2 GB with swap), Ubuntu 22.04/24.04.

## Option A: any Linux VM (permanent link)
Works on Azure for Students, AWS EC2, Google Cloud, DigitalOcean, Oracle Cloud, etc.

1. Create an Ubuntu VM and allow inbound **TCP 80** (and 443 if you will use a domain) in its firewall /
   security group.
2. SSH in and install Docker:
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER && newgrp docker
   ```
3. Get the code and create `.env` with random values:
   ```bash
   git clone https://github.com/meenakshisatpathy20-del/JARVIS.git && cd JARVIS
   for k in POSTGRES_PASSWORD APP_DB_PASSWORD CHAOS_DB_PASSWORD CHAOS_TOKEN INGEST_TOKEN DIAGNOSIS_SIGNING_KEY; do echo "$k=$(openssl rand -hex 16)"; done > .env
   echo "GEMINI_API_KEY=" >> .env
   ```
4. Start it:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.deploy.yml up -d --build
   ```
5. Open `http://<VM-public-IP>/` (wait ~60 s for warm-up), then `http://<VM-public-IP>/lab`.

**HTTPS with a domain (optional):** point a DNS A record at the VM, add `SITE_ADDRESS=your.domain` to
`.env`, open port 443, and run step 4 again. Caddy obtains the certificate automatically.

## Option B: GitHub Codespaces (free, temporary link)
The repo includes `.devcontainer/`, so setup is automatic: Docker is installed, `.env` is generated with
random secrets, and the demo starts on port **8080** every time the codespace starts or wakes up.

1. On the repo page: **Code → Codespaces → Create codespace on main**.
2. Wait for the terminal to print `J.A.R.V.I.S. is starting on port 8080` (first time: about 5–8 minutes).
3. Open the **Ports** tab, right-click port **8080 → Port Visibility → Public**.
4. Copy its **Forwarded Address** (`https://<name>-8080.app.github.dev`): that is the live link.
   The lab is the same address plus `/lab`.

The codespace sleeps after 30 idle minutes. Reopen it from GitHub (**Code → Codespaces**) before judging;
the demo restarts by itself and the link stays the same. The 4-core machine uses free Codespaces hours
(GitHub's free quota covers many hours of demos per month).

## Operating it
- Logs: `docker compose -f docker-compose.yml -f docker-compose.deploy.yml logs -f jarvis`
- Stop: `docker compose -f docker-compose.yml -f docker-compose.deploy.yml down`
- Fresh demo (wipe DB + incidents): add `-v` to the stop command.
- Update after a push: `git pull` then step 4 again.

## Public-demo caveat
Anyone with the link can press **Break** in the lab. Chaos allows one fault at a time and **Heal** restores
everything, so the worst case is a broken demo until someone heals it. Share the link with judges only.
