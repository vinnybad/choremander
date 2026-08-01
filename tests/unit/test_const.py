"""Tests for `custom_components.choremander.const`.

`const.py` imports nothing from Home Assistant, so everything here is a plain
synchronous unit test. These constants are the option lists that back the
options-flow selectors, so a silent edit to one of them can make existing
stored data un-editable from the UI (see
`test_dev_seed_reward_icons_are_not_all_selectable`).
"""

from __future__ import annotations

import copy
from pathlib import Path
import re

import pytest

from custom_components.choremander.const import (
    AVATAR_OPTIONS,
    COMPLETION_SOUND_OPTIONS,
    DAYS_OF_WEEK,
    DEFAULT_COMPLETION_SOUND,
    DEFAULT_POINTS_ICON,
    DEFAULT_POINTS_NAME,
    DOMAIN,
    REWARD_ICON_OPTIONS,
    TIME_CATEGORIES,
    TIME_CATEGORY_ICONS,
)
from tests.unit.dev_seed import DEV_SEED

INTEGRATION_DIR = Path(__file__).resolve().parents[2] / "custom_components" / "choremander"


def _dev_seed() -> dict:
    """Return the committed dev-container seed payload.

    Sourced from `tests/unit/dev_seed.py` rather than `dev/config/.storage/`,
    which the running dev container rewrites underneath the suite.
    """
    return copy.deepcopy(DEV_SEED)


def test_time_category_icons_covers_every_time_category_in_order() -> None:
    """Every chore time category has an icon, and the two lists stay aligned."""
    assert list(TIME_CATEGORY_ICONS) == TIME_CATEGORIES, (
        "TIME_CATEGORY_ICONS must have exactly one entry per time category, in the "
        f"same order; got keys {list(TIME_CATEGORY_ICONS)} vs {TIME_CATEGORIES}"
    )
    assert all(icon.startswith("mdi:") for icon in TIME_CATEGORY_ICONS.values()), (
        f"every time-category icon must be an mdi icon: {TIME_CATEGORY_ICONS}"
    )


def test_every_model_default_is_selectable_in_its_option_list() -> None:
    """The dataclass defaults in models.py must be pickable in the options flow."""
    assert "anytime" in TIME_CATEGORIES, (
        "Chore.time_category defaults to 'anytime', which must be an offered category"
    )
    assert DEFAULT_COMPLETION_SOUND == "coin", (
        "Chore.completion_sound defaults to 'coin'; DEFAULT_COMPLETION_SOUND must agree"
    )
    assert DEFAULT_COMPLETION_SOUND in COMPLETION_SOUND_OPTIONS, (
        "the default completion sound must be selectable in the options flow"
    )
    assert "mdi:account-circle" in AVATAR_OPTIONS, (
        "Child.avatar defaults to 'mdi:account-circle', which must be a listed avatar"
    )
    assert "mdi:gift" in REWARD_ICON_OPTIONS, (
        "Reward.icon defaults to 'mdi:gift', which must be a listed reward icon"
    )


def test_dev_seed_reward_icons_are_not_all_selectable() -> None:
    """CURRENT BEHAVIOUR (defect #6, test plan 6.2) - three seeded rewards use
    icons that are absent from REWARD_ICON_OPTIONS.

    `vol.Optional("icon", default=<stored icon>)` is still validated against the
    option list, so those rewards cannot be edited *or deleted* from the options
    UI. This test pins today's option list so that adding the missing icons - or
    deliberately keeping them out - is a visible change.
    """
    seed_icons = {reward["icon"] for reward in _dev_seed()["rewards"]}
    unselectable = sorted(icon for icon in seed_icons if icon not in REWARD_ICON_OPTIONS)

    assert unselectable == [
        "mdi:book-open-variant",
        "mdi:elephant",
        "mdi:movie-open",
    ], (
        "the set of dev-seed reward icons missing from REWARD_ICON_OPTIONS changed; "
        f"got {unselectable}. Those rewards are un-editable and un-deletable from the UI"
    )
    assert {"mdi:television", "mdi:gamepad-variant"} <= set(REWARD_ICON_OPTIONS), (
        "the two seeded rewards that *are* editable rely on these icons being listed"
    )


@pytest.mark.parametrize(
    ("name", "options", "expected_length"),
    [
        ("AVATAR_OPTIONS", AVATAR_OPTIONS, 66),
        ("REWARD_ICON_OPTIONS", REWARD_ICON_OPTIONS, 14),
        ("COMPLETION_SOUND_OPTIONS", COMPLETION_SOUND_OPTIONS, 18),
        ("DAYS_OF_WEEK", DAYS_OF_WEEK, 7),
        ("TIME_CATEGORIES", TIME_CATEGORIES, 5),
    ],
)
def test_option_lists_have_no_duplicates_and_expected_length(
    name: str, options: list[str], expected_length: int
) -> None:
    """A duplicated option renders twice in the selector and breaks round trips."""
    duplicates = sorted({value for value in options if options.count(value) > 1})
    assert not duplicates, f"{name} contains duplicate entries: {duplicates}"
    assert len(options) == expected_length, (
        f"{name} changed size: expected {expected_length} entries, found {len(options)}"
    )


def test_days_of_week_is_lowercase_and_monday_first() -> None:
    """Index alignment matters: DAYS_OF_WEEK matches `datetime.weekday()`, not JS."""
    assert DAYS_OF_WEEK[0] == "monday", (
        "DAYS_OF_WEEK[0] must be 'monday' so indices line up with datetime.weekday(); "
        "JavaScript's Date.getDay() is Sunday-first and would be off by one"
    )
    assert DAYS_OF_WEEK[-1] == "sunday", "DAYS_OF_WEEK must end on sunday"
    assert all(day == day.lower() for day in DAYS_OF_WEEK), (
        f"stored due_days are compared case-sensitively: {DAYS_OF_WEEK}"
    )


def test_completion_sound_options_exact_contents() -> None:
    """The sound list is consumed positionally by the frontend preview module."""
    assert COMPLETION_SOUND_OPTIONS[0] == "none", (
        "'none' must stay first so it is the obvious opt-out in the dropdown"
    )
    assert COMPLETION_SOUND_OPTIONS[-1] == "fart_random", (
        "'fart_random' must stay last; it is the catch-all randomiser"
    )
    assert COMPLETION_SOUND_OPTIONS == [
        "none",
        "coin",
        "levelup",
        "fanfare",
        "chime",
        "powerup",
        "undo",
        "fart1",
        "fart2",
        "fart3",
        "fart4",
        "fart5",
        "fart6",
        "fart7",
        "fart8",
        "fart9",
        "fart10",
        "fart_random",
    ], "COMPLETION_SOUND_OPTIONS changed; www/ ships one asset per fart entry"


def test_dead_constants_are_exactly_the_documented_thirty() -> None:
    """Pin the constants nothing in the integration reads.

    Every `CONF_*` key, the sound event/service pair, `reset_daily` and the four
    `STATE_*` values are declared but never referenced. Asserting the exact set
    means adding a constant without wiring it up - or wiring one up - shows here.
    """
    declared = re.findall(
        r"^([A-Z_][A-Z0-9_]*)\s*:\s*Final",
        (INTEGRATION_DIR / "const.py").read_text(),
        re.MULTILINE,
    )
    other_sources = "\n".join(
        path.read_text()
        for path in sorted(INTEGRATION_DIR.rglob("*.py"))
        if path.name != "const.py"
    )
    unused = sorted(name for name in declared if not re.search(rf"\b{name}\b", other_sources))

    assert unused == [
        "ATTR_SOUND",
        "CONF_CHILDREN",
        "CONF_CHILD_AVATAR",
        "CONF_CHILD_ID",
        "CONF_CHILD_NAME",
        "CONF_CHILD_POINTS",
        "CONF_CHORES",
        "CONF_CHORE_ASSIGNED_TO",
        "CONF_CHORE_COMPLETION_SOUND",
        "CONF_CHORE_DESCRIPTION",
        "CONF_CHORE_DUE_DAYS",
        "CONF_CHORE_ID",
        "CONF_CHORE_NAME",
        "CONF_CHORE_POINTS",
        "CONF_CHORE_REQUIRES_APPROVAL",
        "CONF_POINTS_ICON",
        "CONF_POINTS_NAME",
        "CONF_REWARDS",
        "CONF_REWARD_COST",
        "CONF_REWARD_DESCRIPTION",
        "CONF_REWARD_ICON",
        "CONF_REWARD_ID",
        "CONF_REWARD_NAME",
        "EVENT_PREVIEW_SOUND",
        "SERVICE_PREVIEW_SOUND",
        "SERVICE_RESET_DAILY",
        "STATE_AWAITING_APPROVAL",
        "STATE_CLAIMED",
        "STATE_COMPLETED",
        "STATE_PENDING",
    ], (
        "the set of unreferenced constants changed. Either a dead constant gained a "
        f"user (good - drop it from this list) or a new one was added. Got: {unused}"
    )


def test_domain_matches_the_storage_and_service_namespace() -> None:
    """DOMAIN is baked into storage keys, service names and device identifiers."""
    assert DOMAIN == "choremander", (
        "changing DOMAIN orphans every stored .storage/choremander.storage.* file"
    )


def test_default_points_settings_match_the_storage_hardcoded_fallbacks() -> None:
    """`ChoremanderStorage` hardcodes its fallbacks instead of importing these."""
    from custom_components.choremander.storage import ChoremanderStorage

    # Bypass __init__ so no HomeAssistant instance is needed - the getters only
    # ever read `_data`.
    storage = ChoremanderStorage.__new__(ChoremanderStorage)
    storage._data = {}

    assert DEFAULT_POINTS_NAME == "Stars"
    assert DEFAULT_POINTS_ICON == "mdi:star"
    assert storage.get_points_name() == DEFAULT_POINTS_NAME, (
        "storage.get_points_name() hardcodes its default; it must agree with "
        "DEFAULT_POINTS_NAME or an empty store disagrees with the config flow"
    )
    assert storage.get_points_icon() == DEFAULT_POINTS_ICON, (
        "storage.get_points_icon() hardcodes its default; it must agree with DEFAULT_POINTS_ICON"
    )
