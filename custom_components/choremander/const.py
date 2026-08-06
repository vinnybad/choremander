"""Constants for Choremander integration."""
from typing import Final

DOMAIN: Final = "choremander"

# Configuration keys
CONF_CHILDREN: Final = "children"
CONF_CHORES: Final = "chores"
CONF_REWARDS: Final = "rewards"
CONF_POINTS_NAME: Final = "points_name"
CONF_POINTS_ICON: Final = "points_icon"

# Child keys
CONF_CHILD_NAME: Final = "name"
CONF_CHILD_AVATAR: Final = "avatar"
CONF_CHILD_POINTS: Final = "points"
CONF_CHILD_ID: Final = "id"

# Chore keys
CONF_CHORE_NAME: Final = "name"
CONF_CHORE_DESCRIPTION: Final = "description"
CONF_CHORE_POINTS: Final = "points"
CONF_CHORE_DUE_DAYS: Final = "due_days"
CONF_CHORE_ASSIGNED_TO: Final = "assigned_to"
CONF_CHORE_ID: Final = "id"
CONF_CHORE_REQUIRES_APPROVAL: Final = "requires_approval"

# Reward keys
CONF_REWARD_NAME: Final = "name"
CONF_REWARD_DESCRIPTION: Final = "description"
CONF_REWARD_COST: Final = "cost"
CONF_REWARD_ICON: Final = "icon"
CONF_REWARD_ID: Final = "id"

# Default values
DEFAULT_POINTS_NAME: Final = "Stars"
DEFAULT_POINTS_ICON: Final = "mdi:star"

# Days of week
DAYS_OF_WEEK: Final = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]

# Time categories for chores
TIME_CATEGORIES: Final = [
    "morning",
    "afternoon",
    "evening",
    "night",
    "anytime",
]

# Time category icons
TIME_CATEGORY_ICONS: Final = {
    "morning": "mdi:weather-sunny",
    "afternoon": "mdi:white-balance-sunny",
    "evening": "mdi:weather-sunset",
    "night": "mdi:weather-night",
    "anytime": "mdi:clock-outline",
}

# Avatar options
AVATAR_OPTIONS: Final = [
    # Basic faces
    "mdi:account-circle",
    "mdi:face-man",
    "mdi:face-woman",
    "mdi:face-man-outline",
    "mdi:face-woman-outline",
    # Fun character avatars
    "mdi:robot-happy",
    "mdi:robot-excited",
    "mdi:alien",
    "mdi:ninja",
    "mdi:pirate",
    # Animals kids love
    "mdi:cat",
    "mdi:dog",
    "mdi:unicorn",
    "mdi:dragon",
    "mdi:owl",
    "mdi:penguin",
    "mdi:panda",
    "mdi:rabbit",
    "mdi:koala",
    "mdi:fox",
    "mdi:dolphin",
    "mdi:jellyfish",
    "mdi:octopus",
    "mdi:butterfly",
    # Splatoon Inkling-inspired (colorful, action-themed)
    "mdi:palette",
    "mdi:spray",
    "mdi:water-outline",
    "mdi:shimmer",
    "mdi:flare",
    "mdi:star-face",
    "mdi:emoticon-cool",
    "mdi:emoticon-excited",
    "mdi:emoticon-kiss",
    "mdi:emoticon-wink",
    "mdi:emoticon-happy",
    "mdi:emoticon-poop",
    "mdi:face-woman-shimmer",
    "mdi:face-man-shimmer",
    "mdi:account-star",
    "mdi:account-heart",
    # Gaming and action themed
    "mdi:gamepad-variant",
    "mdi:controller",
    "mdi:trophy",
    "mdi:crown",
    "mdi:lightning-bolt",
    "mdi:fire",
    "mdi:rocket",
    "mdi:sword",
    "mdi:shield",
    # Space and fantasy
    "mdi:alien-outline",
    "mdi:ufo",
    "mdi:space-invaders",
    "mdi:ghost",
    "mdi:skull-crossbones",
    "mdi:wizard-hat",
    "mdi:magic-staff",
    # Nature and colorful
    "mdi:flower-tulip",
    "mdi:flower-poppy",
    "mdi:clover",
    "mdi:mushroom",
    "mdi:star-shooting",
    "mdi:star-four-points",
    "mdi:heart",
    "mdi:heart-multiple",
    "mdi:diamond-stone",
    "mdi:puzzle-heart",
]

# Reward icon options
REWARD_ICON_OPTIONS: Final = [
    "mdi:gift",
    "mdi:ice-cream",
    "mdi:pizza",
    "mdi:movie",
    "mdi:gamepad-variant",
    "mdi:tablet",
    "mdi:television",
    "mdi:bike",
    "mdi:currency-usd",
    "mdi:shopping",
    "mdi:party-popper",
    "mdi:swim",
    "mdi:sleep",
    "mdi:candy",
]

# Platforms
PLATFORMS: Final = ["sensor", "button", "binary_sensor"]

# Services
SERVICE_COMPLETE_CHORE: Final = "complete_chore"
SERVICE_APPROVE_CHORE: Final = "approve_chore"
SERVICE_REJECT_CHORE: Final = "reject_chore"
SERVICE_CLAIM_REWARD: Final = "claim_reward"
SERVICE_APPROVE_REWARD: Final = "approve_reward"
SERVICE_ADD_POINTS: Final = "add_points"
SERVICE_REMOVE_POINTS: Final = "remove_points"
SERVICE_RESET_DAILY: Final = "reset_daily"
SERVICE_SET_CHORE_ORDER: Final = "set_chore_order"
SERVICE_SET_CHORE_SCHEDULE: Final = "set_chore_schedule"
SERVICE_PREVIEW_SOUND: Final = "preview_sound"

# Events
EVENT_PREVIEW_SOUND: Final = "choremander_preview_sound"

# Attributes
ATTR_CHILD_ID: Final = "child_id"
ATTR_CHORE_ID: Final = "chore_id"
ATTR_REWARD_ID: Final = "reward_id"
ATTR_POINTS: Final = "points"
ATTR_REASON: Final = "reason"
ATTR_CHORE_ORDER: Final = "chore_order"
ATTR_SOUND: Final = "sound"

# The scheduling fields, as `set_chore_schedule` names them. Each one is
# optional in the schema and absent means "leave it alone", so these are read
# with `in call.data` rather than `call.data.get(...)` - a default here would
# make clearing a rule indistinguishable from not mentioning it.
ATTR_DUE_DAYS: Final = "due_days"
ATTR_ROTATION: Final = "rotation"
ATTR_ROTATION_ORDER: Final = "rotation_order"
ATTR_ROTATION_ANCHOR: Final = "rotation_anchor"
ATTR_DAY_OVERRIDES: Final = "day_overrides"
ATTR_DATE_OVERRIDES: Final = "date_overrides"

# States
STATE_PENDING: Final = "pending"
STATE_AWAITING_APPROVAL: Final = "awaiting_approval"
STATE_COMPLETED: Final = "completed"
STATE_CLAIMED: Final = "claimed"

# Completion sound options
# Most sounds are synthesized via Web Audio API
# Fart sounds are CC0 audio files from BigSoundBank.com and GfxSounds.com
COMPLETION_SOUND_OPTIONS: Final = [
    "none",        # No sound
    "coin",        # Coin collect sound
    "levelup",     # Level up / success sound
    "fanfare",     # Celebratory fanfare
    "chime",       # Simple chime
    "powerup",     # Power up sound
    "undo",        # Sad/descending "womp womp" for undo actions
    "fart1",       # Flatulence 1 (short)
    "fart2",       # Flatulence 2 (short)
    "fart3",       # Flatulence 3 (short)
    "fart4",       # Pony flatulence 2 (~3 sec)
    "fart5",       # Flatulence 4 - discreet (short)
    "fart6",       # Prout'cochons 1 - pig game sound (short)
    "fart7",       # Prout'cochons 2 - pig game sound (short)
    "fart8",       # Prout'cochons 3 - pig game sound (short)
    "fart9",       # Pony flatulence 1 (short)
    "fart10",      # Baby fart (short)
    "fart_random", # Random fart - picks a random fart sound each time!
]

# Default completion sound
DEFAULT_COMPLETION_SOUND: Final = "coin"

# Chore keys (sound-related)
CONF_CHORE_COMPLETION_SOUND: Final = "completion_sound"

# Points ledger event types.
#
# These are the stored, structured identity of a ledger entry. They are
# deliberately NOT human-readable prose: the display string is built in the
# card from the event type plus the entry's own fields. Storing a sentence
# instead would force every consumer that wants to classify, colour, filter or
# translate an entry to parse English back out of it.
EVENT_CHORE_APPROVED: Final = "chore_approved"
EVENT_CHORE_REJECTED: Final = "chore_rejected"
EVENT_REWARD_CLAIMED: Final = "reward_claimed"
EVENT_REWARD_REFUNDED: Final = "reward_refunded"
EVENT_POINTS_ADDED: Final = "points_added"
EVENT_POINTS_REMOVED: Final = "points_removed"

# Not a ledger entry - a chore that has been done but not yet approved has not
# moved anyone's balance. It appears in the activity feed because "I did this,
# where is it?" is exactly the question the feed exists to answer.
EVENT_CHORE_PENDING: Final = "chore_pending"

POINTS_EVENT_TYPES: Final = [
    EVENT_CHORE_APPROVED,
    EVENT_CHORE_REJECTED,
    EVENT_REWARD_CLAIMED,
    EVENT_REWARD_REFUNDED,
    EVENT_POINTS_ADDED,
    EVENT_POINTS_REMOVED,
]

# The ledger is a rolling buffer, not an archive. Older entries fall off the
# end so a long-lived install cannot grow the storage blob without bound.
#
# This is why the ledger explains *recent* movements rather than a child's
# whole balance: once entries have been trimmed, or on an install upgraded
# from version 1 (where manual adjustments were never recorded and so cannot
# be reconstructed), the entries no longer sum to the balance. Nothing derives
# a balance from it, and nothing should without first writing carry-forward
# entries at the trim point.
MAX_POINTS_TRANSACTIONS: Final = 500

# A reason is free text from a parent, unbounded at the service boundary. The
# activity feed shares one entity's 16KiB attribute budget, and going over it
# makes Home Assistant stop recording the entity without raising anything.
MAX_REASON_LENGTH: Final = 120

# How many events the activity sensor publishes. Home Assistant's recorder
# refuses to store a state whose attributes exceed 16KiB, so this is kept well
# clear of that ceiling; tests/unit/test_sensor_payload_size.py holds the line.
ACTIVITY_FEED_LIMIT: Final = 40

# How many days of per-child activity the streak card's dot strip can draw.
ACTIVE_DAYS_WINDOW: Final = 30

# How far the resolved schedule sensor publishes. Two whole weeks, aligned to
# Monday: "this week and next" covers every edit a parent actually makes - a
# recurring "every Saturday" rule and a one-off swap are almost always in one
# of the two - without paging, and it keeps the payload small enough that the
# recorder's 16KiB attribute ceiling is never in question. The cost is that an
# override cannot be set a month ahead.
SCHEDULE_WINDOW_DAYS: Final = 14
