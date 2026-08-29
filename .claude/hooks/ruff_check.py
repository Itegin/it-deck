import json
import re
import subprocess
import sys


def main():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return

    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", {})
    file_path = tool_input.get("file_path") or tool_response.get("filePath") or ""
    file_path = file_path.replace("\\", "/")

    if not re.search(r"(^|/)(backend|agents/windows)/.*\.py$", file_path):
        return

    try:
        result = subprocess.run(
            ["python3", "-m", "ruff", "check", file_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return

    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        if len(output) > 1000:
            output = output[:1000] + "\n... (truncated)"
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                "ruff reported issues in " + file_path + ":\n" + output
            ),
            "systemMessage": "ruff found issues",
        }}))


if __name__ == "__main__":
    main()
