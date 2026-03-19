"""Frontend registration for Choremander custom cards."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Final

from homeassistant.components.http import StaticPathConfig
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# URL base path for serving static files
URL_BASE: Final = "/choremander"

# List of card files to register as Lovelace resources
CARDS: Final = [
    "choremander-child-card.js",
    "choremander-rewards-card.js",
    "choremander-approvals-card.js",
    "choremander-points-card.js",
    "choremander-reorder-card.js",
]

# JS modules to load globally (for config flow sound preview)
GLOBAL_MODULES: Final = [
    "choremander-config-sounds.js",
]

# Track if frontend is registered
FRONTEND_REGISTERED: Final = "frontend_registered"


async def _async_get_version(hass: HomeAssistant) -> str:
    """Get version from manifest.json for cache busting (async-safe)."""
    manifest_path = Path(__file__).parent / "manifest.json"
    try:
        content = await hass.async_add_executor_job(
            manifest_path.read_text, "utf-8"
        )
        return json.loads(content).get("version", "1.0.0")
    except Exception:  # noqa: BLE001
        return "1.0.0"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Register static paths for serving card JavaScript files."""
    # Only register once
    if hass.data.get(DOMAIN, {}).get(FRONTEND_REGISTERED):
        _LOGGER.debug("Frontend already registered, skipping")
        return

    www_path = Path(__file__).parent / "www"

    if not www_path.exists():
        _LOGGER.warning("www directory not found at %s", www_path)
        return

    # Register the www folder as a static path
    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_BASE, str(www_path), False)]
    )

    _LOGGER.debug("Registered static path: %s -> %s", URL_BASE, www_path)

    # Register global JS modules (loaded on all pages, including config flow)
    version = await _async_get_version(hass)
    for module in GLOBAL_MODULES:
        module_url = f"{URL_BASE}/{module}?v={version}"
        add_extra_js_url(hass, module_url)
        _LOGGER.info("Registered global frontend module: %s", module_url)

    # Mark as registered
    hass.data.setdefault(DOMAIN, {})[FRONTEND_REGISTERED] = True


async def async_register_cards(hass: HomeAssistant) -> None:
    """Register card resources with Lovelace automatically.

    Only adds missing Choremander resources. Never modifies or removes
    existing resources, including those from other integrations.
    """
    version = await _async_get_version(hass)

    lovelace_data = hass.data.get("lovelace")
    if lovelace_data is None:
        _LOGGER.warning("Lovelace not available, cards may need manual registration")
        return

    # Get the mode - "storage" or "yaml"
    mode = getattr(lovelace_data, "mode", "storage")

    if mode == "yaml":
        _LOGGER.info(
            "Lovelace is in YAML mode. Add these resources to configuration.yaml:"
        )
        for card in CARDS:
            _LOGGER.info("  - url: %s/%s", URL_BASE, card)
            _LOGGER.info("    type: module")
        return

    # Storage mode - only ADD missing resources, never update or remove existing ones
    try:
        resources = lovelace_data.resources
        if resources is None:
            _LOGGER.debug("Lovelace resources collection not available")
            return

        # Build set of existing Choremander resource base URLs only
        # (strip query params for comparison)
        existing_choremander_urls = set()
        for item in resources.async_items():
            url = item.get("url", "")
            base_url = url.split("?")[0]
            # Only track our own resources to avoid touching others
            if base_url.startswith(URL_BASE + "/"):
                existing_choremander_urls.add(base_url)

        # Only add cards that are completely missing — never update existing entries
        # This prevents any risk of corrupting or removing other resources
        for card in CARDS:
            card_url = f"{URL_BASE}/{card}"
            versioned_url = f"{card_url}?v={version}"

            if card_url not in existing_choremander_urls:
                await resources.async_create_item(
                    {"url": versioned_url, "res_type": "module"}
                )
                _LOGGER.info("Registered Lovelace resource: %s", versioned_url)
            else:
                _LOGGER.debug("Resource already registered: %s", card_url)

    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "Could not register Lovelace resources: %s. "
            "Cards available at %s/<card-name>.js",
            err,
            URL_BASE,
        )
