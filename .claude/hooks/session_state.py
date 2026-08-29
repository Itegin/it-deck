import json, subprocess, os

root = os.environ.get("CLAUDE_PROJECT_DIR", ".")
def g(*a): return subprocess.run(["git", "-C", root, *a],
                                 capture_output=True, text=True).stdout.strip()

try:
    athlon = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=3", "athlon",
         "cd ~/controlhub && git log -1 --oneline"],
        capture_output=True, text=True, timeout=5
    )
    athlon_line = athlon.stdout.strip() if athlon.returncode == 0 else "unreachable"
except (subprocess.TimeoutExpired, OSError):
    athlon_line = "unreachable"

lines = [
    f"HEAD: {g('log', '-1', '--oneline')}",
    f"Uncommitted: {g('status', '--short') or 'clean'}",
    f"Unpushed: {g('log', 'origin/main..HEAD', '--oneline') or 'none'}",
    f"Athlon HEAD: {athlon_line}",
]
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "IT-Deck state at session start:\n" + "\n".join(lines),
}}))
