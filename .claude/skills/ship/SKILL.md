---
name: ship
description: Commit, push, deploy to Athlon, and verify every link in the chain landed.
disable-model-invocation: true
argument-hint: [commit message]
allowed-tools: Bash(git add *) Bash(git commit *) Bash(git push *) Bash(git status *) Bash(git log *) Bash(git diff *) Bash(git fetch *) Bash(ssh athlon *)
---

## Ground truth right now
- Working tree: !`git status --short`
- Unpushed commits: !`git fetch origin -q; git log origin/main..HEAD --oneline`
- Athlon's actual HEAD: !`ssh athlon "cd ~/controlhub && git log -1 --oneline"`
- Agent code, uncommitted: !`git status --short -- agents/windows`
- Agent code, committed but unpushed: !`git log origin/main..HEAD --name-only -- agents/windows`

Every line above is measured, not remembered. The `git fetch` matters: without
it, `origin/main` is only as fresh as the last fetch, and both the "unpushed"
and "agent code" lines silently lie whenever something was pushed from another
machine.

## Your task
Ship the current work. Commit message: $ARGUMENTS

### 1. Commit
If the working tree is dirty, stage and commit. List untracked files first and
do **not** blanket-stage them — an untracked file that isn't obviously part of
this work (a stray audit dump, a scratch log) stays out of the commit. Say which
untracked files you skipped.

If the tree is already clean, say so and go to step 2.

### 2. Push
Push to origin/main. Then confirm `git log origin/main..HEAD --oneline` is
**empty**. If it isn't, the push didn't land — stop and report that, don't
continue to deploy.

### 3. Pull + rebuild on Athlon

```
ssh athlon 'cd ~/controlhub && ./deploy.sh'
```

Pass a timeout of ~600000 to the Bash call — `docker compose up -d --build` on a
cold cache runs well past the 120s default, and a timeout there looks exactly
like a failed deploy.

Do **not** put `git pull` in front of this. `deploy.sh` step 2/5 already does
`git pull --rebase`; a plain `git pull` first is a merge-vs-rebase mismatch that
can leave a merge commit on the server and trip deploy.sh's own "unpushed
commits on server" warning.

`deploy.sh` runs `set -euo pipefail` and exits 1 on any failure, so a non-zero
exit is the gate and the Russian marker line tells you which link broke. Read
the real output for these:

| Output line | Meaning |
|---|---|
| `✓ Код в контейнере актуален` | step 5/5 md5 passed — container matches disk |
| `✗ Код в контейнере УСТАРЕЛ` | container is **stale**. Remedy: `docker compose build --no-cache && docker compose up -d` |
| `✗ На сервере есть правки` | step 1/5 aborted — someone edited files directly on Athlon. Show the server's `git status --short` and **stop**. Do not run `git checkout --` on their behalf. |
| `✗ Не поднялся за 20 сек` | backend never became healthy — `docker compose logs backend` |

Never report "md5 verified" unless you actually saw `✓ Код в контейнере
актуален` in the output.

### 4. Agent restart
If either agent-code line in the ground truth above is non-empty, the Windows
agent is running old code. `deploy.sh` rebuilds the backend container only — it
has **no** effect on the agent process.

Tell the user explicitly that the agent must be restarted via the "IT-Deck
Agent" desktop shortcut, then hand off to the `restart-windows-agent` skill
rather than restating its steps here. Claude Code cannot perform that restart.

Once they confirm the restart, verify the last link by measurement:

```
ssh athlon 'cd ~/controlhub && ./check.sh'
```

Look for `АГЕНТ: ✓ Подключён`. `✗ Отключён` means the agent is not back; `?` means
it hasn't connected since the container started. Until you see `Подключён`, the
agent change is **not live** — say so instead of assuming.

### 5. Report the chain
One line each, and only mark a link done if you measured it:

```
committed        — <sha> <subject>   (or: tree was already clean)
pushed           — origin/main..HEAD empty
pulled+rebuilt   — Athlon HEAD <sha>, deploy.sh exit 0
md5 verified     — step 5/5: ✓ Код в контейнере актуален
agent restart    — needed / not needed / confirmed via check.sh
```

Then remind the user: **Ctrl+Shift+R in the browser** for cached JS. The
frontend is bind-mounted, not baked into the image, so a rebuild never busts the
browser cache.
