"""Refuse to publish a release that isn't ready.

Run by .github/workflows/release.yml before a manual release, and handy by
hand: `python scripts/check_release.py v0.5.6`.

Checks that the tag matches ITDECK_VERSION in standalone/launcher.py (the
version the app reports, and compares against GitHub's latest release) and,
once frontend/whats-new.json exists, that the version has its entry there --
every public update is meant to tell its users what changed.
"""

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def problems(tag: str) -> list[str]:
    found = []
    version = tag.lstrip("vV")
    launcher = (REPO / "standalone" / "launcher.py").read_text(encoding="utf-8")
    match = re.search(r'^ITDECK_VERSION = "([^"]+)"', launcher, re.MULTILINE)
    if not match:
        found.append("ITDECK_VERSION not found in standalone/launcher.py")
    elif match.group(1) != version:
        found.append(f"tag {tag} but ITDECK_VERSION is {match.group(1)} -- bump it in the tagged commit")

    whats_new = REPO / "frontend" / "whats-new.json"
    if whats_new.exists():
        entries = json.loads(whats_new.read_text(encoding="utf-8"))
        if not any(entry.get("version") == version for entry in entries):
            found.append(f"frontend/whats-new.json has no entry for {version}")
    return found


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_release.py vX.Y.Z")
        return 2
    found = problems(sys.argv[1])
    for problem in found:
        print(f"release not ready: {problem}")
    if not found:
        print(f"{sys.argv[1]} is ready to release")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
