"""Automatic exposure, pruning, catalogue refresh and missing entities."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.helpers.start import async_at_started

from .const import (
    AUTO_EXPOSE_DEBOUNCE,
    OPT_AUTO_CONTROL,
    OPT_AUTO_EXPOSE,
    OPT_AUTO_EXPOSE_AREAS,
    OPT_CONTROL_ENTITIES,
    OPT_CONTROL_TARGET,
    OPT_READ_ENTITIES,
    OPT_READ_TARGET,
)
from .elevenlabs_client import ElevenLabsError
from .permissions import (
    from_options,
    removed_registry_keys,
    resolve_target,
    stored_keys,
    without_keys,
)
from .repairs import async_check_missing
from .tool_sync import ToolSyncError, async_sync_tools, entity_catalogue

if TYPE_CHECKING:
    from . import ElevenLabsAgentConfigEntry

_LOGGER = logging.getLogger(__name__)


def _watched_target(options: dict[str, Any], target_key: str) -> dict[str, Any]:
    """
    Return the target a switch watches.

    OPT_AUTO_EXPOSE_AREAS wins whenever it is present, even next to a
    stored target. Its absence falls back to the switch's own target.
    """
    if OPT_AUTO_EXPOSE_AREAS in options:
        return {"area_id": list(options[OPT_AUTO_EXPOSE_AREAS])}
    return options.get(target_key) or {}


def _permission_keys(hass: HomeAssistant, entity_ids: set[str]) -> set[str]:
    """Return the permission key of each entity: its own entity registry id."""
    registry = er.async_get(hass)
    keys: set[str] = set()
    for entity_id in entity_ids:
        registry_entry = registry.async_get(entity_id)
        if registry_entry is not None:
            keys.add(registry_entry.id)
    return keys


def _grant_new(
    hass: HomeAssistant, options: dict[str, Any], entity_ids: set[str]
) -> bool:
    """
    Add new entities to the lists their switches allow.

    Change options in place. Return True when it added an entity.
    """
    changed = False
    registered = _permission_keys(hass, entity_ids)
    for switch, target_key, list_key in (
        (OPT_AUTO_EXPOSE, OPT_READ_TARGET, OPT_READ_ENTITIES),
        (OPT_AUTO_CONTROL, OPT_CONTROL_TARGET, OPT_CONTROL_ENTITIES),
    ):
        if not options.get(switch, False):
            continue
        target = _watched_target(options, target_key)
        joining = registered & resolve_target(hass, target)
        if not joining:
            continue
        options[list_key] = sorted(set(options.get(list_key, ())) | joining)
        changed = True
        _LOGGER.debug("Automatic exposure added %s to %s", sorted(joining), list_key)
    return changed


def _catalogue_changed(hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry) -> bool:
    """Return True when the catalogue differs from the one the last sync sent."""
    perms = from_options(entry.options)
    current = entity_catalogue(hass, perms.read | perms.control)
    return current != entry.runtime_data.catalogue


def _prune(hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry) -> None:
    """Remove the permissions of deleted registry entities."""
    removed = removed_registry_keys(hass, entry.options)
    if not removed:
        return
    hass.config_entries.async_update_entry(
        entry, options=without_keys(entry.options, removed)
    )
    _LOGGER.debug("Removed the permissions of deleted entities %s", sorted(removed))


async def _synchronise(hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry) -> None:
    """Push the current allowlist and catalogue to the agent."""
    try:
        await async_sync_tools(hass, entry, entry.runtime_data.client)
    except ElevenLabsError, ToolSyncError, NoURLAvailableError:
        _LOGGER.warning(
            "A registry change could not reach ElevenLabs; the next sync retries"
        )


def async_track_new_entities(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry
) -> CALLBACK_TYPE:
    """
    Follow the entity, device and area registries.

    Remove the permissions of deleted registry entities, add new entities
    to the sets their switches allow, and synchronise when automatic
    exposure wrote or the catalogue changed. Also watch the state of each
    stored entity_id key, and check for missing entities once Home Assistant
    has started.
    """
    pending: set[str] = set()
    cancel_timer: CALLBACK_TYPE | None = None

    _prune(hass, entry)

    @callback
    def _flush(_now: Any) -> None:
        """Prune, grant, then synchronise when anything the agent sees changed."""
        nonlocal cancel_timer
        cancel_timer = None
        entity_ids = set(pending)
        pending.clear()

        _prune(hass, entry)
        options = dict(entry.options)
        changed = _grant_new(hass, options, entity_ids)

        if options != dict(entry.options):
            hass.config_entries.async_update_entry(entry, options=options)
        if changed or _catalogue_changed(hass, entry):
            entry.async_create_task(hass, _synchronise(hass, entry))
        async_check_missing(hass, entry)

    @callback
    def _arm(_event: Any = None) -> None:
        """Restart the debounce."""
        nonlocal cancel_timer
        if cancel_timer is not None:
            cancel_timer()
        cancel_timer = async_call_later(hass, AUTO_EXPOSE_DEBOUNCE, _flush)

    @callback
    def _entity_event(event: Any) -> None:
        """Collect the new entity, then restart the debounce."""
        if event.data["action"] == "create":
            pending.add(event.data["entity_id"])
        _arm()

    @callback
    def _appears_or_vanishes(event_data: Any) -> bool:
        """Return True when a stored entity_id key gains or loses its state."""
        if event_data["old_state"] is not None and event_data["new_state"] is not None:
            return False
        entity_id = event_data["entity_id"]
        return "." in entity_id and entity_id in stored_keys(entry.options)

    @callback
    def _started(_hass: HomeAssistant) -> None:
        """Restart the debounce once Home Assistant has started."""
        _arm()

    unsubscribes = [
        async_at_started(hass, _started),
        hass.bus.async_listen(
            EVENT_STATE_CHANGED, _arm, event_filter=_appears_or_vanishes
        ),
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _entity_event),
        hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, _arm),
        hass.bus.async_listen(ar.EVENT_AREA_REGISTRY_UPDATED, _arm),
    ]

    @callback
    def _stop() -> None:
        """Drop the subscriptions and cancel any armed timer."""
        if cancel_timer is not None:
            cancel_timer()
        for unsubscribe in unsubscribes:
            unsubscribe()

    return _stop
