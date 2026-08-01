"""A small synchronous Home Assistant REST client for end-to-end tests.

Deliberately dependency-free and synchronous: these tests drive a real
container over HTTP, so there is nothing to gain from async, and keeping the
client free of the Home Assistant package avoids any chance of the in-process
unit harness leaking into the container-backed suite.
"""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[2]
TOKEN_FILE = REPO_ROOT / "dev" / ".token"


class HomeAssistantError(RuntimeError):
    """Raised when Home Assistant rejects a request."""


class HAClient:
    """Drives a running Home Assistant instance over its REST API."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    # -- construction --------------------------------------------------

    @classmethod
    def from_token_file(cls) -> HAClient:
        """Build a client from the credentials written by dev/bootstrap.py."""
        if not TOKEN_FILE.exists():
            raise HomeAssistantError(
                f"{TOKEN_FILE} is missing. Run `./dev/start.sh` to bring the "
                "environment up before running end-to-end tests."
            )
        data = json.loads(TOKEN_FILE.read_text())
        return cls(data["hass_url"], data["access_token"])

    # -- plumbing ------------------------------------------------------

    def _request(self, path: str, *, method: str = "GET", payload: Any = None) -> Any:
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.token}"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read().decode()
        except urllib.error.HTTPError as err:
            raise HomeAssistantError(
                f"{method} {path} failed with HTTP {err.code}: {err.read().decode()[:500]}"
            ) from err
        except urllib.error.URLError as err:
            raise HomeAssistantError(
                f"Could not reach Home Assistant at {url}: {err}. Is the container running?"
            ) from err

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def is_up(self) -> bool:
        try:
            self._request("/api/")
        except HomeAssistantError:
            return False
        return True

    # -- state ---------------------------------------------------------

    def state(self, entity_id: str) -> dict[str, Any]:
        """Full state object for one entity."""
        return self._request(f"/api/states/{entity_id}")

    def states(self) -> list[dict[str, Any]]:
        return self._request("/api/states")

    def attributes(self, entity_id: str) -> dict[str, Any]:
        return self.state(entity_id)["attributes"]

    def call_service(self, domain: str, service: str, **data: Any) -> Any:
        """Invoke a service and return the resulting changed states."""
        return self._request(f"/api/services/{domain}/{service}", method="POST", payload=data)

    def wait_for(
        self,
        entity_id: str,
        predicate: Any,
        *,
        timeout: float = 15.0,
        description: str = "condition",
    ) -> dict[str, Any]:
        """Poll an entity until `predicate(state)` holds, or fail with context.

        Service calls return before the coordinator has necessarily pushed new
        state to every entity, so assertions need a settle window rather than an
        immediate read.
        """
        deadline = time.monotonic() + timeout
        last: dict[str, Any] | None = None

        while time.monotonic() < deadline:
            last = self.state(entity_id)
            if predicate(last):
                return last
            time.sleep(0.25)

        observed = last.get("state") if last else "<never read>"
        raise AssertionError(
            f"Timed out after {timeout:.0f}s waiting for {description} on {entity_id}. "
            f"Last observed state: {observed!r}"
        )

    # -- Choremander conveniences --------------------------------------

    OVERVIEW = "sensor.choremander_overview"

    def overview(self) -> dict[str, Any]:
        return self.attributes(self.OVERVIEW)

    def child(self, name: str) -> dict[str, Any]:
        """Look up a child in the overview sensor by display name."""
        for entry in self.overview()["children"]:
            if entry["name"] == name:
                return entry
        raise AssertionError(f"No child named {name!r} in {self.OVERVIEW}")

    def chore(self, name: str) -> dict[str, Any]:
        for entry in self.overview()["chores"]:
            if entry["name"] == name:
                return entry
        raise AssertionError(f"No chore named {name!r} in {self.OVERVIEW}")

    def reward(self, name: str) -> dict[str, Any]:
        for entry in self.overview()["rewards"]:
            if entry["name"] == name:
                return entry
        raise AssertionError(f"No reward named {name!r} in {self.OVERVIEW}")

    def points(self, child_name: str) -> int:
        return int(self.child(child_name)["points"])

    def set_points(self, child_id: str, target: int, current: int) -> None:
        """Force a child's balance to `target` using the add/remove services."""
        delta = target - current
        if delta > 0:
            self.call_service(
                "choremander", "add_points", child_id=child_id, points=delta, reason="test setup"
            )
        elif delta < 0:
            self.call_service(
                "choremander",
                "remove_points",
                child_id=child_id,
                points=-delta,
                reason="test teardown",
            )
