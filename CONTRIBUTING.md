# Contributing to IT-Deck

IT-Deck turns a phone into a Stream Deck-style panel for a Windows PC. It has
three parts: a FastAPI backend, a vanilla-JS frontend with no build step, and
a Python agent on the PC. The standalone `ITDeck.exe` bundles all three.

Start with [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) (how it works, how to
run it, how to extend it). [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
explains why it is built this way.

## Set up (any OS)

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt Pillow psutil
cd backend && AGENT_TOKEN=dev CLIENT_TOKEN=dev ITDECK_DATA_DIR=../data \
  ITDECK_FRONTEND_DIR=../frontend ../.venv/bin/uvicorn app.main:app --reload
```

Open `http://localhost:8000/?token=dev` (the deck) and
`http://localhost:8000/studio.html` (Studio; its token is `dev`). The agent
and the exe build need Windows. See DEVELOPMENT.md §1.

## Before you push

```bash
ruff check .                              # lint (config in pyproject.toml)
.venv/bin/python -m pytest -q             # backend, agent logic, DB fixups, WebSocket
node --test tests/frontend.test.mjs       # frontend logic + cross-file consistency
find frontend/js -name '*.js' -print0 | xargs -0 -n1 node --check
```

CI runs all of this on Linux and on Windows for every pull request.

## Rules that are easy to break

The full list is in [`CLAUDE.md`](CLAUDE.md) (it is written for AI agents
but applies to everyone). The short version:

- **Keep in step.** A tile type lives in several places:
  - `frontend/js/tile-catalog.js`
  - `HANDLERS` in `agents/windows/agent.py`
  - `WIDGETS` in `frontend/js/widgets/index.js`
  - `ICONS` in `frontend/js/render.js`
  - Studio strings in `frontend/js/studio-i18n.js`, EN and RU with the
    same keys

  Themes live in four places. Tests check most of these, but not all.
- **Every command resolves within 5 s** (ok, error or timeout). A `req_id`
  with no result is a bug.
- **Startup fixups never touch user edits.** Guard every UPDATE on the value
  it upgrades *from*. One-shot inserts go through `schema_migration`.
- **Nothing on the phone may be heavy.** The deck targets iPhone X / Safari
  16.7: no WebGL, no blur stacks, no scroll animation.
- **Comments explain why**, not what. Look at the surrounding code for the
  expected density.

## Commits and versions

- Small commits with a conventional prefix (`fix:`, `feat:`, `perf:`,
  `docs:`, `chore:`). The body says what was wrong and why the fix is right.
- The version is `ITDECK_VERSION` in `standalone/launcher.py`. Bump it in
  the same commit you tag.
- Record user-visible changes in [`CHANGELOG.md`](CHANGELOG.md) under
  `Unreleased`.

## Releasing

A pushed `v*.*.*` tag only **builds**: the exe goes into the workflow's
artifacts and the Docker image is built but not pushed. Nothing reaches
users. To publish:

1. Bump `ITDECK_VERSION` and give the version its entry in
   `frontend/whats-new.json`. That is the short "What's new" card users see
   after updating. Commit, then tag `vX.Y.Z` and push the tag.
2. Check it: `python scripts/check_release.py vX.Y.Z`.
3. GitHub → Actions → **Build ITDeck.exe (tag) / publish a release** → *Run
   workflow* with the tag. This publishes the release with the exe, and the
   app's update check starts offering it.
4. For the Docker image (legacy server setup), do the same with **Build
   (tag) / push (manual) backend image**.
