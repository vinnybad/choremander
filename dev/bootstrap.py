#!/usr/bin/env python3
"""Bring a Home Assistant container up to a known, logged-in state.

The committed dev config marks onboarding as complete, but the auth store is
generated at runtime and is deliberately not committed (it holds secrets). A
fresh clone therefore boots an instance that believes it is onboarded yet has no
users, which locks you out of the UI entirely.

This script closes that gap without committing any secret: when the auth store
is missing it rewinds the onboarding marker, lets Home Assistant run its normal
onboarding flow over the REST API, and captures the resulting tokens to
`dev/.token` (gitignored). Running it again with a valid token is a no-op.

Standard library only, so it works before any virtualenv exists.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

DEV_DIR = Path(__file__).resolve().parent
CONFIG_DIR = DEV_DIR / "config"
STORAGE_DIR = CONFIG_DIR / ".storage"
TOKEN_FILE = DEV_DIR / ".token"

AUTH_FILES = (STORAGE_DIR / "auth", STORAGE_DIR / "auth_provider.homeassistant")

USERNAME = "dev"
PASSWORD = "dev"
DISPLAY_NAME = "Dev"

# Home Assistant validates that client_id is a URL it can reach back to, and the
# frontend uses its own origin. Matching it keeps tokens valid for browser use.
CLIENT_ID = "http://localhost:8123/"

# Onboarding steps beyond `users`, in order. Every one of these must complete or
# the frontend keeps showing the setup wizard instead of the dashboards - which
# breaks the browser tests even though the REST API works fine.
# `integration` takes a different payload and is handled separately.
REMAINING_STEPS = ("core_config", "analytics")


class BootstrapError(RuntimeError):
    """Raised when the environment cannot be brought to a usable state."""


def log(message: str) -> None:
    print(f"[bootstrap] {message}", flush=True)


def request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    form: dict[str, str] | None = None,
    token: str | None = None,
    timeout: float = 10.0,
) -> tuple[int, Any]:
    """Perform an HTTP request, returning (status, decoded body).

    Never raises for HTTP error statuses - callers decide what is fatal, since
    several onboarding steps legitimately return 403 when already complete.
    """
    headers = {}
    body: bytes | None = None

    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"

    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode()
            status = response.status
    except urllib.error.HTTPError as err:
        raw = err.read().decode()
        status = err.code

    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def auth_store_exists() -> bool:
    return all(path.exists() for path in AUTH_FILES)


def rewind_onboarding() -> None:
    """Reset the onboarding marker so Home Assistant will accept a new user.

    Only safe to call while the container is stopped: Home Assistant holds the
    storage in memory and would overwrite the file on shutdown.
    """
    onboarding = STORAGE_DIR / "onboarding"
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    onboarding.write_text(
        json.dumps(
            {"version": 4, "minor_version": 1, "key": "onboarding", "data": {"done": []}},
            indent=2,
        )
    )
    log("rewound onboarding marker (auth store absent)")


def wait_for_http(base_url: str, timeout: float) -> None:
    """Block until Home Assistant answers, or raise once the deadline passes."""
    deadline = time.monotonic() + timeout
    last_error = "no response"
    attempt = 0

    while time.monotonic() < deadline:
        attempt += 1
        try:
            status, _ = request(f"{base_url}/manifest.json", timeout=5.0)
            if status < 500:
                log(f"Home Assistant is answering (after {attempt} probes)")
                return
            last_error = f"HTTP {status}"
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
            last_error = str(err)
        time.sleep(2)

    raise BootstrapError(
        f"Home Assistant did not become reachable at {base_url} within {timeout:.0f}s "
        f"(last error: {last_error}). Check `docker compose -f docker-compose.dev.yml logs`."
    )


def token_is_valid(base_url: str) -> bool:
    """Check whether the cached access token still authenticates."""
    if not TOKEN_FILE.exists():
        return False
    try:
        cached = json.loads(TOKEN_FILE.read_text())
    except json.JSONDecodeError:
        return False

    access_token = cached.get("access_token")
    if not access_token:
        return False

    status, _ = request(f"{base_url}/api/", token=access_token)
    if status == 200:
        return True

    # An expired access token is recoverable if we still hold a refresh token.
    refresh_token = cached.get("refresh_token")
    if not refresh_token:
        return False

    status, body = request(
        f"{base_url}/auth/token",
        method="POST",
        form={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        },
    )
    if status != 200 or not isinstance(body, dict) or "access_token" not in body:
        return False

    cached["access_token"] = body["access_token"]
    cached["expires_in"] = body.get("expires_in", 1800)
    _write_token(cached)
    log("refreshed cached access token")
    return True


def _write_token(data: dict[str, Any]) -> None:
    TOKEN_FILE.write_text(json.dumps(data, indent=2) + "\n")
    TOKEN_FILE.chmod(0o600)


def onboard(base_url: str) -> dict[str, Any]:
    """Run the onboarding flow and return the captured token payload."""
    status, body = request(
        f"{base_url}/api/onboarding/users",
        method="POST",
        payload={
            "client_id": CLIENT_ID,
            "name": DISPLAY_NAME,
            "username": USERNAME,
            "password": PASSWORD,
            "language": "en",
        },
    )

    if status != 200 or not isinstance(body, dict) or "auth_code" not in body:
        raise BootstrapError(
            f"Onboarding the '{USERNAME}' user failed (HTTP {status}): {body}. "
            "If this says onboarding is already done, run `./dev/reset.sh` and retry."
        )

    log(f"created user '{USERNAME}'")
    tokens = exchange_auth_code(base_url, body["auth_code"])

    for step in REMAINING_STEPS:
        status, _ = request(
            f"{base_url}/api/onboarding/{step}",
            method="POST",
            payload={},
            token=tokens["access_token"],
        )
        # 403 means the step was already completed - not a failure.
        if status not in (200, 403):
            log(f"warning: onboarding step '{step}' returned HTTP {status}; continuing")

    # The final step takes the OAuth parameters rather than an empty body. Until
    # it succeeds the frontend serves the setup wizard on every route.
    status, _ = request(
        f"{base_url}/api/onboarding/integration",
        method="POST",
        payload={"client_id": CLIENT_ID, "redirect_uri": f"{CLIENT_ID}?auth_callback=1"},
        token=tokens["access_token"],
    )
    if status not in (200, 403):
        raise BootstrapError(
            f"Completing the 'integration' onboarding step failed (HTTP {status}). "
            "The frontend will keep showing the onboarding wizard until this succeeds."
        )

    log("onboarding complete")
    return tokens


def exchange_auth_code(base_url: str, auth_code: str) -> dict[str, Any]:
    """Trade a one-time onboarding auth code for access and refresh tokens."""
    status, body = request(
        f"{base_url}/auth/token",
        method="POST",
        form={
            "grant_type": "authorization_code",
            "code": auth_code,
            "client_id": CLIENT_ID,
        },
    )

    if status != 200 or not isinstance(body, dict) or "access_token" not in body:
        raise BootstrapError(f"Exchanging the auth code for tokens failed (HTTP {status}): {body}")

    payload = {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token"),
        "token_type": body.get("token_type", "Bearer"),
        "expires_in": body.get("expires_in", 1800),
        "client_id": CLIENT_ID,
        "hass_url": base_url,
        "username": USERNAME,
        "password": PASSWORD,
    }
    _write_token(payload)
    log(f"wrote credentials to {TOKEN_FILE.relative_to(DEV_DIR.parent)}")
    return payload


def wait_for_integration(base_url: str, token: str, timeout: float) -> None:
    """Block until Choremander has produced live entities, proving it loaded.

    Entity ids are derived from user-chosen child and chore names, so they
    cannot be matched by domain prefix. Instead this looks for entities that are
    actually available - stale registry entries from earlier seed data linger as
    `unavailable` and must not be mistaken for a healthy load.
    """
    deadline = time.monotonic() + timeout
    seen_any = False

    while time.monotonic() < deadline:
        status, body = request(f"{base_url}/api/states", token=token)
        if status == 200 and isinstance(body, list):
            seen_any = True
            # The overview sensor is created unconditionally, so it is the one
            # reliable signal that the integration finished setting up.
            overview = next(
                (s for s in body if s.get("entity_id") == "sensor.choremander_overview"), None
            )
            if overview and overview.get("state") not in ("unavailable", "unknown"):
                log(f"Choremander loaded (sensor.choremander_overview = {overview['state']})")
                return
        time.sleep(2)

    detail = (
        "the API responded but sensor.choremander_overview never became available"
        if seen_any
        else "the states API never responded"
    )
    raise BootstrapError(
        f"Choremander did not come up within {timeout:.0f}s - {detail}. "
        "Run `./dev/logs.sh` to see why the integration failed to load."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8123")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--pre-start",
        action="store_true",
        help="Run only the offline fixups that must happen before the container starts.",
    )
    args = parser.parse_args()

    if args.pre_start:
        if not auth_store_exists():
            rewind_onboarding()
        else:
            log("auth store present; leaving onboarding marker alone")
        return 0

    try:
        wait_for_http(args.base_url, args.timeout)

        if token_is_valid(args.base_url):
            log("existing credentials still valid; skipping onboarding")
            tokens = json.loads(TOKEN_FILE.read_text())
        else:
            tokens = onboard(args.base_url)

        wait_for_integration(args.base_url, tokens["access_token"], timeout=90.0)
    except BootstrapError as err:
        print(f"[bootstrap] FAILED: {err}", file=sys.stderr)
        return 1

    log("environment ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
