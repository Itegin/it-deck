"""Shared setup for the Python tests.

The backend reads ITDECK_DATA_DIR and ITDECK_FRONTEND_DIR at import time, so
they are pointed at a throwaway directory and the repo's frontend *before*
anything imports it. backend/ and agents/windows/ go on sys.path the same way
the standalone build's --paths puts them there.
"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_DATA = tempfile.mkdtemp(prefix="itdeck-test-")

os.environ["ITDECK_DATA_DIR"] = _DATA
os.environ["ITDECK_FRONTEND_DIR"] = str(REPO / "frontend")
os.environ["AGENT_TOKEN"] = "test-token"
os.environ["CLIENT_TOKEN"] = "test-client"

sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "agents" / "windows"))
