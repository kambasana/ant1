"""Shared helpers for milsim tests that need a live OpenRA-RL server.

The seven integration scripts in this directory (basics, wego, commander, isr,
scenario, spatial, full_game) drive a real OpenRA-RL game server over a
websocket. Without one they used to die on a raw ``ConnectionError`` traceback
from deep inside the client.

This module makes the dependency explicit:

  * :data:`SERVER_URL` reads ``OPENRA_RL_URL`` (default ``http://localhost:8000``)
    so the address is configurable instead of hardcoded in seven places.
  * :func:`server_available` does a cheap TCP probe before anything connects.
  * :data:`START_HINT` is the actionable "how do I get one" message.

Importable both as ``python milsim/tests/test_wego_turns.py`` (the invocation
documented in docs/OPENRA_RL_INTEGRATION.md) and under pytest.
"""

import os
import socket
from urllib.parse import urlparse

DEFAULT_SERVER_URL = "http://localhost:8000"

#: Base URL of the OpenRA-RL server, overridable via ``OPENRA_RL_URL``.
SERVER_URL = os.environ.get("OPENRA_RL_URL", DEFAULT_SERVER_URL)

START_HINT = (
    "OpenRA-RL server not reachable at {url}.\n"
    "Start one first:\n"
    "  cd openra-rl && OPENRA_PATH=$(pwd)/OpenRA "
    "python -m openra_env.server.app --port 8000\n"
    "Or point the tests elsewhere with OPENRA_RL_URL=http://host:port"
)


def _host_port(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def server_available(url: str = None, timeout: float = 2.0) -> bool:
    """True if something is listening on the server's host/port."""
    host, port = _host_port(url or SERVER_URL)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def start_hint(url: str = None) -> str:
    return START_HINT.format(url=url or SERVER_URL)


def require_server_or_explain() -> bool:
    """Script-mode guard: print the actionable hint and report reachability."""
    if server_available():
        return True
    print(f"\nSKIPPED: {start_hint()}")
    return False
