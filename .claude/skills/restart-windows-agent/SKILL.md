---
name: restart-windows-agent
description: Get the IT-Deck Windows agent restarted after changing code in agents/windows/, and verify the new process is actually running. Use whenever agent code changed and the running process needs to pick it up.
---

# Restart Windows Agent

The agent is started **manually** from the "IT-Deck Agent" desktop shortcut,
which points at `agents/windows/start_agent.bat`. It runs as a normal
console process in the interactive user session.

The "IT-Deck Agent" Scheduled Task registered by `install_task.ps1` is
**disabled on both PCs**. It is not what runs the agent — do not try to
start, stop, or query it, and do not report on its state.

`start_agent.bat` exits silently when `agent.py` returns 0, and only
`pause`s (leaving the window open) on a non-zero exit code. So an agent
window still sitting on screen means it **crashed**, and the text in that
window is the error.

## Claude cannot do this restart

Restarting means closing a console window and double-clicking a desktop
shortcut. Both are GUI actions on the Windows PC that Claude Code has no
way to perform. Do not fake it with PowerShell that starts a detached
process — that produces a differently-parented process than the shortcut
does and hides the crash window.

The job here is: **ask the user to restart, then verify they did.**

## Steps

1. Check whether an agent process is running right now:

   ```powershell
   Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
     Where-Object { $_.CommandLine -like '*agent.py*' } |
     Select-Object ProcessId, CreationDate
   ```

   Record the `ProcessId` and `CreationDate` — that is the "before" state.
   No rows means no agent is running at all.

2. Tell the user, in one line, exactly what to do:

   > Close the IT-Deck Agent console window, then launch the "IT-Deck
   > Agent" desktop shortcut again.

   Then stop and wait for them. Do not proceed to step 3 on your own.

   Restarting drops the agent's live WebSocket, so any in-flight command
   from the phone will fail until it reconnects. That is expected and is
   the point of the restart — don't ask the user to confirm it.

3. Once they say it's done, re-run the command from step 1 and compare:

   - **A new `ProcessId`, and a `CreationDate` later than the "before"
     value** → the restart took. Say so and move on.
   - **The same `ProcessId`** → nothing was restarted. The old code is
     still running. Say so plainly and ask again; do not proceed as if the
     change is live.
   - **No rows at all** → the agent isn't running. Most likely it crashed
     on startup and the console window is showing the traceback. Ask the
     user what the window says, and check `agents/windows/.env` exists and
     is valid.

4. Report the before/after in one line. Don't narrate each PowerShell call.

## Why this matters

`deploy.sh` rebuilds the backend container only; it has no effect on the
agent. A stale agent keeps running old code with no error at all — the
failure surfaces later as "the fix didn't work." Until step 3 shows a new
PID, treat any change under `agents/windows/` as **not live**, and don't
report it as working.
