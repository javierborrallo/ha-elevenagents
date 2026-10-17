"""Describe ElevenAgents action events in the logbook."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import callback

from .const import DOMAIN, EVENT_ACTION

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import Event, HomeAssistant


@callback
def async_describe_events(
    _hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, Any]]], None],
) -> None:
    """Register the description of EVENT_ACTION."""

    @callback
    def async_describe_action(event: Event) -> dict[str, Any]:
        """Describe one action event."""
        data = event.data
        action = data.get("action")
        outcome = data.get("outcome")
        error = data.get("error")
        suffix = f" ({error})" if error else ""
        if action is None or outcome is None:
            message = f"rejected a malformed request{suffix}"
        else:
            message = f"{action}: {outcome}{suffix}"
        return {
            LOGBOOK_ENTRY_NAME: "ElevenAgents",
            LOGBOOK_ENTRY_ENTITY_ID: data.get("entity_id"),
            LOGBOOK_ENTRY_MESSAGE: message,
        }

    async_describe_event(DOMAIN, EVENT_ACTION, async_describe_action)
