"""Repair flows for Choremander.

Currently one: restoring the Lovelace resources that older versions deleted.
See `resource_repair.py` for how the missing rows are worked out - this module
is only the confirmation step in front of it.
"""

from __future__ import annotations

import logging

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir
import voluptuous as vol

from .const import DOMAIN
from .resource_repair import (
    ISSUE_ID,
    async_find_restorable_resources,
    async_get_writable_resources,
    async_restore_resources,
)

_LOGGER = logging.getLogger(__name__)


class RestoreResourcesRepairFlow(RepairsFlow):
    """Ask before re-registering the resources, then re-register them."""

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Handle the first step of the fix flow."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Show what will be restored, then restore it once confirmed."""
        if user_input is not None:
            restored = await async_restore_resources(self.hass)
            _LOGGER.info("Restored %s Lovelace resource(s)", len(restored))
            return self.async_create_entry(data={})

        # Recomputed rather than read from the issue: the user may have added
        # some of them back by hand since the issue was raised.
        resources = await async_get_writable_resources(self.hass)
        restorable = (
            await async_find_restorable_resources(self.hass, resources)
            if resources is not None
            else []
        )

        if not restorable:
            # Nothing left to do - showing "0 card(s)" over an empty list would
            # just be confusing. The issue is stale, so clear it too.
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_ID)
            return self.async_abort(reason="nothing_to_restore")

        return self.async_show_form(
            step_id="confirm",
            # Empty rather than omitted, matching Home Assistant's own
            # ConfirmRepairFlow: a confirmation has no fields, but the frontend
            # is handed a schema rather than relying on None serialising to [].
            data_schema=vol.Schema({}),
            description_placeholders={
                "count": str(len(restorable)),
                "resources": "\n".join(f"- `{item.url}`" for item in restorable),
            },
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the flow for a repair issue."""
    if issue_id == ISSUE_ID:
        return RestoreResourcesRepairFlow()

    raise ValueError(f"unknown repair {issue_id}")
