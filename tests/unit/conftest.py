"""Fixtures for the in-process unit suite.

These run Home Assistant inside the test process via
pytest-homeassistant-custom-component - no container required. They are
deliberately scoped to `tests/unit/` so the container-backed suite in
`tests/e2e/` does not inherit any of this machinery.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
import copy
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.choremander.const import DOMAIN
from custom_components.choremander.coordinator import ChoremanderCoordinator
from tests.unit.dev_seed import DEV_SEED


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: Any,
) -> Generator[None]:
    """Load `custom_components/` for every test in this suite."""
    yield


@pytest.fixture
def dev_seed() -> dict[str, Any]:
    """A fresh deep copy of the committed dev-container seed payload.

    Sourced from `tests/unit/dev_seed.py`, never from
    `dev/config/.storage/`: that file is mounted read-write into the running
    `choremander-dev` container, which rewrites completions, point balances and
    `chore_order` underneath the suite. Deep-copied per test because
    `to_dict()`/`from_dict()` alias list objects.
    """
    return copy.deepcopy(DEV_SEED)


@pytest.fixture
def bypass_frontend_registration() -> Generator[None]:
    """Skip static-path and Lovelace resource registration.

    Deliberately NOT autouse. `frontend.py` writes a `frontend_registered` key
    into `hass.data[DOMAIN]` - the same dict `__init__.py` scans to decide
    whether any config entries remain on unload. Bypassing registration
    therefore changes teardown behaviour, and making this automatic would hide
    that interaction from every lifecycle test. Request it explicitly only when
    a test genuinely does not care about the frontend.
    """
    from unittest.mock import AsyncMock, patch

    with (
        patch(
            "custom_components.choremander.async_register_frontend",
            AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.choremander.async_register_cards",
            AsyncMock(return_value=None),
        ),
    ):
        yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A Choremander config entry with default point settings."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Choremander",
        data={"points_name": "Stars", "points_icon": "mdi:star"},
        entry_id="test_entry_id",
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> AsyncGenerator[ConfigEntry]:
    """Set up Choremander with empty storage and yield the config entry."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    yield mock_config_entry


@pytest.fixture
async def coordinator(hass: HomeAssistant, init_integration: ConfigEntry) -> ChoremanderCoordinator:
    """The live coordinator for the set-up entry."""
    return hass.data[DOMAIN][init_integration.entry_id]
