"""The committed Choremander dev seed, frozen as an importable constant.

`dev/config/.storage/choremander.storage.choremander_dev_entry` is mounted
read-write into the `choremander-dev` container, which rewrites it whenever
someone presses a button in the dev instance. Reading it at test time makes the
suite non-deterministic (completion counts, point balances and `chore_order`
all drift). This module is a byte-faithful copy of the version committed at
`git show HEAD:dev/config/.storage/choremander.storage.choremander_dev_entry`,
so seed-backed tests stay hermetic.

Always deep-copy before handing it to a test - `dev_seed` fixtures do.
"""

from __future__ import annotations

from typing import Any

DEV_SEED: dict[str, Any] = {
    "children": [
        {"id": "child_krishna", "name": "Krishna", "points": 25, "avatar": "mdi:account-circle"},
        {"id": "child_radha", "name": "Radha", "points": 15, "avatar": "mdi:account-circle"},
        {"id": "child_meera", "name": "Meera", "points": 8, "avatar": "mdi:account-circle"},
    ],
    "chores": [
        {
            "id": "chore_bed",
            "name": "Make Bed",
            "points": 2,
            "time_category": "morning",
            "assigned_to": ["child_krishna", "child_radha"],
            "requires_approval": False,
            "daily_limit": 1,
        },
        {
            "id": "chore_teeth_am",
            "name": "Brush Teeth (Morning)",
            "points": 1,
            "time_category": "morning",
            "assigned_to": ["child_krishna", "child_radha"],
            "requires_approval": False,
            "daily_limit": 1,
        },
        {
            "id": "chore_feed_dog",
            "name": "Feed the Dog",
            "points": 3,
            "time_category": "morning",
            "assigned_to": ["child_krishna"],
            "requires_approval": False,
            "daily_limit": 1,
        },
        {
            "id": "chore_water_plants",
            "name": "Water the Plants",
            "points": 2,
            "time_category": "morning",
            "assigned_to": ["child_radha"],
            "requires_approval": False,
            "daily_limit": 1,
        },
        {
            "id": "chore_homework",
            "name": "Do Homework",
            "points": 5,
            "time_category": "evening",
            "assigned_to": ["child_krishna"],
            "requires_approval": True,
            "daily_limit": 1,
        },
        {
            "id": "chore_practice_piano",
            "name": "Practice Piano",
            "points": 4,
            "time_category": "evening",
            "assigned_to": ["child_radha"],
            "requires_approval": True,
            "daily_limit": 1,
        },
        {
            "id": "chore_teeth_pm",
            "name": "Brush Teeth (Evening)",
            "points": 1,
            "time_category": "evening",
            "assigned_to": ["child_krishna", "child_radha"],
            "requires_approval": False,
            "daily_limit": 1,
        },
        {
            "id": "chore_tidy_room",
            "name": "Tidy Room",
            "points": 3,
            "time_category": "evening",
            "assigned_to": ["child_krishna", "child_radha"],
            "requires_approval": True,
            "daily_limit": 1,
        },
        {
            "id": "chore_fish",
            "name": "Feed the Fish",
            "points": 2,
            "time_category": "morning",
            "assigned_to": ["child_meera"],
            "due_days": [
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
            ],
            "requires_approval": False,
            "daily_limit": 1,
        },
        {
            "id": "chore_bins",
            "name": "Take Out the Bins",
            "points": 4,
            "time_category": "evening",
            "assigned_to": ["child_meera"],
            "due_days": ["monday", "thursday"],
            "requires_approval": False,
            "daily_limit": 1,
        },
        {
            "id": "chore_dishes",
            "name": "Wash the Dishes",
            "points": 3,
            "time_category": "evening",
            "assigned_to": ["child_krishna", "child_meera"],
            "due_days": ["wednesday", "saturday"],
            "rotation": "occurrence",
            "rotation_order": ["child_krishna", "child_meera"],
            "requires_approval": True,
            "daily_limit": 1,
        },
    ],
    "rewards": [
        {
            "id": "reward_screen",
            "name": "30 Min Screen Time",
            "cost": 10,
            "icon": "mdi:television",
            "assigned_to": [],
            "is_jackpot": False,
            "override_point_value": False,
            "days_to_goal": 7,
        },
        {
            "id": "reward_krishna_game",
            "name": "New Video Game",
            "cost": 50,
            "icon": "mdi:gamepad-variant",
            "assigned_to": ["child_krishna"],
            "is_jackpot": False,
            "override_point_value": True,
        },
        {
            "id": "reward_radha_book",
            "name": "New Book",
            "cost": 30,
            "icon": "mdi:book-open-variant",
            "assigned_to": ["child_radha"],
            "is_jackpot": False,
            "override_point_value": True,
        },
        {
            "id": "reward_movie_night",
            "name": "Family Movie Night",
            "cost": 75,
            "icon": "mdi:movie-open",
            "assigned_to": [],
            "is_jackpot": True,
            "override_point_value": False,
            "days_to_goal": 14,
        },
        {
            "id": "reward_zoo_trip",
            "name": "Trip to the Zoo",
            "cost": 150,
            "icon": "mdi:elephant",
            "assigned_to": [],
            "is_jackpot": True,
            "override_point_value": False,
            "days_to_goal": 30,
        },
    ],
    "completions": [],
    "pending_completions": [],
    "reward_claims": [],
    "pending_reward_claims": [],
    "points_name": "Stars",
    "points_icon": "mdi:star",
}
