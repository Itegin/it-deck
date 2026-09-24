"""Reading and rewriting the standalone launcher's config.env.

Only the standalone build has one: the launcher passes its path as
ITDECK_CONFIG_FILE. The legacy Docker setup takes its tokens from the
server's .env through docker-compose, which this process cannot rewrite, so
there the path is absent and callers treat the settings as read-only.

The format is the launcher's (standalone/launcher.py parse_config): one
KEY=value per line, '#' comments, no quoting.
"""

import os
import tempfile
from pathlib import Path

CONFIG_FILE_ENV = "ITDECK_CONFIG_FILE"


def config_path() -> Path | None:
    value = os.environ.get(CONFIG_FILE_ENV)
    return Path(value) if value else None


def update_config(path: Path, updates: dict[str, str]) -> None:
    """Set `updates` in the file at `path`, leaving every other line as it is.

    Line by line rather than regenerated: config.env is documented as
    hand-editable, and a person's comments, UPDATE_CHECK=0 or VPN_PATH must
    survive a token change untouched. A key that isn't there yet is appended.

    Written to a temporary file beside it and swapped in with os.replace, so
    a crash mid-write can't leave a half-written config -- one the launcher
    would read on the next start as "no tokens" and quietly reset to admin.
    """
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen = set()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.partition("=")[0].strip()
        if key in updates:
            # Every occurrence, not just the first: the launcher reads the
            # last one and the agent the first, so a hand-edited file with a
            # key twice would otherwise have them disagree after a change.
            lines[index] = f"{key}={updates[key]}"
            seen.add(key)
    lines.extend(f"{key}={value}" for key, value in updates.items() if key not in seen)

    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
