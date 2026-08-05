"""End-to-end guard for the issue #2 recovery path.

`frontend.py` stops Choremander deleting other integrations' Lovelace resources,
but it cannot bring back rows already gone, and nothing else does either - HACS
registers a plugin's resource when the plugin is installed, never on startup.
`resource_repair.py` therefore looks for the wreckage and offers to put it back.

These drive the whole round trip against a real container: break the store the
way the bug did, restart, and check Home Assistant raises the repair and that
submitting it actually re-registers the resource. The repair flow is exercised
over `/api/repairs/issues/fix`, the same HTTP endpoints the Repairs panel uses.

Keep this module import-clean - see the note in `test_lovelace_resources.py`.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

import pytest

from .ha_client import HAClient, HomeAssistantError
from .test_lovelace_resources import (
    RESOURCE_STORE,
    THIRD_PARTY_URL,
    _container_publishing,
    _docker,
    _is_up,
    _wait_until_up,
)

ISSUE_ID = "deleted_lovelace_resources"
HANDLER = "choremander"

pytestmark = pytest.mark.e2e


def _read_store_file() -> dict[str, Any]:
    if not RESOURCE_STORE.exists():
        pytest.skip(f"{RESOURCE_STORE} does not exist; run `./dev/start.sh` first.")
    return json.loads(RESOURCE_STORE.read_text())


def _store_urls() -> list[str]:
    return [item["url"] for item in _read_store_file()["data"]["items"]]


def _start_fix_flow(ha: HAClient) -> Any:
    """Begin the repair flow, exactly as the Repairs panel does."""
    return ha._request(
        "/api/repairs/issues/fix",
        method="POST",
        payload={"handler": HANDLER, "issue_id": ISSUE_ID},
    )


def test_a_healthy_install_raises_no_repair(ha: HAClient) -> None:
    """Every resource is registered, so there must be nothing to restore.

    This is the false-positive guard, and it is the one that matters most: the
    dev environment has Choremander's own cards registered from `/choremander/`
    while the very same files are bind-mounted under `config/www/choremander/`.
    An over-eager detector reports all seven as deleted and offers duplicates.
    """
    assert THIRD_PARTY_URL in _store_urls(), (
        "precondition: the third-party resource should be registered"
    )

    with pytest.raises(HomeAssistantError) as err:
        _start_fix_flow(ha)

    # `RepairsFlowManager.async_create_flow` raises `UnknownStep` when the issue
    # does not exist or is not fixable, which the HTTP view turns into exactly
    # this 400. Anything else - a 200 with a form especially - means Choremander
    # decided a resource was missing when none is.
    assert "HTTP 400" in str(err.value) and "does not support" in str(err.value), (
        "with nothing missing there should be no fixable repair issue, but "
        f"starting the flow returned: {err.value}"
    )


def test_a_deleted_resource_is_detected_and_restored_by_the_repair(
    ha: HAClient,
) -> None:
    """The full recovery: break it like the bug did, then let the repair fix it."""
    if os.environ.get("CHOREMANDER_SKIP_DISRUPTIVE") == "1":
        pytest.skip("CHOREMANDER_SKIP_DISRUPTIVE=1; this test restarts the container.")

    container = _container_publishing(ha.base_url)
    if container is None:
        pytest.skip(f"No running container publishes the port of {ha.base_url}.")

    original = _read_store_file()
    assert THIRD_PARTY_URL in _store_urls(), "precondition: resource is registered"

    # Reproduce the damage: stop Home Assistant, drop the third-party row the
    # way the truncating save did, and boot it again.
    assert _docker("stop", container).returncode == 0, f"could not stop {container}"
    try:
        damaged = json.loads(json.dumps(original))
        damaged["data"]["items"] = [
            item for item in damaged["data"]["items"] if item["url"] != THIRD_PARTY_URL
        ]
        RESOURCE_STORE.write_text(json.dumps(damaged, indent=2))
        assert THIRD_PARTY_URL not in _store_urls(), "the row should now be gone"

        assert _docker("start", container).returncode == 0
        _wait_until_up(ha, container)

        # The check runs once Home Assistant has finished starting.
        deadline = time.monotonic() + 120
        flow: Any = None
        while time.monotonic() < deadline:
            try:
                flow = _start_fix_flow(ha)
                break
            except HomeAssistantError:
                time.sleep(3)

        assert flow is not None, (
            "Choremander should have raised a repair for the deleted resource, "
            "but no fix flow could be started within 120s"
        )
        assert flow["type"] == "form", f"expected a confirmation form, got {flow}"
        assert THIRD_PARTY_URL in flow["description_placeholders"]["resources"], (
            "the repair should name the resource it is about to restore; got "
            f"{flow['description_placeholders']}"
        )
        assert THIRD_PARTY_URL not in _store_urls(), (
            "showing the confirmation must not have written anything yet"
        )

        # Confirm it.
        result = ha._request(
            f"/api/repairs/issues/fix/{flow['flow_id']}", method="POST", payload={}
        )
        assert result["type"] == "create_entry", f"the repair did not finish: {result}"

        # Flush the debounced save so the restored row is really on disk.
        assert _docker("stop", container).returncode == 0
        restored_urls = _store_urls()
        assert _docker("start", container).returncode == 0
        _wait_until_up(ha, container)

        assert THIRD_PARTY_URL in restored_urls, (
            "the repair reported success but the resource is not in the store; "
            f"it holds {restored_urls}"
        )
    finally:
        # Put the environment back however the test ended. Restoring the store
        # matters even more than restarting the container: this test physically
        # deletes a row that `test_lovelace_resources.py` asserts is present, so
        # a failure anywhere above would leave that unrelated test failing until
        # somebody thought to run `dev/seed.sh`.
        if THIRD_PARTY_URL not in _store_urls():
            was_running = _is_up(ha)
            if was_running:
                subprocess.run(["docker", "stop", container], capture_output=True, timeout=300)
            RESOURCE_STORE.write_text(json.dumps(original, indent=2))

        if not _is_up(ha):
            subprocess.run(["docker", "start", container], capture_output=True, timeout=300)
            _wait_until_up(ha, container)
