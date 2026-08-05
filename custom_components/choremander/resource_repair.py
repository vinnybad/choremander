"""Recovery for Lovelace resources earlier versions deleted (issue #2).

Choremander used to register its cards against Home Assistant's Lovelace
resource collection before that collection had been loaded from disk. Because
the collection serialises whatever is in memory, the debounced save wrote out
only Choremander's own cards and truncated `.storage/lovelace_resources`,
erasing every resource any other integration had registered. `frontend.py` no
longer does that, but the fix cannot bring back rows that are already gone, and
nothing else restores them: HACS registers a plugin's resource when the plugin
is installed, not on startup, so an affected install stays broken indefinitely.

Everything needed to rebuild those rows survived the wipe, though. The card
files are still on disk under `config/www/`, and the dashboards still reference
the cards by `custom:<element>`. This module pairs the two back up:

    a dashboard asks for `custom:mini-graph-card`
      -> some file under config/www/ calls customElements.define("mini-graph-card")
      -> no registered resource points at that file
      => that resource was deleted and can be restored

All three conditions have to hold, which keeps false positives close to zero:
the card is demonstrably wanted, demonstrably present, and demonstrably not
reachable. Detection only ever reads; the restore itself is performed by the
repair flow in `repairs.py`, after the user has seen the list and confirmed it.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
import logging
from pathlib import Path
import re
from typing import Any, Final

from homeassistant.components.frontend import (
    DATA_EXTRA_JS_URL_ES5,
    DATA_EXTRA_MODULE_URL,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .frontend import async_ensure_resources_loaded

_LOGGER = logging.getLogger(__name__)

ISSUE_ID: Final = "deleted_lovelace_resources"

# Matches customElements.define("my-card", ...) / .define('my-card', ...).
_DEFINE_RE: Final = re.compile(
    r"""customElements\s*\.\s*define\s*\(\s*["']([A-Za-z][\w-]*)["']"""
)

# Bounds on the www scan. A card bundle is tens or hundreds of KB; anything
# larger is not a card, and no sane config has thousands of scripts. These stop
# a pathological www/ directory from stalling startup.
_MAX_FILE_BYTES: Final = 4 * 1024 * 1024
_MAX_FILES: Final = 2000

# Home Assistant serves config/www at /local. HACS additionally serves
# config/www/community at /hacsfiles.
_LOCAL_PREFIX: Final = "/local/"
_HACS_PREFIX: Final = "/hacsfiles/"
_COMMUNITY_DIR: Final = "community"


@dataclass(frozen=True)
class RestorableResource:
    """A resource that a dashboard needs, that exists on disk, and is unlisted."""

    url: str
    """The URL to register, e.g. `/hacsfiles/mini-graph-card/mini-graph-card.js`."""

    relative_path: str
    """Path of the file relative to `config/www`, used for de-duplication."""

    card_types: tuple[str, ...]
    """The `custom:` elements this file defines that some dashboard asks for."""


def _iter_custom_types(config: Any) -> set[str]:
    """Collect every `custom:<element>` type named anywhere in a dashboard."""
    found: set[str] = set()
    stack: list[Any] = [config]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            card_type = node.get("type")
            if isinstance(card_type, str) and card_type.startswith("custom:"):
                found.add(card_type.removeprefix("custom:"))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return found


def _scan_www(www_root: Path) -> dict[str, list[str]]:
    """Map each custom element defined under `config/www` to every file defining it.

    Blocking; call from the executor. Every path is kept, not just the first:
    a card repository often ships both a built bundle and the sources it was
    built from, and only one of them is the module that should be registered.
    Knowing about all of them lets the caller treat the element as reachable
    when *any* of its files is already registered, instead of offering to
    register a source file that is not a loadable module.
    """
    defined: dict[str, list[str]] = {}
    if not www_root.is_dir():
        return defined

    # Bound the walk before sorting it - `rglob` on a pathological tree would
    # otherwise be materialised in full just to be truncated afterwards. Which
    # files survive the cap is then filesystem-order dependent, so say when it
    # bites: past it, restoration silently stops finding things.
    candidates = sorted(islice(www_root.rglob("*.js"), _MAX_FILES))
    if len(candidates) == _MAX_FILES:
        _LOGGER.debug(
            "Stopped scanning %s at %s files; a card beyond the cap will not be "
            "offered for restoration",
            www_root,
            _MAX_FILES,
        )

    for path in candidates:
        try:
            if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
                continue
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as err:
            _LOGGER.debug("Could not read %s: %s", path, err)
            continue

        relative = path.relative_to(www_root).as_posix()
        for element in _DEFINE_RE.findall(source):
            paths = defined.setdefault(element, [])
            if relative not in paths:
                paths.append(relative)

    return defined


def _relative_path_for_url(url: str) -> str | None:
    """Map a resource URL back to a path under `config/www`, if it points there."""
    path = url.split("?")[0].split("#")[0]
    if path.startswith(_HACS_PREFIX):
        return f"{_COMMUNITY_DIR}/{path.removeprefix(_HACS_PREFIX)}"
    if path.startswith(_LOCAL_PREFIX):
        return path.removeprefix(_LOCAL_PREFIX)
    return None


def _url_for_relative_path(relative: str, *, hacs_loaded: bool) -> str:
    """Choose the URL to serve a www file at.

    Prefers `/hacsfiles/...` for files under `community/` when HACS is running,
    matching the URL HACS itself would register so a later reinstall updates the
    row we restore instead of adding a second one.
    """
    if hacs_loaded and relative.startswith(f"{_COMMUNITY_DIR}/"):
        return f"{_HACS_PREFIX}{relative.removeprefix(f'{_COMMUNITY_DIR}/')}"
    return f"{_LOCAL_PREFIX}{relative}"


def _best_candidate(card_type: str, candidates: list[str]) -> str:
    """Choose which of several files defining `card_type` to register.

    A card repository often ships a built bundle next to the sources it was
    built from, and both call `customElements.define` with the same name. Only
    the bundle is a loadable module; registering the source instead hands the
    user a resource row that silently fails to load, which is worse than doing
    nothing because the repair then closes and cannot be retried.

    Prefer, in order: a file named exactly after the element, one whose name
    contains it, then the shallowest and shortest path - built bundles sit at
    the top of a repository while sources live under `src/`. Alphabetical order
    is the last resort, and on its own it is not a signal at all.
    """

    def rank(relative: str) -> tuple[bool, bool, int, int, str]:
        stem = relative.rsplit("/", 1)[-1].removesuffix(".js")
        return (
            stem != card_type,  # False (exact match) sorts first
            card_type not in stem,
            relative.count("/"),
            len(relative),
            relative,
        )

    return min(candidates, key=rank)


async def _async_wanted_card_types(hass: HomeAssistant) -> set[str]:
    """Every `custom:` element referenced by any dashboard."""
    lovelace_data = hass.data.get("lovelace")
    dashboards = getattr(lovelace_data, "dashboards", None) or {}

    wanted: set[str] = set()
    for url_path, dashboard in dashboards.items():
        try:
            config = await dashboard.async_load(False)
        except HomeAssistantError as err:
            # ConfigNotFound subclasses this: a strategy or auto-generated
            # dashboard has no stored config to read.
            _LOGGER.debug("Skipping dashboard %s: %s", url_path or "default", err)
            continue
        wanted |= _iter_custom_types(config)

    return wanted


def _registered_urls(resources: Any, hass: HomeAssistant) -> list[str]:
    """Every URL a module is currently served from."""
    urls = [item.get("url", "") for item in resources.async_items()]

    # Modules registered with the frontend directly (add_extra_js_url) are
    # loaded on every page and so also count as reachable. Both the ES module
    # and the legacy ES5 manager are consulted - a card served from either is
    # loading fine and must not be reported as missing.
    for key in (DATA_EXTRA_MODULE_URL, DATA_EXTRA_JS_URL_ES5):
        extra = hass.data.get(key)
        if extra is not None:
            urls.extend(extra.urls)

    return urls


def _reachable(urls: list[str]) -> tuple[set[str], set[str]]:
    """Split registered URLs into www-relative paths and bare file names.

    The file names matter because a resource does not have to be served out of
    `config/www` at all. Choremander serves its own cards from `/choremander/`,
    and any integration may register a static path of its own; those URLs map to
    no www path, so a copy of the same file sitting under `config/www` would
    otherwise look unregistered. Comparing file names as well keeps the check
    conservative: an ambiguous match means we decline to restore rather than
    offer a duplicate of something already working.

    A URL that *does* resolve to a www path is compared by that path only. Its
    file name carries no extra information, and folding it in would let a
    registered `/hacsfiles/foo/index.js` vouch for an unrelated deleted
    `community/bar/index.js`.
    """
    paths: set[str] = set()
    names: set[str] = set()
    for url in urls:
        if (relative := _relative_path_for_url(url)) is not None:
            paths.add(relative)
        elif bare := url.split("?")[0].split("#")[0].rsplit("/", 1)[-1]:
            names.add(bare)
    return paths, names


async def async_find_restorable_resources(
    hass: HomeAssistant, resources: Any
) -> list[RestorableResource]:
    """Find resources a dashboard needs that exist on disk but are unregistered.

    `resources` must already be loaded - see `frontend.async_register_cards`.
    """
    wanted = await _async_wanted_card_types(hass)
    if not wanted:
        return []

    www_root = Path(hass.config.path("www"))
    defined = await hass.async_add_executor_job(_scan_www, www_root)
    if not defined:
        return []

    registered_paths, registered_names = _reachable(_registered_urls(resources, hass))
    hacs_loaded = "hacs" in hass.config.components

    def _is_reachable(relative: str) -> bool:
        return relative in registered_paths or relative.rsplit("/", 1)[-1] in registered_names

    # Group by file: one resource can provide several of the missing elements.
    by_path: dict[str, list[str]] = {}
    for card_type in sorted(wanted):
        candidates = defined.get(card_type)
        if not candidates:
            continue
        if any(_is_reachable(relative) for relative in candidates):
            # Some file defining this element is already served. Registering
            # another copy would at best duplicate it and at worst point
            # Lovelace at an unbundled source file.
            continue
        by_path.setdefault(_best_candidate(card_type, candidates), []).append(card_type)

    return [
        RestorableResource(
            url=_url_for_relative_path(relative, hacs_loaded=hacs_loaded),
            relative_path=relative,
            card_types=tuple(types),
        )
        for relative, types in sorted(by_path.items())
    ]


async def async_get_writable_resources(hass: HomeAssistant) -> Any | None:
    """The Lovelace resource collection, loaded and writable, or None.

    Returns None in YAML resource mode. There the collection is a
    `ResourceYAMLCollection`, which is a read-only view over
    `configuration.yaml`: it has no `async_create_item`, so offering to restore
    anything would hand the user a repair that fails when they submit it. Its
    `loaded` is a `True` class attribute, so the load guard alone does not catch
    this. Resources are the user's to edit by hand in that mode anyway.
    """
    lovelace_data = hass.data.get("lovelace")
    if lovelace_data is None:
        return None

    if getattr(lovelace_data, "resource_mode", "storage") != "storage":
        _LOGGER.debug("Lovelace resources are in YAML mode; nothing to restore")
        return None

    resources = getattr(lovelace_data, "resources", None)
    if resources is None:
        return None
    if not await async_ensure_resources_loaded(resources):
        return None
    return resources


async def async_check_for_deleted_resources(hass: HomeAssistant) -> None:
    """Raise or clear the repair issue describing what can be restored."""
    try:
        resources = await async_get_writable_resources(hass)
        if resources is None:
            return
        restorable = await async_find_restorable_resources(hass, resources)
    except Exception:
        # Never let a read-only scan break startup - but say so out loud. An
        # affected user whose scan fails silently gets no repair and no clue,
        # and this module only ever reads, so there is nothing to hide.
        _LOGGER.warning(
            "Could not check for deleted Lovelace resources. Your cards are "
            "unaffected; if any are missing from Settings > Dashboards > "
            "Resources, add them back by hand",
            exc_info=True,
        )
        return

    if not restorable:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_ID)
        return

    # Deliberately info, not warning. The repair issue is the signal, and it
    # honours dismissal; a warning does not. Somebody who has looked at the
    # repair and decided to ignore it should not keep finding this in their log
    # on every restart with no way to silence it.
    _LOGGER.info(
        "%s Lovelace resource(s) referenced by your dashboards are missing and "
        "can be restored: %s. See Settings > System > Repairs.",
        len(restorable),
        ", ".join(item.url for item in restorable),
    )

    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_ID,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="deleted_lovelace_resources",
        translation_placeholders={
            "count": str(len(restorable)),
            "resources": "\n".join(f"- `{item.url}`" for item in restorable),
        },
    )


async def async_restore_resources(hass: HomeAssistant) -> list[str]:
    """Re-register every restorable resource. Returns the URLs restored.

    Goes through the same guard as the detection, so the collection is known to
    be a writable storage collection that has been loaded - writing to an
    unloaded one would truncate the store exactly as issue #2 did.
    """
    resources = await async_get_writable_resources(hass)
    if resources is None:
        raise HomeAssistantError(
            "Lovelace resources are not available to write to. They may be in "
            "YAML mode, in which case add the cards to configuration.yaml."
        )

    restorable = await async_find_restorable_resources(hass, resources)

    restored: list[str] = []
    for item in restorable:
        await resources.async_create_item({"url": item.url, "res_type": "module"})
        _LOGGER.info("Restored Lovelace resource: %s", item.url)
        restored.append(item.url)

    ir.async_delete_issue(hass, DOMAIN, ISSUE_ID)
    return restored
