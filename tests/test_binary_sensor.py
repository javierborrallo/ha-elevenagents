"""Tests for the status sensors."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.elevenlabs_agent.binary_sensor import OVERALL, CheckSensor
from custom_components.elevenlabs_agent.check import CHECKS
from custom_components.elevenlabs_agent.const import DOMAIN
from custom_components.elevenlabs_agent.status import check_signal

# homeassistant 2026.9.1's http server sets self.app["hass"] as a plain key.
# aiohttp 3.14.3 raises NotAppKeyWarning for that pattern. pyproject.toml
# turns every warning into an error, so this module ignores this one category.
pytestmark = pytest.mark.filterwarnings(
    "ignore::aiohttp.web_exceptions.NotAppKeyWarning"
)

KEYS = (OVERALL, *CHECKS)


def _result(**statuses: str) -> dict[str, Any]:
    """Build a check result. Every check not named is ok."""
    checks = [
        {"check": name, "status": statuses.get(name, "ok"), "detail": None}
        for name in CHECKS
    ]
    return {
        "ok": all(item["status"] != "problem" for item in checks),
        "checks": checks,
    }


def _entity_id(hass, entry, key: str) -> str:
    """Return the entity_id of one sensor through its unique_id."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id is not None
    return entity_id


def _state(hass, entry, key: str) -> str:
    """Return the state of one sensor."""
    state = hass.states.get(_entity_id(hass, entry, key))
    assert state is not None
    return str(state.state)


async def _announce(hass, entry, result: dict[str, Any]) -> None:
    """Store a result and announce it, as a check does."""
    entry.runtime_data.last_check = result
    async_dispatcher_send(hass, check_signal(entry.entry_id))
    await hass.async_block_till_done()


async def test_every_sensor_is_unknown_before_the_first_check(hass, setup_entry):
    """Nothing is claimed before anything is checked."""
    for key in KEYS:
        assert _state(hass, setup_entry, key) == STATE_UNKNOWN


async def test_a_passing_check_turns_every_sensor_off(hass, setup_entry):
    """Off is working for the problem device class."""
    await _announce(hass, setup_entry, _result())

    for key in KEYS:
        assert _state(hass, setup_entry, key) == STATE_OFF


@pytest.mark.parametrize("check", [c for c in CHECKS if c != "webhook"])
async def test_a_problem_turns_its_sensor_and_the_agent_on(hass, setup_entry, check):
    """One failing check shows on its own sensor and on the agent."""
    await _announce(hass, setup_entry, _result(**{check: "problem"}))

    assert _state(hass, setup_entry, check) == STATE_ON
    assert _state(hass, setup_entry, OVERALL) == STATE_ON
    for other in CHECKS:
        if other != check:
            assert _state(hass, setup_entry, other) == STATE_OFF


async def test_a_webhook_problem_alone_leaves_the_agent_off(hass, setup_entry):
    """The webhook probe can fail behind a router that ElevenLabs gets through."""
    await _announce(hass, setup_entry, _result(webhook="problem"))

    assert _state(hass, setup_entry, "webhook") == STATE_ON
    assert _state(hass, setup_entry, OVERALL) == STATE_OFF


async def test_a_webhook_problem_does_not_hide_another_one(hass, setup_entry):
    """Any other problem still turns the agent on."""
    await _announce(hass, setup_entry, _result(webhook="problem", key="problem"))

    assert _state(hass, setup_entry, OVERALL) == STATE_ON


async def test_a_skipped_check_is_unknown_not_off(hass, setup_entry):
    """An OK nobody checked would be a lie."""
    await _announce(
        hass,
        setup_entry,
        _result(
            key="problem",
            protected="skipped",
            tools_attached="skipped",
            tools_current="skipped",
        ),
    )

    assert _state(hass, setup_entry, "key") == STATE_ON
    for check in ("protected", "tools_attached", "tools_current"):
        assert _state(hass, setup_entry, check) == STATE_UNKNOWN


async def test_the_entity_lists_are_attributes(hass, setup_entry):
    """An automation reads the entities the agent lacks."""
    result = _result(tools_current="problem")
    detail = {
        "tools": ["ha_get_state"],
        "missing": ["light.a"],
        "extra": ["light.b"],
        "unreadable": [],
    }
    for item in result["checks"]:
        if item["check"] == "tools_current":
            item["detail"] = detail
    await _announce(hass, setup_entry, result)

    state = hass.states.get(_entity_id(hass, setup_entry, "tools_current"))
    assert state is not None
    for key, value in detail.items():
        assert state.attributes[key] == value


async def test_a_plain_detail_is_one_attribute(hass, setup_entry):
    """A list or a string travels as detail."""
    result = _result(tools_attached="problem")
    for item in result["checks"]:
        if item["check"] == "tools_attached":
            item["detail"] = ["ha_call_service"]
    await _announce(hass, setup_entry, result)

    state = hass.states.get(_entity_id(hass, setup_entry, "tools_attached"))
    assert state is not None
    assert state.attributes["detail"] == ["ha_call_service"]


async def test_the_sensors_share_one_service_device(hass, setup_entry):
    """Eight sensors under one device for the agent."""
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, setup_entry.entry_id), setup_entry.entry_id
    )
    assert device is not None
    assert device.entry_type is dr.DeviceEntryType.SERVICE
    assert device.manufacturer == "ElevenLabs"
    assert device.name == setup_entry.title

    entries = er.async_entries_for_config_entry(
        er.async_get(hass), setup_entry.entry_id
    )
    assert len(entries) == len(KEYS)
    assert {entry.device_id for entry in entries} == {device.id}


async def test_only_the_agent_sensor_is_not_diagnostic(hass, setup_entry):
    """The agent sensor is the one a dashboard shows by default."""
    registry = er.async_get(hass)
    for key in KEYS:
        entry = registry.async_get(_entity_id(hass, setup_entry, key))
        assert entry is not None
        expected = None if key == OVERALL else EntityCategory.DIAGNOSTIC
        assert entry.entity_category == expected


def test_the_check_sensor_excludes_its_entity_lists_from_the_recorder():
    """Missing and extra can reach several kilobytes on a large home."""
    assert CheckSensor._unrecorded_attributes == frozenset(  # noqa: SLF001
        {"tools", "missing", "extra", "unreadable", "detail"}
    )


async def test_an_unloaded_entry_leaves_every_sensor_unavailable(hass, setup_entry):
    """Loaded reads as available or unavailable."""
    await _announce(hass, setup_entry, _result())

    await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()

    for key in KEYS:
        assert _state(hass, setup_entry, key) == STATE_UNAVAILABLE
