"""Tests for the logbook description of an action event."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import Event

from custom_components.elevenagents.const import DOMAIN, EVENT_ACTION
from custom_components.elevenagents.logbook import async_describe_events


def _describe(hass, **data: Any) -> dict[str, Any]:
    """Register the describer and run it on a fake action event."""
    registered: list[tuple[str, str, Any]] = []
    async_describe_events(
        hass, lambda domain, name, describe: registered.append((domain, name, describe))
    )
    assert [(domain, name) for domain, name, _ in registered] == [
        (DOMAIN, EVENT_ACTION)
    ]
    event = Event(EVENT_ACTION, data)
    described: dict[str, Any] = registered[0][2](event)
    return described


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            {"action": "light.turn_on", "outcome": "done", "error": None},
            "light.turn_on: done",
        ),
        (
            {
                "action": "lock.open",
                "outcome": "failed",
                "error": "action_not_allowed",
            },
            "lock.open: failed (action_not_allowed)",
        ),
        (
            {"action": None, "outcome": None, "error": "malformed_request"},
            "rejected a malformed request (malformed_request)",
        ),
    ],
)
def test_the_describer_words_the_three_shapes(hass, data, message):
    """No error, an error, and a malformed request."""
    described = _describe(hass, entity_id="lock.front", conversation_id=None, **data)

    assert described == {
        LOGBOOK_ENTRY_NAME: "ElevenAgents",
        LOGBOOK_ENTRY_ENTITY_ID: "lock.front",
        LOGBOOK_ENTRY_MESSAGE: message,
    }


def test_the_describer_accepts_an_event_with_no_entity(hass):
    """A malformed request can carry no entity."""
    described = _describe(
        hass,
        entity_id=None,
        action=None,
        outcome=None,
        error="malformed_request",
        conversation_id=None,
    )

    assert described[LOGBOOK_ENTRY_ENTITY_ID] is None
