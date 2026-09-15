# IT-Deck

![IT-Deck logo](frontend/icons/icon-180.png)

Self-hosted universal control surface that turns an old iPhone into a deck
for your PC.

*(Screenshots to be added — capturing them requires a live device, out of
scope for this edit.)*

## Quick start (standalone, v0.3.0+)

Runs entirely on the PC you want to control — no separate server, no
Docker, no Ansible.

```
git clone https://github.com/Itegin/it-deck.git
cd it-deck
standalone\build.ps1
```

Needs **Python 3.7–3.12** on the machine that *builds* it (`comtypes`, used
for audio control, doesn't support 3.13/3.14 — see `agents/windows/
requirements.txt`). The resulting `standalone\dist\ITDeck.exe` (~23 MB) is
fully standalone — the PC that *runs* it needs nothing installed, not even
Python.

Run `ITDeck.exe` once. On first launch it:

1. Adds an **IT-Deck** shortcut to your Desktop — every launch after this
   first one, use that instead of `standalone\dist\ITDeck.exe`.
2. Generates `AGENT_TOKEN` (random) and `CLIENT_TOKEN` (defaults to
   `admin` — a self-hosted LAN gets little real security from a token
   anyway; hand-edit `%LOCALAPPDATA%\IT-Deck\config.env` if you want a real
   one), and picks a free port. Kept across restarts — deleting
   `config.env` invalidates the URL your phone already has.
3. Starts the backend and the Windows agent as two separate processes, so
   closing/relaunching one doesn't take down the other.
4. Opens a small window with the Dashboard and Studio links (and a copy
   button) — and prints the same links in the console, e.g.
   `http://192.168.0.15:8000/?token=admin`. Backend/agent logs go to
   `%LOCALAPPDATA%\IT-Deck\logs\`, not the console, so that link doesn't
   scroll out of view.

Open the Dashboard link on your phone's browser once (same Wi-Fi as the PC)
— no typing needed, the token's in the link and gets stored and stripped
from the URL after. Press Ctrl+C in the console window to stop everything.

There's no prebuilt download yet — `standalone\build.ps1` is currently the
only way to get `ITDeck.exe`, so it's a build step on every PC you want to
control (see `standalone/launcher.py` for how config generation, LAN
detection, and the two-process split work).

**Requirements**: a Windows PC to control (the agent depends on
`pycaw`/`comtypes` — Windows COM audio APIs — and `pywin32`, so it only runs
on Windows, by design, in the interactive user session), and an iPhone or
any modern phone with a browser on the same LAN.

## What it does

The Dashboard (phone) shows a grid of tiles; tapping one sends a command over
a WebSocket to the Windows agent, which executes it and reports back. Every
command resolves within 5 seconds — ok, error, or timeout — and the tile shows
which. Long-pressing a tile opens a context menu with a **Force Stop** option
for killing a stuck process.

A fresh install seeds 7 tiles, all wired to a real command:

- **Terminal** — launch an app (`launch_app`; the seeded config opens Notepad)
- **Mic** — mute/unmute the microphone; turns red while muted
- **Volume** — drag to set system output volume
- **Headphones** — mute/unmute speaker output
- **Audio Switch** — swap between two configured output devices, showing the
  current one under the label
- **Screenshot** — capture the PC's primary monitor to the **PC's clipboard**
- **VPN** — start/stop a configured VPN client process

(Three earlier placeholder tiles — Lights, Spotify, Sleep PC — had no
handler behind them and just returned `unknown command`; they're gone from
new installs, and a startup fixup removes them from existing databases too.)

That catalog is only the starting point — **Studio Mode** (`/studio.html`) is
a separate desktop-only admin page for editing it directly (add/edit/delete
tiles, pick audio devices from the live agent, compact the layout). The phone
Dashboard never mutates the catalog.

## Legacy: multi-device server deployment

Before v0.3.0, IT-Deck's backend ran as a Docker container on a separate
server, reached by a Windows agent and a phone over the LAN. This still
works and is documented below, but the standalone mode above is the
recommended path for a single controlled PC.

### Requirements

- A Debian/Linux server (or any Docker host) to run the backend container
- A Windows PC to control. The agent depends on `pycaw`/`comtypes` (Windows
  COM audio APIs) and `pywin32`, so it only runs on Windows, by design, and it
  must run in the interactive user session. **Python 3.7–3.12** — `comtypes`
  does not support 3.13/3.14.
- An iPhone or any modern phone with a browser — it's a PWA served over the
  LAN, not a native app

### Setup

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

### Automated server setup

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

## Status

v0.3.0 (tagged) — personal project, active development, API may change.

See [docs/IT-Deck_Tech_Reference.md](docs/IT-Deck_Tech_Reference.md) for the
full architecture reference — message shapes, file-by-file map, database
schema, tile and theme internals, platform constraints, and known limitations.
