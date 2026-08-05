"""Frontend registration for Choremander custom cards."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Final

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
    "choremander-activity-card.js",
    "choremander-streak-card.js",
]

# JS modules to load globally (for config flow sound preview)
GLOBAL_MODULES: Final = [
    "choremander-config-sounds.js",
]

# Track if frontend is registered
FRONTEND_REGISTERED: Final = "frontend_registered"


def _get_version() -> str:
    """Get version from manifest.json for cache busting.

    Blocking - reach it through `_async_get_version` from inside the event loop.
    """
    import json

    manifest_path = Path(__file__).parent / "manifest.json"
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
            return manifest.get("version", "1.0.0")
    except (FileNotFoundError, json.JSONDecodeError):
        return "1.0.0"


async def _async_get_version(hass: HomeAssistant) -> str:
    """Read the manifest off the event loop.

    Home Assistant flags the bare `open()` with a "Detected blocking call to
    open ... please create a bug report" warning and a full stack trace on every
    startup, which is alarming and entirely our doing.
    """
    return await hass.async_add_executor_job(_get_version)


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


async def async_ensure_resources_loaded(resources: Any) -> bool:
    """Load Lovelace's resource collection so it is safe to write to.

    `ResourceStorageCollection` is lazy: until `async_load()` has read
    `.storage/lovelace_resources`, its in-memory `data` is empty and
    `async_items()` reports nothing. Creating an item in that state makes the
    debounced save serialise *only* the rows just added, truncating the file and
    erasing every other integration's cards - which is exactly what issue #2
    was. Home Assistant guards its own writes the same way, in
    `ResourceStorageCollection.async_get_info` and `_update_data`.

    Returns False when the collection exposes no load state at all, meaning Home
    Assistant changed the API from under us. Callers must not write in that
    case: failing to register a card is recoverable, deleting somebody's
    dashboards is not.
    """
    loaded = getattr(resources, "loaded", None)
    if loaded is None:
        return False
    # `not loaded` rather than `is False`: if this ever stops being a plain bool,
    # anything falsy still means "not loaded yet" and must be loaded. An identity
    # check would quietly skip the load and reopen issue #2.
    if not loaded:
        await resources.async_load()
        resources.loaded = True
        _LOGGER.debug("Loaded the Lovelace resource collection before writing to it")
    return True


async def async_register_cards(hass: HomeAssistant) -> None:
    """Register card resources with Lovelace automatically."""
    version = await _async_get_version(hass)

    lovelace_data = hass.data.get("lovelace")
    if lovelace_data is None:
        _LOGGER.warning("Lovelace not available, cards may need manual registration")
        return

    # Get the mode - "storage" or "yaml". Home Assistant exposes this as
    # `resource_mode`; `mode` is the dashboard mode and does not exist on
    # LovelaceData, so reading it made this branch dead code.
    mode = getattr(lovelace_data, "resource_mode", "storage")

    if mode == "yaml":
        _LOGGER.info(
            "Lovelace is in YAML mode. Add these resources to configuration.yaml:"
        )
        for card in CARDS:
            _LOGGER.info("  - url: %s/%s", URL_BASE, card)
            _LOGGER.info("    type: module")
        return

    # Storage mode - add resources automatically
    try:
        resources = lovelace_data.resources
        if resources is None:
            _LOGGER.debug("Lovelace resources collection not available")
            return

        if not await async_ensure_resources_loaded(resources):
            _LOGGER.warning(
                "Lovelace resource collection has no load state; skipping "
                "automatic registration. Add the cards manually from %s/<card-name>.js",
                URL_BASE,
            )
            return

        # Get existing resource URLs (strip query params for comparison)
        existing_urls = set()
        for item in resources.async_items():
            url = item.get("url", "")
            existing_urls.add(url.split("?")[0])

        # Register each card
        for card in CARDS:
            card_url = f"{URL_BASE}/{card}"
            versioned_url = f"{card_url}?v={version}"

            if card_url in existing_urls:
                # Update version if needed
                for item in resources.async_items():
                    if item.get("url", "").split("?")[0] == card_url:
                        if item.get("url") != versioned_url:
                            await resources.async_update_item(
                                item["id"],
                                {"url": versioned_url},
                            )
                            _LOGGER.debug("Updated card version: %s", versioned_url)
                        break
            else:
                await resources.async_create_item(
                    {"url": versioned_url, "res_type": "module"}
                )
                _LOGGER.info("Registered Lovelace resource: %s", versioned_url)

    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "Could not register Lovelace resources: %s. "
            "Cards available at %s/<card-name>.js",
            err,
            URL_BASE,
        )
