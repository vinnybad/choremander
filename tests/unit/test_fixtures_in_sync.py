"""Guard that the frozen unit-test seed matches the environment fixture.

`tests/unit/dev_seed.py` is a frozen copy of
`dev/fixtures/choremander.storage.choremander_dev_entry`. The unit suite reads
the frozen copy so it stays hermetic - the live `dev/config/.storage/` file is
mounted read-write into the running container and is rewritten constantly - but
that only works if the two stay in step.

Without this test, editing the fixture would silently leave the unit suite
asserting against data no other layer uses.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.unit.dev_seed import DEV_SEED

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "dev"
    / "fixtures"
    / "choremander.storage.choremander_dev_entry"
)


def test_frozen_seed_matches_the_environment_fixture() -> None:
    """The unit suite and the containers must start from identical data."""
    assert FIXTURE.exists(), (
        f"{FIXTURE} is missing. dev/seed.sh restores the environment from that "
        "file, so the end-to-end suites cannot reach a known state without it."
    )

    on_disk = json.loads(FIXTURE.read_text())["data"]

    assert on_disk == DEV_SEED, (
        "tests/unit/dev_seed.py has drifted from "
        "dev/fixtures/choremander.storage.choremander_dev_entry. The unit suite "
        "and the end-to-end suites would be starting from different data. "
        "Regenerate the frozen copy from the fixture, then update any test that "
        "asserts the changed values."
    )


def test_fixture_starts_from_a_clean_slate() -> None:
    """The fixture must carry no completions, claims, or accrued history.

    The end-to-end suites reset to this file between runs. If it ever captures a
    mid-run state, every downstream assertion about starting balances shifts.
    """
    data = json.loads(FIXTURE.read_text())["data"]

    assert data["completions"] == [], (
        "The fixture contains chore completions, so it was captured mid-run. "
        "Rebuild it from a freshly seeded environment."
    )
    assert data["reward_claims"] == [], (
        "The fixture contains reward claims, so it was captured mid-run. "
        "Rebuild it from a freshly seeded environment."
    )

    balances = {c["name"]: c["points"] for c in data["children"]}
    assert balances == {"Krishna": 25, "Radha": 15}, (
        f"The fixture's starting balances are {balances}, not the documented "
        "Krishna 25 / Radha 15. Tests across all three layers assert these."
    )
