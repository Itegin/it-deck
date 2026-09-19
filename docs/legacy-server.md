# Legacy: multi-device server deployment

Before v0.3.0 the backend ran as a Docker container on a separate server,
reached over the LAN by a Windows agent and a phone. It still works, and this
is where its setup lives now — the README covers the standalone `ITDeck.exe`,
which is the recommended path for a single controlled PC.

How the two halves actually deploy and what `deploy.sh`, `check.sh` and
`backup.sh` do is §9 of the
[tech reference](IT-Deck_Tech_Reference.md); this page is the initial setup.

## Requirements

- A Debian/Linux server (or any Docker host) to run the backend container
- A Windows PC to control. The agent depends on `pycaw`/`comtypes` (Windows
  COM audio APIs) and `pywin32`, so it only runs on Windows, by design, and it
  must run in the interactive user session. **Python 3.7–3.12** — `comtypes`
  does not support 3.13/3.14.
- An iPhone or any modern phone with a browser — it's a PWA served over the
  LAN, not a native app

## Setup

1. Clone this repo onto the server that will run the backend:
   ```
   git clone https://github.com/Itegin/it-deck.git
   cd it-deck
   cp .env.example .env
   ```
   `AGENT_TOKEN` — the shared secret the Windows agent and Studio Mode both
   need — is the only value the backend itself actually reads at runtime.
   The other two entries in `.env.example` are carried so the agent, compose
   and the firewall agree: `SERVER_IP` appears nowhere in `backend/`, and
   changing `SERVER_PORT` here alone does **not** move the backend, because
   the port also lives in `docker-compose.yml`'s mapping and
   `backend/Dockerfile`'s `uvicorn --port`. Change all three together.
   (`backend/app/config.py` reads `SERVER_PORT`, but nothing imports it.)

2. Start the backend, building the image locally:
   ```
   docker compose up -d --build
   ```
   `./deploy.sh` does the same thing plus a git pull, a health check, and a
   check that the code inside the container matches the code on disk — run
   it instead once the server is already set up, for every later update.

3. Copy (or clone) `agents/windows/` onto the Windows PC being controlled:
   ```
   cd agents/windows
   pip install -r requirements.txt
   cp .env.example .env
   ```
   Fill in `SERVER_IP` (the backend server's LAN IP), `SERVER_PORT`,
   `AGENT_TOKEN` (same value as the backend's), and the audio/VPN values for
   your own hardware — see the comments in `.env.example`. The two
   `OUTPUT_DEVICE_*` values must be SoundVolumeView's full "Command-Line
   Friendly ID", not a bare device name.

4. Start the agent with `start_agent.bat`. On first run it creates an
   **IT-Deck Agent** shortcut on the desktop, which is how it's launched from
   then on; the window stays open only if the agent exited non-zero, and the
   text in it is the error. A singleton mutex stops two copies running at once.

   `install_task.ps1` registers a Scheduled Task to start the agent at logon
   (as the interactive user, never SYSTEM). Per `CLAUDE.md` that task is
   currently disabled on the maintainer's PCs in favour of the manual
   shortcut, so treat it as optional.

5. On the phone, open `http://<server-lan-ip>:8000` in the browser and add it
   to the home screen.

## Automated server setup

Steps 1–2 above can be done in one shot with the Ansible playbook in
`ansible/` against a fresh Debian 12 server:

```
ansible-galaxy collection install -r ansible/requirements.yml
cp ansible/inventory.yml.example ansible/inventory.yml
# edit ansible/inventory.yml with your server's IP, user, and SSH key
ansible-playbook -i ansible/inventory.yml ansible/site.yml
```

It provisions rather more than the manual steps: base packages, Docker via
get.docker.com (Debian's `docker.io` has no compose v2), a `ufw` firewall
(22/80/443/8000 open, default-deny inbound), **nginx as a TLS reverse proxy**
with a self-signed `itdeck.local` cert and WebSocket upgrade handling, the app
checkout and first `docker compose up`, a daily backup cron job, and a
loopback-only Prometheus + node_exporter monitoring stack (no firewall rule —
reach it with `ssh -L 9090:127.0.0.1:9090`). It also swaps Debian's apt
mirrors for a Yandex mirror.

It's safe to re-run: an existing `.env` is never overwritten, and the TLS cert
is not regenerated. The repo is cloned anonymously over **HTTPS**, so no git
SSH key is needed on the target — the key that is required is the
`ansible_ssh_private_key_file` in your `inventory.yml`, for Ansible's own SSH
connection to the server. See `ansible/site.yml` for what each step does and
why.

Note that after this playbook runs, the deck is reachable two ways —
`http://<ip>:8000` directly, and `https://<ip>` through nginx. Use the direct
HTTP one: the Dashboard opens its WebSocket as `ws://`, which a browser blocks
as mixed content on an HTTPS page.
