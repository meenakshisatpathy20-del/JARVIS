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

## Option A: a cloud VM (permanent link): Oracle Cloud Always Free, Azure, AWS, GCP
One command on a fresh Ubuntu VM does everything (Docker, VM firewall, code, `.env`, start, auto-restart
after reboot):
```bash
curl -fsSL https://raw.githubusercontent.com/meenakshisatpathy20-del/J.A.R.V.I.S/main/scripts/vm_setup.sh | bash
```
It prints the live link: `http://<VM-public-IP>:8000/` (lab at `/lab`). Run it again any time to update.

### Oracle Cloud Always Free, step by step
1. Sign up at cloud.oracle.com (a card is needed for verification only; Always Free resources are not
   charged). Pick a home region near you, e.g. India South (Hyderabad) or India West (Mumbai).
2. **Compute → Instances → Create instance**
   - Image: **Canonical Ubuntu 22.04** (or 24.04)
   - Shape: **Change shape → Ampere → VM.Standard.A1.Flex**, **2 OCPU, 12 GB** (Always Free eligible)
   - Networking: create a new VCN with a **public subnet**, **assign a public IPv4 address**
   - SSH keys: **Generate a key pair for me → Save private key**
   - If it says *Out of capacity*, try another availability domain or try again a little later.
3. **Open port 8000 in Oracle's firewall:** instance page → **Subnet** link → **Security Lists** →
   Default Security List → **Add Ingress Rules**: Source CIDR `0.0.0.0/0`, IP Protocol TCP,
   Destination port range `8000`.
4. **SSH in** (Windows PowerShell; the key is in Downloads):
   ```bash
   icacls "$HOME\Downloads\ssh-key.key" /inheritance:r /grant:r "$($env:USERNAME):R"
   ssh -i "$HOME\Downloads\ssh-key.key" ubuntu@<VM-public-IP>
   ```
5. Run the one-command setup above. Open the printed link and wait ~60 s for warm-up.

The image builds natively on Oracle's ARM CPUs (Python, Postgres and Caddy images are multi-arch).

## Option B: GitHub Codespaces (free, temporary link)
The repo includes `.devcontainer/`, so setup is automatic: Docker is installed, `.env` is generated with
random secrets, and the demo starts on port **8000** every time the codespace starts or wakes up.

1. On the repo page: **Code → Codespaces → Create codespace on main**.
2. Wait for the terminal to print `J.A.R.V.I.S. is starting on port 8000` (first time: about 5–8 minutes).
3. Open the **Ports** tab, right-click port **8000 → Port Visibility → Public**.
4. Copy its **Forwarded Address** (`https://<name>-8000.app.github.dev`): that is the live link.
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
