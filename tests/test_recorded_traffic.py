"""
Replay of recorded agent request bodies.

Recorded during the spike on 10 September 2026, and RECORDED_DATA on
22 October 2026, after the rename to ha_perform_action and data.
Do not edit these payloads to make a test pass: they are evidence.
"""

from __future__ import annotations

import pytest

from custom_components.elevenagents.const import (
    OPT_CONTROL_ENTITIES,
    OPT_EXCEPTIONS,
    OPT_READ_ENTITIES,
    OUTCOME_DONE,
    OUTCOME_FAILED,
)
from custom_components.elevenagents.webhook import async_read, async_write

RECORDED_READ_ONE = {"tool": "get_state", "entity_id": ["light.kitchen_lights"]}

RECORDED_READ_MANY = {
    "tool": "get_state",
    "entity_id": [
        "light.bed_light",
        "light.ceiling_lights",
        "light.kitchen_lights",
        "light.office_rgbw_lights",
        "light.living_room_rgbww_lights",
        "light.entrance_color_white_lights",
    ],
}

RECORDED_TURN_ON = {
    "tool": "call_service",
    "action": "light.turn_on",
    "entity_id": "light.bed_light",
}

RECORDED_DENIED = {
    "tool": "call_service",
    "action": "cover.open_cover",
    "entity_id": "cover.garage_door",
}

RECORDED_DATA = {
    "tool": "call_service",
    "entity_id": "climate.hvac",
    "action": "climate.set_temperature",
    "data": {"temperature": 21},
}


@pytest.fixture
async def spike_house(hass, config_entry, entity_registry):
    """Recreate the entities and permissions the spike ran against."""
    entities: dict[str, str] = {}
    entity_id_to_registry_id: dict[str, str] = {}
    for domain, object_id, state in (
        ("light", "bed_light", "off"),
        ("light", "ceiling_lights", "on"),
        ("light", "kitchen_lights", "on"),
        ("light", "office_rgbw_lights", "off"),
        ("light", "living_room_rgbww_lights", "off"),
        ("light", "entrance_color_white_lights", "off"),
        ("climate", "heatpump", "heat"),
        # The development house of 22 October 2026 adds this entity.
        ("climate", "hvac", "cool"),
        ("cover", "garage_door", "closed"),
    ):
        entry = entity_registry.async_get_or_create(
            domain, "demo", object_id, suggested_object_id=object_id
        )
        # climate.set_temperature needs TARGET_TEMPERATURE; cover.open_cover
        # needs OPEN. Zero would make every write fail feature matching and
        # the denial test would pass for the wrong reason.
        features = {"climate": 1, "cover": 1}.get(domain, 0)
        hass.states.async_set(entry.entity_id, state, {"supported_features": features})
        entities[f"{domain}.{object_id}"] = entry.id
        entity_id_to_registry_id[entry.entity_id] = entry.id

    controlled = [
        entities[key]
        for key in entities
        if key.startswith(("light.", "climate.", "cover."))
    ]
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            OPT_READ_ENTITIES: list(entities.values()),
            OPT_CONTROL_ENTITIES: controlled,
            # The spike permitted no cover service.
            OPT_EXCEPTIONS: {
                entities["cover.garage_door"]: ["cover.open_cover", "cover.close_cover"]
            },
        },
    )
    # Store mapping so tests can look up registry IDs by the actual entity_ids
    return entity_id_to_registry_id


async def test_recorded_single_read(hass, config_entry, spike_house):
    """The agent's one-entity read still resolves."""
    body = await async_read(hass, config_entry, RECORDED_READ_ONE)

    assert len(body["results"]) == 1
    assert body["results"][0]["entity_id"] == "light.kitchen_lights"
    assert body["results"][0]["state"] == "on"


async def test_recorded_six_entity_read(hass, config_entry, spike_house):
    """Asked whether any light is on, the agent listed all six in one call."""
    body = await async_read(hass, config_entry, RECORDED_READ_MANY)

    assert len(body["results"]) == 6
    assert all("state" in result for result in body["results"])


async def test_recorded_turn_on(hass, config_entry, spike_house):
    """The permitted write the spike verified against real Home Assistant."""

    async def turn_on(call) -> None:
        hass.states.async_set("light.bed_light", "on", {"supported_features": 0})

    hass.services.async_register("light", "turn_on", turn_on)

    body = await async_write(hass, config_entry, RECORDED_TURN_ON)

    assert body["outcome"] == OUTCOME_DONE
    assert body["state"] == "on"


async def test_recorded_denied_write_leaves_the_entity_alone(
    hass, config_entry, spike_house
):
    """cover.garage_door stayed closed in the spike. It stays closed here."""
    called: list[str] = []

    async def open_cover(call) -> None:
        called.append("open")
        hass.states.async_set("cover.garage_door", "open", {"supported_features": 0})

    hass.services.async_register("cover", "open_cover", open_cover)

    body = await async_write(hass, config_entry, RECORDED_DENIED)

    assert body["outcome"] == OUTCOME_FAILED
    assert body["error"] == "action_not_allowed"
    assert called == []
    assert hass.states.get("cover.garage_door").state == "closed"


async def test_recorded_data(hass, config_entry, spike_house):
    """The agent nests the parameters under data and picks the key."""

    async def set_temperature(call) -> None:
        hass.states.async_set(
            "climate.hvac",
            "cool",
            {"supported_features": 0, "temperature": call.data["temperature"]},
        )

    hass.services.async_register("climate", "set_temperature", set_temperature)

    body = await async_write(hass, config_entry, RECORDED_DATA)

    assert body["outcome"] == OUTCOME_DONE
    assert body["temperature"] == 21
