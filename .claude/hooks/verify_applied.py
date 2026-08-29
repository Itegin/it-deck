import hashlib, json, os, subprocess, sys

data = json.load(sys.stdin)
session = data.get("session_id", "nosession")
root = os.environ.get("CLAUDE_PROJECT_DIR", ".")

diff = subprocess.run(["git", "-C", root, "diff", "HEAD"],
                      capture_output=True, text=True).stdout
if not diff.strip():
    sys.exit(0)          # nothing changed; nothing to verify

# Only nag once per distinct working-tree state, so we never loop forever.
stamp = os.path.join(os.environ.get("TEMP", "/tmp"),
                     f"itdeck-verify-{session}")
digest = hashlib.sha1(diff.encode()).hexdigest()
if os.path.exists(stamp) and open(stamp).read().strip() == digest:
    sys.exit(0)
open(stamp, "w").write(digest)

stat = subprocess.run(["git", "-C", root, "diff", "--stat", "HEAD"],
                      capture_output=True, text=True).stdout

print(
    "VERIFY BEFORE STOPPING. This is the actual working tree vs HEAD:\n\n"
    f"{stat}\n"
    "Check every change you just claimed to make against this diff. If "
    "something you reported is missing or only partially applied, apply it "
    "now. If a file under agents/windows/ appears above, the running agent "
    "has NOT picked it up unless it was restarted this session. When "
    "everything matches, say so in one line and stop.",
    file=sys.stderr,
)
sys.exit(2)
