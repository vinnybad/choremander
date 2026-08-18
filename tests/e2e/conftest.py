"""Fixtures for the container-backed end-to-end suite.

Every test here drives the real Home Assistant instance started by
`./dev/start.sh`. Nothing in this directory imports Home Assistant itself.
"""

from __future__ import annotations

from collections.abc import Generator
import os
from pathlib import Path
import subprocess

import pytest
import pytest_socket

from .environment import environment_unavailable
from .ha_client import HAClient, HomeAssistantError

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def seeded_environment() -> None:
    """Reset Choremander's stored data to the committed fixture once per run.

    These tests consume real, persistent state: daily limits are spent, and a
    pending reward claim cannot be cleared through any public service (there is
    no reject_reward). Without a reset, a second run starts from wherever the
    previous one stopped and asserts against different numbers.

    Set CHOREMANDER_SKIP_SEED=1 to skip this when iterating on a single test.
    """
    if os.environ.get("CHOREMANDER_SKIP_SEED") == "1":
        return

    seed = REPO_ROOT / "dev" / "seed.sh"
    if not seed.exists():
        environment_unavailable(f"{seed} is missing; cannot guarantee a clean starting state.")

    result = subprocess.run([str(seed)], cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        environment_unavailable(
            "Could not reset the dev environment - is it running?\n"
            f"{seed} exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
        )


@pytest.fixture
def allow_local_sockets() -> Generator[None]:
    """Re-enable loopback networking for this suite only.

    pytest-homeassistant-custom-component ships pytest-socket, which blocks all
    network access. That is correct for the in-process unit tests but fatal
    here, since these tests exist precisely to talk to the container over HTTP.
    Access is narrowed to loopback rather than opened wholesale, so a test that
    reaches for the public internet still fails.

    This is function-scoped because pytest-socket re-arms its block before every
    test; anything depending on the network must therefore request it directly
    rather than rely on a session-scoped setup.
    """
    # Order matters: disable_socket() swaps in a socket class that refuses to
    # construct at all, so it must be lifted before the host allow-list (which
    # only guards connect()) can have any effect.
    pytest_socket.enable_socket()
    pytest_socket.socket_allow_hosts(["127.0.0.1", "localhost", "::1"], allow_unix_socket=True)
    yield
    pytest_socket.disable_socket(allow_unix_socket=True)


@pytest.fixture
def ha(allow_local_sockets: None) -> HAClient:
    """A client for the running dev environment, or a clear reason why not."""
    try:
        client = HAClient.from_token_file()
    except HomeAssistantError as err:
        environment_unavailable(str(err))

    if not client.is_up():
        environment_unavailable(
            f"Home Assistant is not responding at {client.base_url}. Run `./dev/start.sh` first."
        )

    return client


@pytest.fixture
def restore_points(ha: HAClient) -> Generator[None]:
    """Snapshot every child's balance and put it back after the test.

    The end-to-end suite mutates persistent state in a long-lived container, so
    without this a run would leave the environment different from how it found
    it and a second run would assert against different numbers.
    """
    before = {c["id"]: (c["name"], int(c["points"])) for c in ha.overview()["children"]}
    yield
    for child_id, (name, original) in before.items():
        current = ha.points(name)
        if current != original:
            ha.set_points(child_id, original, current)
