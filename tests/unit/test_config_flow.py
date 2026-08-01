"""Tests for `ChoremanderConfigFlow` - the initial user-facing setup form.

The config flow is a single step. Everything interesting about it lives in the
ordering of the branches inside `async_step_user`: the duplicate-entry guard is
*inside* the `user_input is not None` branch, so the "already configured" abort
only fires on submit, never on the first render.
"""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.choremander.config_flow import (
    ChoremanderConfigFlow,
    ChoremanderOptionsFlow,
)
from custom_components.choremander.const import (
    DEFAULT_POINTS_ICON,
    DEFAULT_POINTS_NAME,
    DOMAIN,
)


async def _start_user_flow(hass: HomeAssistant) -> dict[str, Any]:
    """Kick off the interactive (`SOURCE_USER`) config flow."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


@pytest.mark.usefixtures("bypass_frontend_registration")
async def test_user_step_renders_a_form_with_no_errors(hass: HomeAssistant) -> None:
    """Opening the flow shows the welcome form rather than doing anything."""
    result = await _start_user_flow(hass)

    assert result["type"] is data_entry_flow.FlowResultType.FORM, (
        f"the first screen of the config flow should be a form, got {result['type']}"
    )
    assert result["step_id"] == "user", "the only config-flow step is called 'user'"
    assert result["errors"] == {}, (
        "a freshly opened form must not pre-populate any validation errors"
    )
    assert result["description_placeholders"] == {"title": "Welcome to Choremander!"}, (
        "the welcome banner text is part of the user-visible contract"
    )


@pytest.mark.usefixtures("bypass_frontend_registration")
async def test_submitting_an_empty_form_creates_an_entry_with_star_defaults(
    hass: HomeAssistant,
) -> None:
    """Both fields are optional, so an empty submit falls back to Stars/mdi:star."""
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY, (
        "an empty submit is valid - every field has a default - so the entry is created"
    )
    assert result["title"] == "Choremander", "the entry title is hardcoded"
    assert result["data"] == {
        "points_name": DEFAULT_POINTS_NAME,
        "points_icon": DEFAULT_POINTS_ICON,
    }, "an omitted points name/icon must fall back to the DEFAULT_* constants"

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == DOMAIN, (
        "the flow claims the domain itself as the unique id, which is what makes "
        "Choremander single-instance"
    )


@pytest.mark.usefixtures("bypass_frontend_registration")
async def test_points_name_and_icon_typed_by_the_user_are_stored_verbatim(
    hass: HomeAssistant,
) -> None:
    """Whatever the parent types for their currency is what gets persisted."""
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"points_name": "Coins", "points_icon": "mdi:cash"}
    )
    await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"] == {"points_name": "Coins", "points_icon": "mdi:cash"}, (
        "user-supplied currency settings must survive into entry.data untouched"
    )


@pytest.mark.usefixtures("bypass_frontend_registration")
async def test_a_second_instance_aborts_on_submit_and_not_on_open(
    hass: HomeAssistant,
) -> None:
    """The duplicate guard lives inside the submit branch, so the form still opens.

    A user with Choremander already installed can open "Add integration", fill
    the form in, and only then be told they cannot have a second copy.
    """
    MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={}).add_to_hass(hass)

    result = await _start_user_flow(hass)
    assert result["type"] is data_entry_flow.FlowResultType.FORM, (
        "async_set_unique_id/_abort_if_unique_id_configured run only when user_input "
        "is not None, so opening the flow still renders the form"
    )

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is data_entry_flow.FlowResultType.ABORT, (
        "submitting a second instance must abort instead of creating a duplicate"
    )
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("bypass_frontend_registration")
async def test_an_existing_entry_without_the_domain_unique_id_does_not_block_setup(
    hass: HomeAssistant,
) -> None:
    """The guard matches on unique_id only - a `unique_id=None` entry is invisible.

    This is a trap for test authors: `MockConfigEntry(domain=DOMAIN)` alone does
    NOT reproduce the "already configured" abort.
    """
    MockConfigEntry(domain=DOMAIN, data={}).add_to_hass(hass)

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY, (
        "an entry lacking unique_id='choremander' does not trip the duplicate guard, "
        "so a second entry is happily created"
    )
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_async_get_options_flow_hands_back_the_choremander_options_flow(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The gear icon in the UI must route to Choremander's own options handler."""
    mock_config_entry.add_to_hass(hass)

    flow = ChoremanderConfigFlow.async_get_options_flow(mock_config_entry)
    assert isinstance(flow, ChoremanderOptionsFlow), (
        "async_get_options_flow must return a ChoremanderOptionsFlow so the options "
        "menu is Choremander's and not Home Assistant's generic one"
    )
