"""Tests for the diagnostics download."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from homeassistant.components.diagnostics import REDACTED
from pytest_homeassistant_custom_component.components.diagnostics import (
    _get_diagnostics_for_config_entry,
    get_diagnostics_for_config_entry,
)

from custom_components.elevenagents.const import (
    CONF_AGENT_ID,
    CONF_API_KEY,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    OPT_AUTO_EXPOSE_AREAS,
    OPT_CONTROL_ENTITIES,
    OPT_EXCEPTIONS,
    OPT_PUBLIC_URL,
    OPT_READ_ENTITIES,
    OPT_SENSITIVE,
)
from custom_components.elevenagents.diagnostics import (
    async_get_config_entry_diagnostics,
)

if TYPE_CHECKING:
    from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

# homeassistant 2026.9.1's http server sets self.app["hass"] as a plain key.
# aiohttp 3.14.3 raises NotAppKeyWarning for that pattern. pyproject.toml
# turns every warning into an error, so this module ignores this one category.
pytestmark = pytest.mark.filterwarnings(
    "ignore::aiohttp.web_exceptions.NotAppKeyWarning"
)

SECRETS = ("sk_test", "agent_test", "webhook_test", "secret_test")


async def test_no_secret_reaches_the_download(
    hass, setup_entry, hass_client: ClientSessionGenerator
) -> None:
    """A diagnostics file is posted in public issues."""
    whole = await _get_diagnostics_for_config_entry(hass, hass_client, setup_entry)

    text = json.dumps(whole)
    for secret in SECRETS:
        assert secret not in text


async def test_the_secret_fields_are_marked_redacted(
    hass, setup_entry, hass_client: ClientSessionGenerator
) -> None:
    """The reader sees which fields exist, not their values."""
    data = await get_diagnostics_for_config_entry(hass, hass_client, setup_entry)

    stored = data["entry"]["data"]
    for key in (CONF_API_KEY, CONF_AGENT_ID, CONF_WEBHOOK_ID, CONF_WEBHOOK_SECRET):
        assert stored[key] == REDACTED


async def test_the_public_url_is_marked_redacted(
    hass, setup_entry, hass_client: ClientSessionGenerator
) -> None:
    """A proxy URL names the user's domain."""
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_PUBLIC_URL: "https://hooks.example.com"}
    )

    whole = await _get_diagnostics_for_config_entry(hass, hass_client, setup_entry)

    assert "hooks.example.com" not in json.dumps(whole)
    assert whole["data"]["entry"]["options"][OPT_PUBLIC_URL] == REDACTED


async def test_the_summary_counts_permissions_and_what_is_missing(
    hass, setup_entry, entity_registry, hass_client: ClientSessionGenerator
) -> None:
    """Support needs the shape of the configuration, not the house."""
    light = entity_registry.async_get_or_create("light", "demo", "one")
    hass.states.async_set("lock.yaml_present", "locked")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={
            OPT_READ_ENTITIES: [light.id, "sensor.yaml_gone"],
            OPT_CONTROL_ENTITIES: ["lock.yaml_present"],
            OPT_EXCEPTIONS: {"lock.yaml_present": ["lock.unlock"]},
        },
    )
    setup_entry.runtime_data.catalogue = (["light.demo_one"], "light.demo_one = x")

    data = await get_diagnostics_for_config_entry(hass, hass_client, setup_entry)

    assert data["permissions"] == {
        "read": 2,
        "control": 1,
        "restricted": 1,
        "missing": 1,
    }
    assert data["sync"] == {"catalogue_sent": True, "entities": 1}


async def test_no_entity_or_area_name_reaches_the_download(
    hass, setup_entry, entity_registry, hass_client: ClientSessionGenerator
) -> None:
    """Entity and area names describe the user's home."""
    light = entity_registry.async_get_or_create("light", "demo", "bedside")
    hass.states.async_set("lock.yaml_front_door", "locked")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={
            OPT_READ_ENTITIES: [light.id, "sensor.yaml_gone_garden"],
            OPT_CONTROL_ENTITIES: ["lock.yaml_front_door"],
            OPT_EXCEPTIONS: {"lock.yaml_front_door": ["lock.unlock"]},
            OPT_SENSITIVE: {"lock.yaml_front_door": {"conversation": True}},
            OPT_AUTO_EXPOSE_AREAS: ["guest_bedroom"],
        },
    )
    setup_entry.runtime_data.catalogue = (["light.demo_bedside"], "x")

    whole = await _get_diagnostics_for_config_entry(hass, hass_client, setup_entry)

    text = json.dumps(whole)
    for name in (light.id, "bedside", "front_door", "garden", "guest_bedroom"):
        assert name not in text


async def test_no_entity_state_reaches_the_download(
    hass, setup_entry, hass_client: ClientSessionGenerator
) -> None:
    """A lock's state or an alarm's code is nobody else's business."""
    hass.states.async_set("lock.front", "unlocked", {"code_format": "1234"})
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_CONTROL_ENTITIES: ["lock.front"]}
    )

    whole = await _get_diagnostics_for_config_entry(hass, hass_client, setup_entry)

    text = json.dumps(whole)
    assert "unlocked" not in text
    assert "1234" not in text


async def test_an_unloaded_entry_still_answers(hass, setup_entry) -> None:
    """The entry is in trouble precisely when someone downloads this."""
    await hass.config_entries.async_unload(setup_entry.entry_id)

    data = await async_get_config_entry_diagnostics(hass, setup_entry)

    assert data["sync"] == {"catalogue_sent": False, "entities": None}
    assert data["entry"]["state"] == "not_loaded"
