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

from custom_components.choremander.frontend import CARDS, URL_BASE
from tests.e2e.test_lovelace_resources import CARDS as E2E_CARDS
from tests.e2e.test_lovelace_resources import THIRD_PARTY_URL
from tests.e2e.test_lovelace_resources import URL_BASE as E2E_URL_BASE
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


# ---------------------------------------------------------------------------
# Lovelace resources (issue #2)
# ---------------------------------------------------------------------------

RESOURCES_FIXTURE = FIXTURE.parent / "lovelace_resources"
THIRD_PARTY_MODULE = (
    Path(__file__).resolve().parents[2] / "dev" / "config" / "www" / "third-party-card.js"
)


def test_the_environment_seeds_a_third_party_lovelace_resource() -> None:
    """The dev environment must own a resource Choremander did not create.

    Issue #2 - Choremander truncating `.storage/lovelace_resources` to its own
    cards - went unnoticed precisely because the dev environment had no other
    resources to lose. Without this fixture the end-to-end guard is vacuous.
    """
    assert RESOURCES_FIXTURE.exists(), (
        f"{RESOURCES_FIXTURE} is missing, so the dev environment starts with no "
        "third-party Lovelace resource and nothing would catch a regression of "
        "issue #2."
    )

    items = json.loads(RESOURCES_FIXTURE.read_text())["data"]["items"]
    urls = [item["url"] for item in items]

    assert THIRD_PARTY_URL in urls, (
        f"The resource fixture no longer seeds {THIRD_PARTY_URL}; it holds {urls}. "
        "tests/e2e/test_lovelace_resources.py asserts that exact URL survives startup."
    )
    assert not any(url.startswith("/choremander") for url in urls), (
        "The fixture must not pre-seed Choremander's own cards - the end-to-end "
        "test needs the integration to register them itself."
    )


def test_the_seeded_third_party_module_actually_exists() -> None:
    """A 404ing resource would break the dashboards' clean-console assertion."""
    assert THIRD_PARTY_MODULE.exists(), (
        f"{THIRD_PARTY_MODULE} is missing, but dev/fixtures/lovelace_resources "
        f"points Lovelace at {THIRD_PARTY_URL}. Home Assistant serves "
        "dev/config/www/ at /local/, so the file has to be there or every "
        "dashboard logs a console error."
    )


INTEGRATION = Path(__file__).resolve().parents[2] / "custom_components" / "choremander"
STRINGS = INTEGRATION / "strings.json"
TRANSLATIONS_EN = INTEGRATION / "translations" / "en.json"


def test_the_english_translations_match_strings_json() -> None:
    """`strings.json` is the source; `translations/en.json` is a hand copy.

    Home Assistant serves the translations file to the frontend and never looks
    at `strings.json` at runtime, so an edit to one and not the other silently
    ships the old wording - or, for a new key, a raw `component.choremander...`
    placeholder in the UI.
    """
    strings = json.loads(STRINGS.read_text())
    english = json.loads(TRANSLATIONS_EN.read_text())

    assert english == strings, (
        "custom_components/choremander/translations/en.json has drifted from "
        "strings.json. Copy the changed block across so the UI shows what the "
        "source says."
    )


def test_the_repair_issue_is_translated() -> None:
    """The recovery repair is useless if its text renders as a placeholder."""
    issues = json.loads(STRINGS.read_text()).get("issues", {})
    issue = issues.get("deleted_lovelace_resources")

    assert issue is not None, (
        "resource_repair.py raises `deleted_lovelace_resources`; without a "
        "strings entry Home Assistant shows the raw translation key"
    )
    confirm = issue["fix_flow"]["step"]["confirm"]
    for placeholder in ("{count}", "{resources}"):
        assert placeholder in confirm["description"], (
            f"the confirmation step is passed {placeholder} but never shows it"
        )
    assert "nothing_to_restore" in issue["fix_flow"]["abort"], (
        "the flow aborts with `nothing_to_restore`; that reason needs text"
    )


def test_the_e2e_card_list_matches_the_integrations() -> None:
    """`tests/e2e/` cannot import Home Assistant, so it spells `CARDS` out."""
    assert list(E2E_CARDS) == list(CARDS), (
        "tests/e2e/test_lovelace_resources.py has drifted from "
        "custom_components/choremander/frontend.py. Update the literal copy there "
        f"to {list(CARDS)}."
    )
    assert E2E_URL_BASE == URL_BASE, (
        f"The end-to-end copy of URL_BASE is {E2E_URL_BASE}, not {URL_BASE}."
    )
