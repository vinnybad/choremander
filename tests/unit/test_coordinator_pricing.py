"""Dynamic reward pricing - `calculate_dynamic_reward_costs` and friends.

This is the integration's core money algorithm: it converts a child's expected
daily chore earnings into the number of points a reward should cost. Everything
here is pure arithmetic over `storage.get_children()` / `storage.get_chores()`,
so the tests drive a real `ChoremanderCoordinator` with its `_data` dict
assigned directly - no config entry, no mocks, no I/O.

Every literal in this file comes from the worked examples in the test plan
(section 4), which were produced by executing the real algorithm against the
unmodified dev seed.
"""

from __future__ import annotations

from copy import deepcopy
import logging
from typing import Any

from homeassistant.core import HomeAssistant
import pytest

from custom_components.choremander.coordinator import ChoremanderCoordinator
from custom_components.choremander.models import Child, Chore, Reward
from custom_components.choremander.schedule import DAY_NAMES

COORDINATOR_LOGGER = "custom_components.choremander.coordinator"

# --------------------------------------------------------------------------
# Fixtures and builders
# --------------------------------------------------------------------------
#
# `dev_seed` (a deep copy of the committed dev-container seed) comes from
# tests/unit/conftest.py.


@pytest.fixture
def make_pricing_coordinator(hass: HomeAssistant):
    """Build a real coordinator over hand-written storage data.

    `calculate_dynamic_reward_costs` and `get_child_daily_points` are
    synchronous and only read `storage.get_children()` / `storage.get_chores()`,
    so no `async_load`, no `async_refresh`, and no config entry are needed.
    """

    def _make(
        children: list[Child] | None = None,
        chores: list[Chore] | None = None,
        rewards: list[Reward] | None = None,
    ) -> ChoremanderCoordinator:
        coordinator = ChoremanderCoordinator(hass, "pricing_entry")
        coordinator.storage._data = {
            "children": [c.to_dict() for c in children or []],
            "chores": [c.to_dict() for c in chores or []],
            "rewards": [r.to_dict() for r in rewards or []],
            "completions": [],
            "reward_claims": [],
            "points_name": "Stars",
            "points_icon": "mdi:star",
        }
        return coordinator

    return _make


@pytest.fixture
def seeded_coordinator(hass: HomeAssistant, dev_seed: dict[str, Any]) -> ChoremanderCoordinator:
    """A coordinator holding a private deep copy of the dev seed."""
    coordinator = ChoremanderCoordinator(hass, "seed_entry")
    coordinator.storage._data = deepcopy(dev_seed)
    return coordinator


def child(child_id: str, name: str | None = None, points: int = 0) -> Child:
    """A child with a pinned id, so assertions can name it."""
    return Child(name=name or child_id, points=points, id=child_id)


def chore(
    points: int,
    *,
    assigned_to: list[str] | None = None,
    percentage: int = 100,
    chore_id: str = "chore_x",
    name: str = "Chore",
    due_days: list[str] | None = None,
    rotation: str = "none",
    rotation_order: list[str] | None = None,
    day_overrides: dict[str, list[str]] | None = None,
) -> Chore:
    """A chore carrying the fields the pricing math reads.

    Every scheduling argument defaults to the value a chore stored before
    schedules existed loads with, so the builder keeps producing the
    always-on chore the tests above this section were written against.
    """
    return Chore(
        name=name,
        points=points,
        assigned_to=assigned_to or [],
        completion_percentage_per_month=percentage,
        due_days=due_days or [],
        rotation=rotation,
        rotation_order=rotation_order or [],
        day_overrides=day_overrides or {},
        id=chore_id,
    )


def reward(
    *,
    cost: int = 50,
    assigned_to: list[str] | None = None,
    is_jackpot: bool = False,
    override_point_value: bool = False,
    days_to_goal: int = 30,
    name: str = "Reward",
) -> Reward:
    """A reward; note `Reward(...)` defaults to dynamic pricing."""
    return Reward(
        name=name,
        cost=cost,
        assigned_to=assigned_to or [],
        is_jackpot=is_jackpot,
        override_point_value=override_point_value,
        days_to_goal=days_to_goal,
        id="reward_x",
    )


def warnings_from_coordinator(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Only the WARNING records emitted by the coordinator module."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == COORDINATOR_LOGGER and record.levelno == logging.WARNING
    ]


# --------------------------------------------------------------------------
# The dev seed - the numbers a real family actually sees
# --------------------------------------------------------------------------


def test_dev_seed_daily_points_sum_each_childs_assigned_chores(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """Krishna earns 15 pts/day, Radha 13, from the seeded chore assignments."""
    daily = seeded_coordinator.get_child_daily_points(
        seeded_coordinator.get_reward("reward_screen")
    )

    assert daily == {"child_krishna": 15.0, "child_radha": 13.0}, (
        "Krishna's shared+own chores (2+1+3+5+1+3) should total 15 pts/day and "
        f"Radha's (2+1+2+4+1+3) 13 pts/day, got {daily}"
    )


def test_screen_time_costs_each_child_seven_days_of_their_own_earnings(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """A non-jackpot reward prices per child: their daily rate x days_to_goal."""
    costs = seeded_coordinator.calculate_dynamic_reward_costs(
        seeded_coordinator.get_reward("reward_screen")
    )

    assert costs == {"child_krishna": 105, "child_radha": 91}, (
        "30 Min Screen Time has days_to_goal=7, so Krishna should pay 15x7=105 "
        f"and Radha 13x7=91 - the static cost of 10 is ignored. Got {costs}"
    )


def test_movie_night_jackpot_charges_every_child_the_pooled_family_total(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """A jackpot pools all children's daily points before multiplying."""
    costs = seeded_coordinator.calculate_dynamic_reward_costs(
        seeded_coordinator.get_reward("reward_movie_night")
    )

    assert costs == {"child_krishna": 392, "child_radha": 392}, (
        "Family Movie Night is a jackpot with days_to_goal=14, so both children "
        f"should see (15+13)x14=392, got {costs}"
    )


def test_zoo_trip_jackpot_uses_thirty_days_of_the_pooled_total(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """Same pooling, longer horizon: 28 pts/day x 30 days."""
    costs = seeded_coordinator.calculate_dynamic_reward_costs(
        seeded_coordinator.get_reward("reward_zoo_trip")
    )

    assert costs == {"child_krishna": 840, "child_radha": 840}, (
        "Trip to the Zoo is a jackpot with days_to_goal=30, so both children "
        f"should see (15+13)x30=840, got {costs}"
    )


def test_override_point_value_short_circuits_to_the_manually_entered_cost(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """`override_point_value` skips the chore math entirely."""
    game = seeded_coordinator.get_reward("reward_krishna_game")
    book = seeded_coordinator.get_reward("reward_radha_book")

    assert seeded_coordinator.calculate_dynamic_reward_costs(game) == {"child_krishna": 50}, (
        "New Video Game has override_point_value=True and is assigned only to "
        "Krishna, so it must cost exactly its manual 50 points, for him alone"
    )
    assert seeded_coordinator.calculate_dynamic_reward_costs(book) == {"child_radha": 30}, (
        "New Book is Radha's override reward and must cost its manual 30 points"
    )


def test_get_child_daily_points_ignores_override_and_jackpot_flags(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """The daily-rate helper is flag-blind; only the cost calculator branches."""
    game = seeded_coordinator.get_reward("reward_krishna_game")
    zoo = seeded_coordinator.get_reward("reward_zoo_trip")

    assert seeded_coordinator.get_child_daily_points(game) == {"child_krishna": 15.0}, (
        "get_child_daily_points must report Krishna's real 15.0 pts/day even for "
        "an override reward whose price is pinned at 50"
    )
    assert seeded_coordinator.get_child_daily_points(zoo) == {
        "child_krishna": 15.0,
        "child_radha": 13.0,
    }, (
        "get_child_daily_points must report per-child rates for a jackpot too - "
        "it never pools, unlike calculate_dynamic_reward_costs"
    )


# --------------------------------------------------------------------------
# Rounding and float accumulation
# --------------------------------------------------------------------------


def test_prices_use_bankers_rounding_so_half_points_can_round_down(
    make_pricing_coordinator,
) -> None:
    """Python's `round` is half-to-even: 2.5 -> 2 but 7.5 -> 8."""
    coordinator = make_pricing_coordinator(
        children=[child("A")],
        chores=[chore(5, percentage=50)],
    )

    one_day = coordinator.calculate_dynamic_reward_costs(reward(days_to_goal=1))
    three_days = coordinator.calculate_dynamic_reward_costs(reward(days_to_goal=3))

    assert one_day == {"A": 2}, (
        "2.5 points must round half-to-even down to 2, not up to 3 - the "
        f"frontend's Math.round would disagree here. Got {one_day}"
    )
    assert three_days == {"A": 8}, f"7.5 points must round half-to-even up to 8, got {three_days}"


def test_a_price_never_drops_below_one_point(make_pricing_coordinator) -> None:
    """`max(1, round(...))` floors near-zero prices at a single point."""
    coordinator = make_pricing_coordinator(
        children=[child("A")],
        chores=[chore(1, percentage=1)],
    )

    costs = coordinator.calculate_dynamic_reward_costs(reward(days_to_goal=30))

    assert costs == {"A": 1}, (
        "1 point at 1% for 30 days is 0.3, which rounds to 0; the max(1, ...) "
        f"floor must lift it to 1 so the reward is not free. Got {costs}"
    )


def test_float_accumulation_artifacts_nudge_prices_off_the_exact_half(
    make_pricing_coordinator,
) -> None:
    """Percentages are divided before summing, so exact halves rarely survive."""
    single = make_pricing_coordinator(
        children=[child("A")],
        chores=[chore(3, percentage=10)],
    )
    combined = make_pricing_coordinator(
        children=[child("A")],
        chores=[
            chore(7, percentage=33, chore_id="chore_a"),
            chore(2, percentage=66, chore_id="chore_b"),
        ],
    )

    assert single.calculate_dynamic_reward_costs(reward(days_to_goal=5)) == {"A": 2}, (
        "3 points at 10% is 0.30000000000000004, so five days is just over 1.5 "
        "and rounds up to 2 - banker's rounding would have given 2 anyway, but "
        "for the opposite reason"
    )
    assert combined.calculate_dynamic_reward_costs(reward(days_to_goal=30)) == {"A": 109}, (
        "7@33% + 2@66% is 3.63/day; 30 days is 108.89999999999999 and must round to 109"
    )


def test_completion_percentage_above_one_hundred_scales_without_an_upper_clamp(
    make_pricing_coordinator,
) -> None:
    """A chore done 2.5x a day simply earns 2.5x the points."""
    coordinator = make_pricing_coordinator(
        children=[child("A")],
        chores=[chore(10, percentage=250)],
    )

    costs = coordinator.calculate_dynamic_reward_costs(reward(cost=7, days_to_goal=4))

    assert costs == {"A": 100}, (
        "10 points at 250% is 25 pts/day; four days must price at 100 with no "
        f"ceiling applied. Got {costs}"
    )


def test_completion_percentage_of_zero_falls_back_to_the_manual_cost(
    make_pricing_coordinator,
) -> None:
    """A chore expected zero times a day earns nothing, so pricing gives up."""
    coordinator = make_pricing_coordinator(
        children=[child("A")],
        chores=[chore(50, percentage=0)],
    )

    costs = coordinator.calculate_dynamic_reward_costs(reward(cost=42, days_to_goal=10))

    assert costs == {"A": 42}, (
        "A daily rate of 0.0 is not > 0, so the child must fall back to the "
        f"reward's manually entered cost of 42, not to 1 or 0. Got {costs}"
    )


# --------------------------------------------------------------------------
# Branch-order edge cases
# --------------------------------------------------------------------------


def test_days_to_goal_of_zero_or_negative_is_coerced_to_thirty_days(
    make_pricing_coordinator,
) -> None:
    """Non-positive horizons silently become the 30-day default."""
    coordinator = make_pricing_coordinator(
        children=[child("A")],
        chores=[chore(2)],
    )

    zero = coordinator.calculate_dynamic_reward_costs(reward(days_to_goal=0))
    negative = coordinator.calculate_dynamic_reward_costs(reward(days_to_goal=-5))
    one = coordinator.calculate_dynamic_reward_costs(reward(days_to_goal=1))

    assert zero == {"A": 60}, f"days_to_goal=0 must be treated as 30 days (2 x 30 = 60), got {zero}"
    assert negative == {"A": 60}, (
        f"days_to_goal=-5 must be treated as 30 days (2 x 30 = 60), got {negative}"
    )
    assert one == {"A": 2}, (
        "days_to_goal=1 is positive and must be honoured as one day, proving "
        f"the coercion only fires for non-positive values. Got {one}"
    )


def test_a_reward_nobody_is_assigned_to_is_priced_for_nobody(
    make_pricing_coordinator,
) -> None:
    """The empty-assignment guard runs before the override branch."""
    coordinator = make_pricing_coordinator(
        children=[child("A"), child("B")],
        chores=[chore(4)],
    )

    ghost = coordinator.calculate_dynamic_reward_costs(reward(assigned_to=["ghost_child"], cost=77))
    ghost_override = coordinator.calculate_dynamic_reward_costs(
        reward(assigned_to=["ghost_child"], cost=77, override_point_value=True)
    )

    assert ghost == {}, f"A reward assigned only to a non-existent child prices nobody, got {ghost}"
    assert ghost_override == {}, (
        "The 'no assigned children' early return precedes the override branch, "
        f"so even a manually priced reward returns nothing. Got {ghost_override}"
    )


def test_a_household_with_no_children_prices_nothing(make_pricing_coordinator) -> None:
    """Zero children means an empty result, whatever the reward looks like."""
    coordinator = make_pricing_coordinator(children=[], chores=[chore(4)])

    assert coordinator.calculate_dynamic_reward_costs(reward(cost=99)) == {}, (
        "With no children configured there is nobody to price a reward for"
    )
    assert coordinator.get_child_daily_points(reward(cost=99)) == {}, (
        "With no children configured there are no daily point rates to report"
    )


def test_with_no_chores_every_child_pays_the_rewards_static_cost(
    make_pricing_coordinator,
) -> None:
    """No chores means no earnings data, so the manual cost is used verbatim."""
    coordinator = make_pricing_coordinator(children=[child("A"), child("B")], chores=[])

    plain = coordinator.calculate_dynamic_reward_costs(reward(cost=60))
    jackpot = coordinator.calculate_dynamic_reward_costs(reward(cost=60, is_jackpot=True))

    assert plain == {"A": 60, "B": 60}, (
        f"Without chores each child pays the reward's own cost of 60, got {plain}"
    )
    assert jackpot == {"A": 60, "B": 60}, (
        "The no-chores fallback runs before the jackpot branch, so a jackpot is "
        f"NOT pooled to 120 here - each child still pays 60. Got {jackpot}"
    )


def test_get_child_daily_points_is_empty_when_there_are_no_chores(
    make_pricing_coordinator,
) -> None:
    """Its own guard returns `{}` rather than a dict of zeroes."""
    coordinator = make_pricing_coordinator(children=[child("A"), child("B")], chores=[])

    assert coordinator.get_child_daily_points(reward(cost=60)) == {}, (
        "With no chores, get_child_daily_points returns an empty dict - it does "
        "not report {'A': 0.0, 'B': 0.0}"
    )


def test_a_chore_assigned_to_nobody_counts_towards_every_childs_rate(
    make_pricing_coordinator,
) -> None:
    """An empty `assigned_to` means 'anyone', not 'no one'."""
    coordinator = make_pricing_coordinator(
        children=[child("A"), child("B")],
        chores=[
            chore(3, assigned_to=[], chore_id="shared"),
            chore(2, assigned_to=["A"], chore_id="a_only"),
        ],
    )

    costs = coordinator.calculate_dynamic_reward_costs(reward(days_to_goal=2))

    assert costs == {"A": 10, "B": 6}, (
        "The unassigned 3-point chore counts for both children, while the "
        "2-point chore counts only for A, so over 2 days A pays (3+2)x2=10 and "
        f"B pays 3x2=6. Got {costs}"
    )


def test_a_child_who_earns_nothing_falls_back_to_the_manual_cost_only_when_not_a_jackpot(
    make_pricing_coordinator,
) -> None:
    """The per-child fallback exists only on the non-jackpot branch."""
    coordinator = make_pricing_coordinator(
        children=[child("A"), child("B")],
        chores=[chore(10, assigned_to=["A"])],
    )

    plain = coordinator.calculate_dynamic_reward_costs(reward(cost=999, days_to_goal=10))
    jackpot = coordinator.calculate_dynamic_reward_costs(
        reward(cost=999, days_to_goal=10, is_jackpot=True)
    )

    assert plain == {"A": 100, "B": 999}, (
        "B has no chores assigned, so a non-jackpot reward charges B the manual "
        f"999 while A pays 10 pts/day x 10 days = 100. Got {plain}"
    )
    assert jackpot == {"A": 100, "B": 100}, (
        "A jackpot pools the household's 10 pts/day, so B never reaches the 999 "
        f"fallback and pays the same 100 as A. Got {jackpot}"
    )


# --------------------------------------------------------------------------
# Scheduling - how often a chore actually comes round
# --------------------------------------------------------------------------
#
# `_daily_expected_points` multiplies by `scheduled_days_per_week / 7`,
# measured by resolving `PRICING_WINDOW_DAYS` (28) days ahead and counting the
# days each child is named. Twenty-eight days contains exactly four of every
# weekday whatever today is, so a chore due on one weekday is 4/28 of a daily
# one and none of these tests depend on the date they run on.


def test_an_unscheduled_chore_prices_exactly_as_it_did_before_scheduling(
    make_pricing_coordinator,
) -> None:
    """The property every installed household depends on.

    A chore with no `due_days` falls on all 28 days of the window, so the
    frequency factor is exactly 1.0 and the arithmetic reduces to the
    `points x (completion_pct / 100)` this function computed before schedules
    existed. Asserted explicitly rather than left implied by the tests above,
    because it is the whole reason this feature could ship without a migration.
    """
    coordinator = make_pricing_coordinator(
        children=[child("A")],
        chores=[chore(10, percentage=100)],
    )

    assert coordinator.get_child_daily_points(reward()) == {"A": 10.0}, (
        "10 points at 100% with no schedule must still be 10.0 pts/day - a "
        "frequency factor of anything but 1.0 here would reprice every reward "
        "in every existing install on upgrade"
    )
    assert coordinator.calculate_dynamic_reward_costs(reward(days_to_goal=10)) == {"A": 100}, (
        "and 10 days of it must still cost 100"
    )


def test_listing_all_seven_due_days_prices_the_same_as_listing_none(
    make_pricing_coordinator,
) -> None:
    """The two ways of saying "every day" must not disagree.

    An empty `due_days` means every day for back-compat; spelling the seven out
    is what a parent gets if they tick every box in the form. They are the same
    schedule and must be the same price.
    """
    every_day = make_pricing_coordinator(
        children=[child("A")],
        chores=[chore(10, due_days=list(DAY_NAMES))],
    )
    unscheduled = make_pricing_coordinator(children=[child("A")], chores=[chore(10)])

    assert (
        every_day.get_child_daily_points(reward())
        == unscheduled.get_child_daily_points(reward())
        == {"A": 10.0}
    ), "ticking all seven days must price identically to ticking none"


def test_a_chore_due_one_day_a_week_costs_a_seventh_of_a_daily_one(
    make_pricing_coordinator,
) -> None:
    """The frequency factor, at its simplest."""
    weekly = make_pricing_coordinator(
        children=[child("A")],
        chores=[chore(70, due_days=["monday"])],
    )
    daily = make_pricing_coordinator(children=[child("A")], chores=[chore(70)])

    assert weekly.get_child_daily_points(reward()) == {"A": 10.0}, (
        "70 points on Mondays only is 4 days of the 28-day window, so 70 x 4/28 "
        "= 10.0 pts/day - a seventh of what the same chore earns daily"
    )
    assert daily.calculate_dynamic_reward_costs(reward(days_to_goal=7)) == {"A": 490}
    assert weekly.calculate_dynamic_reward_costs(reward(days_to_goal=7)) == {"A": 70}, (
        "the same reward must cost 70 rather than 490 when the chore that pays "
        "for it only comes round on Mondays"
    )


def test_a_mon_wed_fri_chore_is_not_priced_as_though_it_ran_every_day(
    make_pricing_coordinator,
) -> None:
    """The trap this factor exists to close.

    Before it, a parent who set up a Mon/Wed/Fri rotation got a reward priced
    off seven days of earnings the child could never make. The goal moved
    seven-thirds further away and nothing in the UI said why.
    """
    coordinator = make_pricing_coordinator(
        children=[child("A")],
        chores=[chore(70, due_days=["monday", "wednesday", "friday"])],
    )

    assert coordinator.get_child_daily_points(reward()) == {"A": 30.0}, (
        "three days a week is 12 of the 28-day window, so 70 x 12/28 = 30.0 "
        "pts/day, not the 70.0 an unscheduled chore would report"
    )
    assert coordinator.calculate_dynamic_reward_costs(reward(days_to_goal=7)) == {"A": 210}, (
        "so a week's goal is 210 points and not the unreachable 490"
    )


def test_an_occurrence_rotation_splits_a_chores_days_between_the_children_on_it(
    make_pricing_coordinator,
) -> None:
    """Taking turns halves what each child can expect to earn from a chore.

    Reading the frequency off `due_days` instead of resolving it per child
    would price both children as though they did the chore every weekend,
    which is exactly double what either of them can actually earn.
    """
    taking_turns = make_pricing_coordinator(
        children=[child("A"), child("B")],
        chores=[
            chore(
                70,
                assigned_to=["A", "B"],
                due_days=["saturday", "sunday"],
                rotation="occurrence",
                rotation_order=["A", "B"],
            )
        ],
    )
    both_every_time = make_pricing_coordinator(
        children=[child("A"), child("B")],
        chores=[chore(70, assigned_to=["A", "B"], due_days=["saturday", "sunday"])],
    )

    assert both_every_time.get_child_daily_points(reward()) == {"A": 20.0, "B": 20.0}, (
        "with no rotation both children are on all 8 weekend days: 70 x 8/28 = 20.0"
    )
    assert taking_turns.get_child_daily_points(reward()) == {"A": 10.0, "B": 10.0}, (
        "alternating gives each child 4 of those 8 days - 70 x 4/28 = 10.0 - "
        "however the anchor happens to line up with today, because eight "
        "occurrences shared between two children always divides evenly"
    )


def test_a_weekday_override_reprices_only_the_days_it_names(
    make_pricing_coordinator,
) -> None:
    """An override is part of the frequency, not an exception to it."""
    coordinator = make_pricing_coordinator(
        children=[child("A"), child("B")],
        chores=[chore(28, assigned_to=["A"], day_overrides={"monday": ["B"]})],
    )

    assert coordinator.get_child_daily_points(reward()) == {"A": 24.0, "B": 4.0}, (
        "A keeps 24 of the window's 28 days at 28 x 24/28 = 24.0 pts/day, and "
        "the four Mondays hand B 28 x 4/28 = 4.0 - counting days off `due_days` "
        f"would have missed both. Got {coordinator.get_child_daily_points(reward())}"
    )


def test_a_chore_that_never_comes_round_earns_nothing_rather_than_dividing_by_zero(
    make_pricing_coordinator,
) -> None:
    """A chore due on no day contributes nothing and must not raise.

    `["funday"]` is a non-empty `due_days` naming no real weekday, which is
    what a hand-written service call or a typo in YAML produces. The child is
    never scheduled, so they earn nothing from it and fall back to the reward's
    manual cost - the same place a child with no chores at all lands.
    """
    coordinator = make_pricing_coordinator(
        children=[child("A")],
        chores=[chore(50, assigned_to=["A"], due_days=["funday"])],
    )

    assert coordinator.get_child_daily_points(reward()) == {"A": 0.0}, (
        "the child is on a chore, so they appear in the rates - at 0.0, the "
        "same rate a chore with a completion percentage of 0 already reports. "
        "Reaching this at all means the zero-day guard held instead of dividing"
    )
    assert coordinator.calculate_dynamic_reward_costs(reward(cost=42, days_to_goal=10)) == {
        "A": 42
    }, (
        "and 0.0 is not > 0, so pricing gives up and charges the reward's "
        "manually entered cost of 42 - the existing behaviour for a child who "
        "earns nothing, reached by a new route"
    )


# --------------------------------------------------------------------------
# The singular back-compat wrapper
# --------------------------------------------------------------------------


def test_singular_cost_wrapper_prefers_the_named_child_then_the_first_price(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """`calculate_dynamic_reward_cost` degrades from exact -> first -> static."""
    screen = seeded_coordinator.get_reward("reward_screen")

    assert seeded_coordinator.calculate_dynamic_reward_cost(screen, "child_radha") == 91, (
        "Asking for Radha's price must return her own 13x7=91, not Krishna's 105"
    )
    assert seeded_coordinator.calculate_dynamic_reward_cost(screen, "nobody") == 105, (
        "An unknown child_id falls through to the first price in the dict, which "
        "is Krishna's 105 - there is no assignment validation here"
    )
    assert seeded_coordinator.calculate_dynamic_reward_cost(screen) == 105, (
        "Omitting child_id also returns the first computed price, 105"
    )


def test_singular_cost_wrapper_returns_the_static_cost_when_nothing_is_priced(
    make_pricing_coordinator,
) -> None:
    """An empty cost dict is the only path back to `reward.cost`."""
    coordinator = make_pricing_coordinator(
        children=[child("A")],
        chores=[chore(4)],
    )
    unassignable = reward(cost=123, assigned_to=["ghost_child"])

    assert coordinator.calculate_dynamic_reward_cost(unassignable, "A") == 123, (
        "When no child is priced the wrapper falls all the way back to the "
        "reward's static cost of 123"
    )


# --------------------------------------------------------------------------
# Log noise
# --------------------------------------------------------------------------


def test_pricing_never_logs_at_warning_level(
    make_pricing_coordinator, caplog: pytest.LogCaptureFixture
) -> None:
    """Developer tracing must stay at DEBUG.

    `calculate_dynamic_reward_costs` runs once per reward every time the
    overview sensor rebuilds its attributes, which is every refresh. It used to
    emit three fixed WARNINGs plus one per priced child each time, flooding the
    log of every install with `CALC_COSTS:` lines nobody asked for.
    """
    coordinator = make_pricing_coordinator(
        children=[child("A"), child("B")],
        chores=[chore(4)],
    )

    with caplog.at_level(logging.DEBUG, logger=COORDINATOR_LOGGER):
        coordinator.calculate_dynamic_reward_costs(reward(days_to_goal=5))

        assert warnings_from_coordinator(caplog) == [], (
            "pricing a reward must not warn; this is routine, expected work"
        )
        assert any("CALC_COSTS" in record.getMessage() for record in caplog.records), (
            "the tracing is still there, just at DEBUG where it belongs"
        )


def test_override_pricing_is_also_silent(
    make_pricing_coordinator, caplog: pytest.LogCaptureFixture
) -> None:
    """The override branch returns early, and equally quietly."""
    coordinator = make_pricing_coordinator(
        children=[child("A"), child("B")],
        chores=[chore(4)],
    )

    with caplog.at_level(logging.WARNING, logger=COORDINATOR_LOGGER):
        coordinator.calculate_dynamic_reward_costs(reward(cost=25, override_point_value=True))

    assert warnings_from_coordinator(caplog) == []


def test_get_child_daily_points_is_silent(
    make_pricing_coordinator, caplog: pytest.LogCaptureFixture
) -> None:
    """The daily-rate helper logs nothing, unlike the cost calculator."""
    coordinator = make_pricing_coordinator(
        children=[child("A"), child("B")],
        chores=[chore(4)],
    )

    with caplog.at_level(logging.DEBUG, logger=COORDINATOR_LOGGER):
        coordinator.get_child_daily_points(reward())

    assert [r for r in caplog.records if r.name == COORDINATOR_LOGGER] == [], (
        "get_child_daily_points must not log at all - it is called once per "
        "reward per sensor update"
    )
