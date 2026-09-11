"""Tests for tool schema generation and workspace synchronisation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest

from custom_components.elevenlabs_agent.const import (
    CONF_TOOL_IDS,
    TOOL_CALL_SERVICE,
    TOOL_GET_STATE,
)
from custom_components.elevenlabs_agent.elevenlabs_client import ElevenLabsNotFoundError
from custom_components.elevenlabs_agent.permissions import Permissions
from custom_components.elevenlabs_agent.tool_sync import (
    CALL_SERVICE_DESCRIPTION,
    GET_STATE_DESCRIPTION,
    ToolSyncError,
    _check_readback,
    _flatten_fields,
    async_sync_tools,
    build_call_service,
    build_get_state,
    entity_catalogue,
    service_data_properties,
)

if TYPE_CHECKING:
    from elevenlabs import AsyncElevenLabs

# homeassistant 2026.9.1's http server sets self.app["hass"] as a plain key.
# aiohttp 3.14.3 raises NotAppKeyWarning for that pattern. pyproject.toml
# turns every warning into an error, so this module ignores this one category.
pytestmark = pytest.mark.filterwarnings(
    "ignore::aiohttp.web_exceptions.NotAppKeyWarning"
)

# The client wrappers are patched by the tool_calls fixture, so the value
# passed as the client argument is never used for anything but identity.
_CLIENT = cast("AsyncElevenLabs", object())

DESCRIPTIONS = {
    "climate": {
        "set_temperature": {
            "target": {"entity": [{"domain": ["climate"]}]},
            "fields": {
                # No field carries a description on a real instance.
                # Verified 11 September 2026 against the running instance.
                "temperature": {"selector": {"number": {"min": 7, "max": 35}}},
                "hvac_mode": {"selector": {"select": {"options": ["off", "heat"]}}},
                "target": {"selector": {"entity": {}}},
            },
        }
    },
    "light": {
        "turn_on": {
            "target": {"entity": [{"domain": ["light"]}]},
            "fields": {
                "brightness_pct": {"selector": {"number": {"min": 0, "max": 100}}}
            },
        }
    },
}


# --- Task 11: schema generation ---------------------------------------------


async def test_entity_catalogue_pairs_the_enum_with_readable_lines(
    hass, entity_registry, area_registry
):
    """The description carries the mapping from spoken name to entity_id."""
    area = area_registry.async_create("Salon")
    entry = entity_registry.async_get_or_create("light", "demo", "salon")
    entity_registry.async_update_entity(entry.entity_id, area_id=area.id)
    hass.states.async_set(entry.entity_id, "on", {"friendly_name": "Lampara"})

    enum, description = entity_catalogue(hass, {entry.id})

    assert enum == [entry.entity_id]
    assert f"{entry.entity_id} = Lampara (Salon)" in description


async def test_entity_with_no_area_has_no_empty_parenthesis(hass, entity_registry):
    """An unassigned entity is written without a trailing empty bracket."""
    entry = entity_registry.async_get_or_create("light", "demo", "loose")
    hass.states.async_set(entry.entity_id, "on", {"friendly_name": "Loose"})

    _, description = entity_catalogue(hass, {entry.id})

    assert f"{entry.entity_id} = Loose" in description
    assert "()" not in description


def test_service_data_carries_types_and_no_constraints():
    """No minimum, no maximum, no enum. Home Assistant validates values."""
    translations = {
        "component.climate.services.set_temperature.fields.temperature.description": (
            "Target temperature"
        ),
        "component.climate.services.set_temperature.fields.hvac_mode.description": (
            "HVAC operation mode."
        ),
        "component.light.services.turn_on.fields.brightness_pct.description": (
            "Brightness percentage"
        ),
    }
    properties = service_data_properties(
        DESCRIPTIONS, {"climate.set_temperature", "light.turn_on"}, translations
    )

    assert properties["temperature"]["type"] == "number"
    assert "minimum" not in properties["temperature"]
    assert "description" in properties["temperature"]

    # A select becomes a plain string. Options are not copied. The text
    # comes from the translations mapping, never from the field.
    assert properties["hvac_mode"] == {
        "type": "string",
        "description": "HVAC operation mode.",
    }

    # A non-scalar selector is omitted entirely.
    assert "target" not in properties


def test_colliding_keys_with_different_types_are_omitted():
    """Guessing wrong sends a schema that always fails validation."""
    descriptions = {
        "a": {
            "one": {
                "target": {"entity": [{"domain": ["a"]}]},
                "fields": {"value": {"description": "n", "selector": {"number": {}}}},
            }
        },
        "b": {
            "two": {
                "target": {"entity": [{"domain": ["b"]}]},
                "fields": {"value": {"description": "s", "selector": {"text": {}}}},
            }
        },
    }

    properties = service_data_properties(descriptions, {"a.one", "b.two"}, {})

    assert "value" not in properties


def test_field_description_comes_from_translations_not_the_field():
    """
    Every field of a real service returns description=None.

    Verified 11 September 2026 against the running instance. A field's own
    "description" key, when present in a test fixture, must still be
    ignored in favour of the translations mapping.
    """
    descriptions = {
        "light": {
            "turn_on": {
                "target": {"entity": [{"domain": ["light"]}]},
                "fields": {
                    "brightness_pct": {
                        "description": "wrong, from the field itself",
                        "selector": {"number": {"min": 0, "max": 100}},
                    }
                },
            }
        }
    }
    translations = {
        "component.light.services.turn_on.fields.brightness_pct.description": (
            "Brightness percentage"
        )
    }

    properties = service_data_properties(descriptions, {"light.turn_on"}, translations)

    assert properties["brightness_pct"]["description"] == "Brightness percentage"


def test_flatten_fields_descends_into_a_section():
    """A field entry with fields and no selector is a section, not a leaf."""
    fields = {
        "temperature_range": {
            "fields": {
                "target_temp_high": {"selector": {"number": {}}},
                "target_temp_low": {"selector": {"number": {}}},
            }
        },
        "hvac_mode": {"selector": {"select": {"options": ["off", "heat"]}}},
    }

    flat = _flatten_fields(fields)

    assert set(flat) == {"target_temp_high", "target_temp_low", "hvac_mode"}


def test_service_data_properties_descends_into_a_section():
    """
    climate.set_temperature's temperature_range section is not dropped.

    A flat iteration over fields sees temperature_range as one field with
    no selector, treats it as non-scalar and drops both of its children
    silently.
    """
    descriptions = {
        "climate": {
            "set_temperature": {
                "target": {"entity": [{"domain": ["climate"]}]},
                "fields": {
                    "temperature_range": {
                        "fields": {
                            "target_temp_high": {"selector": {"number": {}}},
                            "target_temp_low": {"selector": {"number": {}}},
                        }
                    }
                },
            }
        }
    }

    properties = service_data_properties(descriptions, {"climate.set_temperature"}, {})

    assert set(properties) == {"target_temp_high", "target_temp_low"}


async def test_call_service_schema_shape(hass, entity_registry):
    """The discriminator is a constant with no description; entity_id is a string."""
    entry = entity_registry.async_get_or_create("light", "demo", "one")
    hass.states.async_set(entry.entity_id, "on", {"supported_features": 0})

    perms = Permissions(read=frozenset(), control=frozenset({entry.id}), exceptions={})
    config = build_call_service(
        hass, perms, DESCRIPTIONS, {}, "https://example/hook", "sekrit", 20
    )

    properties = config["api_schema"]["request_body_schema"]["properties"]

    assert properties["tool"] == {"type": "string", "constant_value": "call_service"}
    assert properties["entity_id"]["type"] == "string"
    assert "light.turn_on" in properties["action"]["enum"]
    assert config["response_timeout_secs"] == 20
    headers = config["api_schema"]["request_headers"]
    assert headers["X-Elevenlabs-Agent-Secret"] == "sekrit"


async def test_get_state_schema_shape(hass, entity_registry):
    """entity_id is an array; its items carry the enum and a description."""
    entry = entity_registry.async_get_or_create("light", "demo", "one")
    hass.states.async_set(entry.entity_id, "on", {"friendly_name": "One"})

    perms = Permissions(read=frozenset({entry.id}), control=frozenset(), exceptions={})
    config = build_get_state(hass, perms, "https://example/hook", "sekrit", 20)

    schema = config["api_schema"]["request_body_schema"]
    properties = schema["properties"]

    assert schema["required"] == ["entity_id"]
    assert properties["tool"] == {"type": "string", "constant_value": "get_state"}
    assert "description" not in properties["tool"]
    assert properties["entity_id"]["type"] == "array"
    assert "description" in properties["entity_id"]
    assert properties["entity_id"]["items"]["enum"] == [entry.entity_id]
    assert "description" in properties["entity_id"]["items"]


async def test_get_state_reads_read_and_control_call_service_reads_control_only(
    hass, entity_registry
):
    """build_get_state offers read | control; call_service offers control alone."""
    readable = entity_registry.async_get_or_create("light", "demo", "readable")
    controllable = entity_registry.async_get_or_create("light", "demo", "controllable")
    hass.states.async_set(readable.entity_id, "on", {"supported_features": 0})
    hass.states.async_set(controllable.entity_id, "on", {"supported_features": 0})

    perms = Permissions(
        read=frozenset({readable.id}),
        control=frozenset({controllable.id}),
        exceptions={},
    )

    get_state = build_get_state(hass, perms, "https://example/hook", "sekrit", 20)
    call_service = build_call_service(
        hass, perms, DESCRIPTIONS, {}, "https://example/hook", "sekrit", 20
    )

    get_state_entities = get_state["api_schema"]["request_body_schema"]["properties"][
        "entity_id"
    ]["items"]["enum"]
    call_service_entities = call_service["api_schema"]["request_body_schema"][
        "properties"
    ]["entity_id"]["enum"]

    assert set(get_state_entities) == {readable.entity_id, controllable.entity_id}
    assert call_service_entities == [controllable.entity_id]


# --- Task 12: synchronising, owning and attaching ---------------------------


@pytest.fixture
def tool_calls():
    """Patch the Task 10 client wrappers as module functions and echo writes back."""
    with (
        patch(
            "custom_components.elevenlabs_agent.tool_sync.async_create_tool"
        ) as create_tool,
        patch(
            "custom_components.elevenlabs_agent.tool_sync.async_update_tool"
        ) as update_tool,
        patch(
            "custom_components.elevenlabs_agent.tool_sync.async_get_tool"
        ) as get_tool,
        patch(
            "custom_components.elevenlabs_agent.tool_sync.async_get_agent"
        ) as get_agent,
        patch(
            "custom_components.elevenlabs_agent.tool_sync.async_patch_agent"
        ) as patch_agent,
    ):
        written: dict[str, dict] = {}

        async def create(_client, config) -> SimpleNamespace:
            tool_id = f"tool_{config['name']}"
            written[tool_id] = config
            return SimpleNamespace(id=tool_id, tool_config=config)

        async def update(_client, tool_id, config) -> SimpleNamespace:
            written[tool_id] = config
            return SimpleNamespace(id=tool_id, tool_config=config)

        async def get(_client, tool_id) -> SimpleNamespace:
            return SimpleNamespace(id=tool_id, tool_config=written[tool_id])

        create_tool.side_effect = create
        update_tool.side_effect = update
        get_tool.side_effect = get
        get_agent.return_value = SimpleNamespace(
            conversation_config=SimpleNamespace(
                agent=SimpleNamespace(
                    prompt=SimpleNamespace(tool_ids=["tool_user_made"])
                )
            )
        )

        yield SimpleNamespace(
            create_tool=create_tool,
            update_tool=update_tool,
            get_tool=get_tool,
            get_agent=get_agent,
            patch_agent=patch_agent,
            written=written,
        )


async def test_sync_is_idempotent(hass, setup_entry, tool_calls):
    """Running twice produces no second create."""
    await async_sync_tools(hass, setup_entry, _CLIENT)
    first = dict(setup_entry.data[CONF_TOOL_IDS])
    tool_calls.create_tool.reset_mock()

    await async_sync_tools(hass, setup_entry, _CLIENT)

    assert tool_calls.create_tool.call_count == 0
    assert setup_entry.data[CONF_TOOL_IDS] == first


async def test_attaching_preserves_tools_the_user_added(hass, setup_entry, tool_calls):
    """The PATCH is a union, never an overwrite."""
    await async_sync_tools(hass, setup_entry, _CLIENT)

    conversation_config = tool_calls.patch_agent.call_args.kwargs["conversation_config"]
    attached = conversation_config["agent"]["prompt"]["tool_ids"]

    assert "tool_user_made" in attached
    assert set(setup_entry.data[CONF_TOOL_IDS].values()) <= set(attached)


async def test_attaching_never_drops_a_tool_this_integration_synced_before(
    hass, setup_entry, tool_calls
):
    """A second sync's PATCH still carries every id this integration owns."""
    await async_sync_tools(hass, setup_entry, _CLIENT)
    owned = set(setup_entry.data[CONF_TOOL_IDS].values())

    await async_sync_tools(hass, setup_entry, _CLIENT)

    conversation_config = tool_calls.patch_agent.call_args.kwargs["conversation_config"]
    attached = conversation_config["agent"]["prompt"]["tool_ids"]

    assert owned <= set(attached)


async def test_a_deleted_tool_is_recreated_not_an_error(hass, setup_entry, tool_calls):
    """404 on update means the user deleted it from the dashboard."""
    hass.config_entries.async_update_entry(
        setup_entry,
        data={**setup_entry.data, CONF_TOOL_IDS: {TOOL_GET_STATE: "tool_gone"}},
    )

    async def update(_client, tool_id, config) -> SimpleNamespace:
        if tool_id == "tool_gone":
            message = "gone"
            raise ElevenLabsNotFoundError(message)
        tool_calls.written[tool_id] = config
        return SimpleNamespace(id=tool_id, tool_config=config)

    tool_calls.update_tool.side_effect = update

    await async_sync_tools(hass, setup_entry, _CLIENT)

    assert setup_entry.data[CONF_TOOL_IDS][TOOL_GET_STATE] != "tool_gone"


async def test_read_back_divergence_raises(hass, setup_entry, tool_calls):
    """A 200 proves acceptance, never effect."""

    async def get(_client, tool_id) -> SimpleNamespace:
        return SimpleNamespace(id=tool_id, tool_config={"name": "something else"})

    tool_calls.get_tool.side_effect = get

    with pytest.raises(ToolSyncError):
        await async_sync_tools(hass, setup_entry, _CLIENT)


async def test_the_id_is_stored_even_when_the_readback_diverges(
    hass, setup_entry, tool_calls
):
    """
    The id is stored before the write is verified.

    A tool that exists in the workspace and whose id is not stored can
    never be reclaimed, because the ownership rule creates a new one when
    no id is found.
    """

    async def get(_client, tool_id) -> SimpleNamespace:
        return SimpleNamespace(id=tool_id, tool_config={"name": "something else"})

    tool_calls.get_tool.side_effect = get

    with pytest.raises(ToolSyncError):
        await async_sync_tools(hass, setup_entry, _CLIENT)

    assert setup_entry.data[CONF_TOOL_IDS].get(TOOL_GET_STATE) is not None


async def test_response_timeout_secs_is_not_compared(hass, setup_entry, tool_calls):
    """The API may return a different timeout; only the sent fields are compared."""

    async def get(_client, tool_id) -> SimpleNamespace:
        sent = tool_calls.written[tool_id]
        changed_timeout = sent["response_timeout_secs"] + 1
        changed = {**sent, "response_timeout_secs": changed_timeout}
        return SimpleNamespace(id=tool_id, tool_config=changed)

    tool_calls.get_tool.side_effect = get

    await async_sync_tools(hass, setup_entry, _CLIENT)


async def test_sync_never_touches_a_tool_id_it_does_not_own(
    hass, setup_entry, tool_calls
):
    """A key with a name this integration does not use stays untouched."""
    hass.config_entries.async_update_entry(
        setup_entry,
        data={**setup_entry.data, CONF_TOOL_IDS: {"unrelated": "tool_other"}},
    )

    await async_sync_tools(hass, setup_entry, _CLIENT)

    assert setup_entry.data[CONF_TOOL_IDS]["unrelated"] == "tool_other"
    assert setup_entry.data[CONF_TOOL_IDS][TOOL_GET_STATE].startswith("tool_")
    assert setup_entry.data[CONF_TOOL_IDS][TOOL_CALL_SERVICE].startswith("tool_")


def test_the_write_tool_description_carries_the_never_repeat_rule() -> None:
    """
    The rule reaches the model through this string and nowhere else.

    Section 15 puts the operative rule in the tool description rather than
    the README prompt block, and the toggle residual risk is accepted on
    the condition that this sentence ships. An earlier edit of the plan
    deleted it and nothing noticed.
    """
    assert "unconfirmed" in CALL_SERVICE_DESCRIPTION
    assert "ha_get_state" in CALL_SERVICE_DESCRIPTION
    assert "Never repeat the action." in CALL_SERVICE_DESCRIPTION


def test_the_read_tool_description_forbids_inventing_an_entity() -> None:
    """The read tool's own two instructions to the model."""
    assert "before answering" in GET_STATE_DESCRIPTION
    assert "Never invent an entity id." in GET_STATE_DESCRIPTION


def test_both_tool_configs_declare_their_type(hass) -> None:
    """
    The union variant is declared, never inferred.

    Pydantic resolves the webhook variant from the presence of api_schema
    when type is absent, so omitting it works today. It stops working the
    moment another variant of the union carries api_schema.
    """
    perms = Permissions(read=frozenset(), control=frozenset(), exceptions={})

    for config in (
        build_get_state(hass, perms, "https://x.invalid/h", "s", 20),
        build_call_service(hass, perms, {}, {}, "https://x.invalid/h", "s", 20),
    ):
        assert config["type"] == "webhook"


def test_readback_ignores_the_defaults_the_api_adds() -> None:
    """
    The comparison is one-directional: sent values must survive, that is all.

    MEASURED 11 September 2026 against the real workspace. Creating a tool
    and reading it back returns every value unchanged and adds its own
    defaults at several depths: content_type, kind, path_params_schema,
    auth_resolved_params, and is_omitted, dynamic_variable, constant_value
    and is_system_provided on every property. Comparing the whole object
    for equality therefore diverges on every sync, which would make
    sync_tools raise every time it ran.
    """
    sent = {
        "name": "ha_get_state",
        "description": "Read state.",
        "api_schema": {
            "url": "https://x.invalid/h",
            "method": "POST",
            "request_body_schema": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "constant_value": "get_state"}
                },
            },
        },
    }
    got = {
        "name": "ha_get_state",
        "description": "Read state.",
        "api_schema": {
            "url": "https://x.invalid/h",
            "method": "POST",
            "kind": "webhook",
            "content_type": "application/json",
            "path_params_schema": {},
            "auth_resolved_params": [],
            "request_body_schema": {
                "type": "object",
                "description": "",
                "is_omitted": False,
                "properties": {
                    "tool": {
                        "type": "string",
                        "constant_value": "get_state",
                        "is_omitted": False,
                        "dynamic_variable": "",
                    }
                },
            },
        },
    }

    _check_readback("ha_get_state", sent, got)


def test_readback_still_catches_a_changed_value() -> None:
    """Ignoring added keys must not mean ignoring changed ones."""
    sent = {"name": "ha_get_state", "description": "Read state.", "api_schema": {}}
    got = {"name": "ha_get_state", "description": "Something else.", "api_schema": {}}

    with pytest.raises(ToolSyncError):
        _check_readback("ha_get_state", sent, got)
