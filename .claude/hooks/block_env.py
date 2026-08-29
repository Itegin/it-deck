import json, re, sys

data = json.load(sys.stdin)
fp = (data.get("tool_input", {}).get("file_path") or "").replace("\\", "/")
if re.search(r"(^|/)\.env$", fp):
    print("BLOCKED: .env is gitignored and machine-specific - edit it "
          "manually, not through Claude Code.", file=sys.stderr)
    sys.exit(2)
