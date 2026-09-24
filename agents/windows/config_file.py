"""The one value the agent re-reads from the launcher's config.env."""

import os


def current_token(fallback: str) -> str:
    """AGENT_TOKEN as it is now, not as it was when this process started.

    Studio can change the token while everything runs (backend/app/api/
    access.py writes config.env and applies it to the backend at once). The
    agent's open socket stays authenticated, but its next connect -- after a
    backend restart, or a respawn by the launcher with its start-up
    environment -- must present the new value. The standalone launcher passes
    the file's path as ITDECK_CONFIG_FILE; without it (a legacy .env agent)
    the value from the environment is the only one there is.
    """
    path = os.environ.get("ITDECK_CONFIG_FILE")
    if not path:
        return fallback
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                key, sep, value = line.strip().partition("=")
                if sep and key.strip() == "AGENT_TOKEN" and value.strip():
                    return value.strip()
    except OSError:
        pass
    return fallback
