import json, sys

data = json.load(sys.stdin)
fp = (data.get("tool_input", {}).get("file_path") or "").replace("\\", "/")
if "/agents/windows/" in fp and not fp.endswith((".md", ".txt")):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            "agents/windows/ was modified. The running agent process is "
            "still executing the OLD code. Do NOT report this change as "
            "working until the agent has been restarted. deploy.sh does "
            "not restart the agent."
        ),
        "systemMessage": "agent code changed - restart required"
    }}))
