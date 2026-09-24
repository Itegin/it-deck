"""The two shared secrets, checked in one place.

AGENT_TOKEN gates every write endpoint and the agent's /ws/agent handshake;
CLIENT_TOKEN gates the Dashboard's /ws/client handshake. Both are read from
the environment on every check rather than once at import, so a test (or a
launcher that sets them after import) sees the current value.

Both fail closed: an unset or empty variable rejects everything. Without that,
a missing env var (None) would compare equal to a missing header (None) and
let an unauthenticated caller straight through.
"""

import hmac
import os

from fastapi import HTTPException


def token_ok(env_var: str, presented: object) -> bool:
    expected = os.environ.get(env_var)
    if not expected or not isinstance(presented, str):
        return False
    # compare_digest, not ==: a plain comparison returns as soon as one
    # character differs, which leaks how much of a guess was right.
    return hmac.compare_digest(presented.encode(), expected.encode())


def check_agent_token(x_agent_token: str | None) -> None:
    if not token_ok("AGENT_TOKEN", x_agent_token):
        raise HTTPException(status_code=401, detail="missing or invalid X-Agent-Token")
