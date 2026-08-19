"""pytest wiring for the milsim test suite.

There is no repo-root pytest config (the only pyproject lives in openra-rl/ and
its testpaths do not cover milsim), so this conftest carries everything the
milsim tests need:

  * sys.path entries for the repo root and openra-rl/, matching the bootstrap
    each script does for its standalone ``python milsim/tests/test_x.py`` run.
  * the ``integration`` marker used by the seven server-dependent modules.
  * fixtures that skip — visibly — when the OpenRA-RL server is absent.
"""

import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, "..", ".."))
_OPENRA_RL = os.path.join(_REPO_ROOT, "openra-rl")

for _path in (_TESTS_DIR, _OPENRA_RL, _REPO_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _openra_server import SERVER_URL, server_available, start_hint  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: needs a live OpenRA-RL server (set OPENRA_RL_URL to point at one)",
    )


@pytest.fixture(scope="session")
def openra_server_url():
    """Base URL of the OpenRA-RL server under test."""
    return SERVER_URL


@pytest.fixture(scope="session")
def openra_server(openra_server_url):
    """Skip the calling test unless an OpenRA-RL server is actually reachable.

    A skip, not a silent pass: the check never runs, and pytest says so.
    """
    if not server_available(openra_server_url):
        pytest.skip(start_hint(openra_server_url))
    return openra_server_url
