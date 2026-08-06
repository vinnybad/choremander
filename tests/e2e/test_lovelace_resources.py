"""End-to-end guard for issue #2: Choremander must not erase other resources.

`ResourceStorageCollection` is lazy - `loaded` is False and its in-memory `data`
is empty until `async_load()` reads `.storage/lovelace_resources`. Creating
items on an unloaded collection made the debounced save serialise only the rows
Choremander had just added, truncating the file to seven entries and wiping
every other integration's cards. Users saw all their custom cards disappear, and
disappear again on every reboot after they re-added them.

These assert against the real file on disk rather than the REST API, because
that file *is* the artefact the issue is about - and Lovelace only exposes
resources over the websocket API, which this suite deliberately does not speak.

Keep this module import-clean: `tests/unit/test_fixtures_in_sync.py` imports
`CARDS`, `URL_BASE` and `THIRD_PARTY_URL` from here to check they have not
drifted from the integration. Anything with a side effect at module scope - a
`HAClient.from_token_file()`, a docker probe, a filesystem read - would break
collection of the entire unit suite, not just this file. Do that work inside
tests or fixtures.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any
import urllib.parse

import pytest

from .ha_client import HAClient

REPO_ROOT = Path(__file__).resolve().parents[2]
RESOURCE_STORE = REPO_ROOT / "dev" / "config" / ".storage" / "lovelace_resources"
THIRD_PARTY_URL = "/local/third-party-card.js"

# Spelled out rather than imported from the integration: nothing in this
# directory imports Home Assistant, and `frontend.py` pulls it in.
# `tests/unit/test_fixtures_in_sync.py` fails if these drift from `CARDS`.
URL_BASE = "/choremander"
CARDS = (
    "choremander-child-card.js",
    "choremander-rewards-card.js",
    "choremander-approvals-card.js",
    "choremander-points-card.js",
    "choremander-reorder-card.js",
    "choremander-activity-card.js",
    "choremander-streak-card.js",
    "choremander-schedule-card.js",
)

pytestmark = pytest.mark.e2e


def _read_store() -> list[dict[str, Any]]:
    """The resource rows Home Assistant has actually written to disk."""
    if not RESOURCE_STORE.exists():
        pytest.skip(f"{RESOURCE_STORE} does not exist; run `./dev/start.sh` first.")
    return json.loads(RESOURCE_STORE.read_text())["data"]["items"]


def _wait_for_store(predicate: Any, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Poll the store until `predicate` holds.

    Home Assistant debounces resource writes by ten seconds, so the file lags
    behind startup and a bare read races the container.
    """
    deadline = time.monotonic() + timeout
    items = _read_store()
    while time.monotonic() < deadline:
        items = _read_store()
        if predicate(items):
            return items
        time.sleep(1.0)
    return items


def _has_all_cards(items: list[dict[str, Any]]) -> bool:
    urls = {item["url"].split("?")[0] for item in items}
    return all(f"{URL_BASE}/{card}" in urls for card in CARDS)


def test_choremander_registers_all_of_its_cards(ha: HAClient) -> None:
    """Baseline: the integration really did write its own resources."""
    items = _wait_for_store(_has_all_cards)
    urls = {item["url"].split("?")[0] for item in items}

    missing = [card for card in CARDS if f"{URL_BASE}/{card}" not in urls]
    assert not missing, f"Choremander did not register {missing}; store holds {sorted(urls)}"


def test_the_third_party_resource_survives_startup(ha: HAClient) -> None:
    """Issue #2: a resource Choremander did not create must still be there."""
    items = _wait_for_store(_has_all_cards)
    urls = [item["url"] for item in items]

    assert THIRD_PARTY_URL in urls, (
        f"{THIRD_PARTY_URL} was erased from {RESOURCE_STORE.name} by Choremander's "
        f"card registration. The store now holds {urls}"
    )


def _is_up(ha: HAClient) -> bool:
    """`HAClient.is_up`, but tolerant of a container that is mid-restart.

    A socket reset while Home Assistant is shutting down surfaces as a bare
    `ConnectionResetError`, which urllib does not wrap into `URLError` and the
    client therefore does not catch.
    """
    try:
        return ha.is_up()
    except OSError:
        return False


def _container_publishing(base_url: str) -> str | None:
    """The container serving `base_url`, found by its published port.

    Resolved from the port rather than hardcoded so this works against whatever
    instance `dev/.token` points at, including a throwaway one on another port.
    """
    port = urllib.parse.urlparse(base_url).port or 8123
    result = subprocess.run(
        ["docker", "ps", "--filter", f"publish={port}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    names = result.stdout.split()
    return names[0] if names else None


def _docker(*args: str, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def _wait_until_up(ha: HAClient, container: str) -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline and not _is_up(ha):
        time.sleep(2)
    assert _is_up(ha), f"{container} did not come back up"


def test_a_restart_preserves_every_resource(ha: HAClient) -> None:
    """The reported symptom recurred on every reboot, not just at install."""
    if os.environ.get("CHOREMANDER_SKIP_DISRUPTIVE") == "1":
        pytest.skip("CHOREMANDER_SKIP_DISRUPTIVE=1; this test restarts the container.")

    before = _wait_for_store(_has_all_cards)
    assert THIRD_PARTY_URL in [item["url"] for item in before], (
        "precondition: the third-party resource should be present before restarting"
    )

    container = _container_publishing(ha.base_url)
    if container is None:
        pytest.skip(f"No running container publishes the port of {ha.base_url}.")

    result = _docker("restart", container)
    if result.returncode != 0:
        pytest.skip(f"Could not restart {container}:\n{result.stdout}\n{result.stderr}")
    _wait_until_up(ha, container)

    # Stop it again before reading. Polling for a predicate would be useless
    # here: every card is already in the file from before the restart, so the
    # first read satisfies it and returns long before the restarted instance has
    # written anything. Home Assistant debounces resource saves by ten seconds
    # and flushes them on shutdown, so a graceful stop is what makes a
    # truncating write observable rather than a race.
    stopped = _docker("stop", container)
    if stopped.returncode != 0:
        pytest.skip(f"Could not stop {container}:\n{stopped.stdout}\n{stopped.stderr}")

    try:
        after = _read_store()
    finally:
        started = _docker("start", container)
        if started.returncode == 0:
            _wait_until_up(ha, container)

    after_urls = [item["url"] for item in after]

    assert THIRD_PARTY_URL in after_urls, (
        f"{THIRD_PARTY_URL} survived install but was erased by a restart. "
        f"The store now holds {after_urls}"
    )
    assert sorted(after_urls) == sorted(item["url"] for item in before), (
        "a restart must neither drop nor duplicate resources; "
        f"before={sorted(item['url'] for item in before)} after={sorted(after_urls)}"
    )
