"""Automatic exposure of entities added after setup."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later

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
from .permissions import resolve_target
from .tool_sync import async_sync_tools

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


async def _synchronise(hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry) -> None:
    """Push the widened allowlist to the agent."""
    try:
        await async_sync_tools(hass, entry, entry.runtime_data.client)
    except ElevenLabsError:
        _LOGGER.warning(
            "Automatic exposure could not reach ElevenLabs; the next sync retries"
        )


def async_track_new_entities(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry
) -> CALLBACK_TYPE:
    """Add entities registered later to the sets their switches allow."""
    pending: set[str] = set()
    cancel_timer: CALLBACK_TYPE | None = None

    @callback
    def _flush(_now: Any) -> None:
        """Move the collected entities into the permission lists, once."""
        nonlocal cancel_timer
        cancel_timer = None
        entity_ids = set(pending)
        pending.clear()
        if not entity_ids:
            return

        options = dict(entry.options)
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
            _LOGGER.debug(
                "Automatic exposure added %s to %s", sorted(joining), list_key
            )

        if changed:
            hass.config_entries.async_update_entry(entry, options=options)
            entry.async_create_task(hass, _synchronise(hass, entry))

    @callback
    def _registered(event: Any) -> None:
        """Collect a newly registered entity and arm the debounce."""
        nonlocal cancel_timer
        if event.data.get("action") != "create":
            return
        pending.add(event.data["entity_id"])
        if cancel_timer is not None:
            cancel_timer()
        cancel_timer = async_call_later(hass, AUTO_EXPOSE_DEBOUNCE, _flush)

    unsubscribe = hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _registered)

    @callback
    def _stop() -> None:
        """Drop the subscription and cancel any armed timer."""
        if cancel_timer is not None:
            cancel_timer()
        unsubscribe()

    return _stop
