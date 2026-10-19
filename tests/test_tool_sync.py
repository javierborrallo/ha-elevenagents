"""Tests for tool schema generation and workspace synchronisation."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import pytest
from homeassistant.helpers import issue_registry as ir

from custom_components.elevenagents.const import (
    CONF_RESPONSE_TIMEOUT,
    CONF_TOOL_IDS,
    DEFAULT_RESPONSE_TIMEOUT,
    DOMAIN,
    ISSUE_AGENT_UNPROTECTED,
    ISSUE_TIMEOUT_CROSSED,
    OPT_AGENT_SETTINGS,
    OPT_CONFIRM_TIMEOUT,
    OPT_READ_ENTITIES,
    OPT_SENSITIVE,
    TOOL_CALL_SERVICE,
    TOOL_GET_STATE,
)
from custom_components.elevenagents.elevenlabs_client import (
    ElevenLabsError,
    ElevenLabsNotFoundError,
)
from custom_components.elevenagents.permissions import Permissions
from custom_components.elevenagents.tool_sync import (
    AGENT_SETTING_DEFAULTS,
    CALL_SERVICE_DESCRIPTION,
    GET_STATE_DESCRIPTION,
    ToolSyncError,
    _check_readback,
    _flatten_fields,
    _scalar_fields,
    agent_settings,
    async_build_definitions,
    async_sync_tools,
    build_call_service,
    build_get_state,
    entity_catalogue,
    entity_enum,
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


async def test_catalogue_carries_an_unregistered_entity(hass, entity_registry):
    """An entity keyed by entity_id reaches the agent like any other."""
    entry = entity_registry.async_get_or_create("light", "demo", "registered")
    hass.states.async_set(entry.entity_id, "on", {"friendly_name": "Registered"})
    hass.states.async_set("lock.yaml_lock", "locked", {"friendly_name": "Yaml Lock"})

    enum, description = entity_catalogue(hass, {entry.id, "lock.yaml_lock"})

    assert enum == [entry.entity_id, "lock.yaml_lock"]
    assert "lock.yaml_lock = Yaml Lock" in description


async def test_catalogue_drops_a_key_whose_registry_entry_is_gone(
    hass, entity_registry
):
    """A deleted entity leaves a key that must not reach the agent."""
    entry = entity_registry.async_get_or_create("light", "demo", "doomed")
    hass.states.async_set(entry.entity_id, "on")
    entity_registry.async_remove(entry.entity_id)

    enum, description = entity_catalogue(hass, {entry.id})

    assert enum == []
    assert entry.id not in description


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


def _two_services(first: str, second: str) -> dict[str, Any]:
    """Return two services that declare the key value with two selectors."""
    return {
        "a": {
            "one": {
                "target": {"entity": [{"domain": ["a"]}]},
                "fields": {"value": {"description": "x", "selector": {first: {}}}},
            }
        },
        "b": {
            "two": {
                "target": {"entity": [{"domain": ["b"]}]},
                "fields": {"value": {"description": "y", "selector": {second: {}}}},
            }
        },
    }


@pytest.mark.parametrize(("first", "second"), [("number", "text"), ("text", "number")])
def test_a_key_typed_number_and_string_is_published_as_a_string(first, second):
    """
    number.set_value types value as text and input_number.set_value as a number.

    Omitting the key leaves the agent unable to set either.
    """
    properties = service_data_properties(
        _two_services(first, second), {"a.one", "b.two"}, {}
    )

    assert properties["value"]["type"] == "string"


def test_a_key_typed_number_and_boolean_is_omitted():
    """No single type serves both, so guessing sends a schema that fails."""
    properties = service_data_properties(
        _two_services("number", "boolean"), {"a.one", "b.two"}, {}
    )

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


def test_scalar_fields_keys_each_scalar_to_its_json_type():
    """One helper decides which fields are scalar and their JSON type."""
    description = {
        "fields": {
            "temperature": {"selector": {"number": {}}},
            "mode": {"selector": {"select": {}}},
            "target": {"selector": {"entity": {}}},
            "note": {"description": "no selector"},
        }
    }
    assert _scalar_fields(description) == {
        "temperature": "number",
        "mode": "string",
    }


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
        hass,
        perms,
        DESCRIPTIONS,
        {},
        "https://example/hook",
        "sekrit",
        20,
        settings=AGENT_SETTING_DEFAULTS,
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
    config = build_get_state(
        hass,
        perms,
        "https://example/hook",
        "sekrit",
        20,
        settings=AGENT_SETTING_DEFAULTS,
    )

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

    get_state = build_get_state(
        hass,
        perms,
        "https://example/hook",
        "sekrit",
        20,
        settings=AGENT_SETTING_DEFAULTS,
    )
    call_service = build_call_service(
        hass,
        perms,
        DESCRIPTIONS,
        {},
        "https://example/hook",
        "sekrit",
        20,
        settings=AGENT_SETTING_DEFAULTS,
    )

    get_state_entities = get_state["api_schema"]["request_body_schema"]["properties"][
        "entity_id"
    ]["items"]["enum"]
    call_service_entities = call_service["api_schema"]["request_body_schema"][
        "properties"
    ]["entity_id"]["enum"]

    assert set(get_state_entities) == {readable.entity_id, controllable.entity_id}
    assert call_service_entities == [controllable.entity_id]


# --- Task 3 (0.2): agent behaviour settings ride with the tool --------------


def test_agent_settings_fall_back_to_the_platform_defaults() -> None:
    """An entry that never set it still produces a complete block."""
    assert agent_settings({}) == {"pre_tool_speech": "auto"}


def test_agent_settings_take_what_the_user_chose() -> None:
    """A stored value wins over the default."""
    stored = {OPT_AGENT_SETTINGS: {"pre_tool_speech": "force"}}

    assert agent_settings(stored) == {"pre_tool_speech": "force"}


async def test_the_built_tool_carries_the_settings(hass) -> None:
    """What the user chose reaches the tool definition, not just the options."""
    perms = Permissions(read=frozenset(), control=frozenset(), exceptions={})
    built = build_get_state(
        hass,
        perms,
        "https://example/hook",
        "secret",
        20,
        settings={"pre_tool_speech": "force"},
    )

    assert built["pre_tool_speech"] == "force"


# --- Task 12: synchronising, owning and attaching ---------------------------


@pytest.fixture
def tool_calls():
    """Patch the Task 10 client wrappers as module functions and echo writes back."""
    with (
        patch(
            "custom_components.elevenagents.tool_sync.async_create_tool"
        ) as create_tool,
        patch(
            "custom_components.elevenagents.tool_sync.async_update_tool"
        ) as update_tool,
        patch("custom_components.elevenagents.tool_sync.async_get_tool") as get_tool,
        patch("custom_components.elevenagents.tool_sync.async_get_agent") as get_agent,
        patch(
            "custom_components.elevenagents.tool_sync.async_patch_agent"
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
            platform_settings=None,
            conversation_config=SimpleNamespace(
                agent=SimpleNamespace(
                    prompt=SimpleNamespace(tool_ids=["tool_user_made"])
                )
            ),
        )

        yield SimpleNamespace(
            create_tool=create_tool,
            update_tool=update_tool,
            get_tool=get_tool,
            get_agent=get_agent,
            patch_agent=patch_agent,
            written=written,
        )


async def test_a_sync_records_the_catalogue_it_sent(
    hass, setup_entry, tool_calls, entity_registry
):
    """The registry listener compares against this record."""
    entry = entity_registry.async_get_or_create("light", "demo", "kept")
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_READ_ENTITIES: [entry.id]}
    )

    await async_sync_tools(hass, setup_entry, _CLIENT)

    assert setup_entry.runtime_data.catalogue == entity_catalogue(hass, {entry.id})
    assert setup_entry.runtime_data.catalogue[0] == [entry.entity_id]


async def test_a_failed_sync_keeps_the_previous_record(
    hass, setup_entry, tool_calls, entity_registry
):
    """A record the agent never received would hide the change from the next event."""
    await async_sync_tools(hass, setup_entry, _CLIENT)
    before = setup_entry.runtime_data.catalogue

    entry = entity_registry.async_get_or_create("light", "demo", "added")
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_READ_ENTITIES: [entry.id]}
    )
    tool_calls.patch_agent.side_effect = ElevenLabsError("down")

    with pytest.raises(ElevenLabsError):
        await async_sync_tools(hass, setup_entry, _CLIENT)

    assert setup_entry.runtime_data.catalogue == before


async def test_a_sync_that_outlives_a_reload_does_not_write_into_the_new_runtime(
    hass, setup_entry, tool_calls
):
    """A stale sync must not write its catalogue into a runtime a reload created."""
    old_runtime = setup_entry.runtime_data

    async def get(_client, tool_id) -> SimpleNamespace:
        # Simulate a reload finishing while this sync is still running.
        setup_entry.runtime_data = replace(old_runtime, catalogue=None)
        return SimpleNamespace(id=tool_id, tool_config=tool_calls.written[tool_id])

    tool_calls.get_tool.side_effect = get

    await async_sync_tools(hass, setup_entry, _CLIENT)

    assert setup_entry.runtime_data.catalogue is None


async def test_a_sync_raises_the_issue_for_an_open_agent(hass, setup_entry, tool_calls):
    """The agent read for the attach tells whether anyone may call it."""
    await async_sync_tools(hass, setup_entry, _CLIENT)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_AGENT_UNPROTECTED)
    assert issue is not None


async def test_a_sync_clears_the_issue_for_a_protected_agent(
    hass, setup_entry, tool_calls
):
    """Turning on auth in ElevenLabs clears the issue at the next sync."""
    await async_sync_tools(hass, setup_entry, _CLIENT)
    tool_calls.get_agent.return_value.platform_settings = SimpleNamespace(
        auth=SimpleNamespace(enable_auth=True, allowlist=[])
    )

    await async_sync_tools(hass, setup_entry, _CLIENT)

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_AGENT_UNPROTECTED) is None


async def test_a_sync_raises_the_issue_for_crossed_timeouts(
    hass, setup_entry, tool_calls
):
    """A confirmation wait longer than the agent waits is never answered."""
    agent_timeout = int(
        setup_entry.data.get(CONF_RESPONSE_TIMEOUT, DEFAULT_RESPONSE_TIMEOUT)
    )
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_CONFIRM_TIMEOUT: agent_timeout + 5}
    )

    await async_sync_tools(hass, setup_entry, _CLIENT)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_TIMEOUT_CROSSED)
    assert issue is not None
    assert issue.translation_placeholders == {
        "confirm_timeout": str(agent_timeout + 5),
        "agent_timeout": str(agent_timeout),
    }


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


def _agent_with_events(events: list[str] | None) -> SimpleNamespace:
    return SimpleNamespace(
        platform_settings=None,
        conversation_config=SimpleNamespace(
            agent=SimpleNamespace(prompt=SimpleNamespace(tool_ids=[])),
            conversation=SimpleNamespace(client_events=events),
        ),
    )


async def test_attaching_adds_the_tool_events_to_the_agent(
    hass, setup_entry, tool_calls
):
    """The PATCH keeps the agent's client events and adds the two tool events."""
    tool_calls.get_agent.return_value = _agent_with_events(
        ["audio", "agent_response", "agent_tool_response"]
    )

    await async_sync_tools(hass, setup_entry, _CLIENT)

    conversation_config = tool_calls.patch_agent.call_args.kwargs["conversation_config"]
    assert conversation_config["conversation"]["client_events"] == [
        "audio",
        "agent_response",
        "agent_tool_response",
        "agent_tool_request",
        "agent_tool_response_full_payload",
    ]


async def test_attaching_leaves_client_events_alone_without_a_list(
    hass, setup_entry, tool_calls
):
    """An agent without a client event list keeps the platform default."""
    tool_calls.get_agent.return_value = _agent_with_events(None)

    await async_sync_tools(hass, setup_entry, _CLIENT)

    conversation_config = tool_calls.patch_agent.call_args.kwargs["conversation_config"]
    assert "conversation" not in conversation_config


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
    rule = "Never repeat an action that returned unconfirmed."
    assert rule in CALL_SERVICE_DESCRIPTION


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
        build_get_state(
            hass, perms, "https://x.invalid/h", "s", 20, settings=AGENT_SETTING_DEFAULTS
        ),
        build_call_service(
            hass,
            perms,
            {},
            {},
            "https://x.invalid/h",
            "s",
            20,
            settings=AGENT_SETTING_DEFAULTS,
        ),
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


def test_readback_catches_a_discarded_pre_tool_speech() -> None:
    """A setting the API silently reverts must not sync as if it landed."""
    sent = {
        "name": "ha_get_state",
        "description": "Read state.",
        "api_schema": {},
        "pre_tool_speech": "force",
    }
    got = {
        "name": "ha_get_state",
        "description": "Read state.",
        "api_schema": {},
        "pre_tool_speech": "auto",
    }

    with pytest.raises(ToolSyncError, match="pre_tool_speech"):
        _check_readback("ha_get_state", sent, got)


def test_entity_enum_reads_both_tool_shapes():
    """ha_get_state holds the enum under items; ha_call_service holds it directly."""
    get_state = {
        "api_schema": {
            "request_body_schema": {
                "properties": {
                    "entity_id": {"type": "array", "items": {"enum": ["a.b"]}}
                }
            }
        }
    }
    call_service = {
        "api_schema": {
            "request_body_schema": {
                "properties": {
                    "entity_id": {"type": "string", "items": None, "enum": ["c.d"]}
                }
            }
        }
    }

    assert entity_enum(get_state) == ["a.b"]
    assert entity_enum(call_service) == ["c.d"]


def test_entity_enum_is_none_without_an_enum():
    """A tool in another shape has no list to compare."""
    assert entity_enum({"name": "x"}) is None
    assert entity_enum(SimpleNamespace(api_schema=None)) is None
    empty_schema: dict[str, Any] = {
        "api_schema": {"request_body_schema": {"properties": {}}}
    }
    assert entity_enum(empty_schema) is None


def test_the_write_tool_description_limits_the_call_to_the_named_device() -> None:
    """The rule sits where the model reads it while it builds the call."""
    assert "the one Home Assistant entity the user named" in CALL_SERVICE_DESCRIPTION
    assert "matches the user's words and can do what they asked" in (
        CALL_SERVICE_DESCRIPTION
    )
    assert "Otherwise do not call it: tell the user." in CALL_SERVICE_DESCRIPTION


async def test_the_write_catalogue_is_introduced_as_the_named_entity(
    hass, entity_registry
):
    """The catalogue under entity_id says which entry to pick."""
    entry = entity_registry.async_get_or_create("light", "demo", "one")
    hass.states.async_set(entry.entity_id, "on", {"supported_features": 0})
    perms = Permissions(read=frozenset(), control=frozenset({entry.id}), exceptions={})

    config = build_call_service(
        hass,
        perms,
        DESCRIPTIONS,
        {},
        "https://example/hook",
        "sekrit",
        20,
        settings=AGENT_SETTING_DEFAULTS,
    )

    description = config["api_schema"]["request_body_schema"]["properties"][
        "entity_id"
    ]["description"]
    assert description.startswith("The entity the user named, from this catalogue:\n")
    assert entry.entity_id in description


def test_an_empty_write_catalogue_keeps_its_plain_sentence(hass) -> None:
    """No lead-in points at a catalogue that does not exist."""
    perms = Permissions(read=frozenset(), control=frozenset(), exceptions={})

    config = build_call_service(
        hass,
        perms,
        DESCRIPTIONS,
        {},
        "https://example/hook",
        "sekrit",
        20,
        settings=AGENT_SETTING_DEFAULTS,
    )

    properties = config["api_schema"]["request_body_schema"]["properties"]
    assert (
        properties["entity_id"]["description"] == "No entity is exposed to this tool."
    )


def test_action_and_service_data_state_their_limits(hass) -> None:
    """The action list is a union, and an unsupported parameter can pass in silence."""
    perms = Permissions(read=frozenset(), control=frozenset(), exceptions={})

    config = build_call_service(
        hass,
        perms,
        DESCRIPTIONS,
        {},
        "https://example/hook",
        "sekrit",
        20,
        settings=AGENT_SETTING_DEFAULTS,
    )

    properties = config["api_schema"]["request_body_schema"]["properties"]
    assert (
        "covers every controlled entity together" in properties["action"]["description"]
    )
    assert (
        "can ignore one it does not support without an error"
        in properties["service_data"]["description"]
    )


# --- Task 3 (0.5): confirmation wiring in the tool schema -------------------


async def test_call_service_carries_the_conversation_id_and_the_code(hass) -> None:
    """The platform fills conversation_id; the model fills the code."""
    perms = Permissions(read=frozenset(), control=frozenset(), exceptions={})
    config = build_call_service(
        hass,
        perms,
        DESCRIPTIONS,
        {},
        "https://example/hook",
        "sekrit",
        20,
        settings=AGENT_SETTING_DEFAULTS,
    )
    properties = config["api_schema"]["request_body_schema"]["properties"]
    conversation_id = properties["conversation_id"]

    assert conversation_id["dynamic_variable"] == "system__conversation_id"
    assert "confirmation_code" in properties
    assert "conversation_history" not in properties
    required = config["api_schema"]["request_body_schema"]["required"]
    assert "confirmation_code" not in required
    assert "conversation_id" not in required


async def test_call_service_carries_the_history_only_when_asked(hass) -> None:
    """conversation_history is a property only when some mark asks for confirmation."""
    perms = Permissions(read=frozenset(), control=frozenset(), exceptions={})
    config = build_call_service(
        hass,
        perms,
        DESCRIPTIONS,
        {},
        "https://example/hook",
        "sekrit",
        20,
        settings=AGENT_SETTING_DEFAULTS,
        with_history=True,
    )
    properties = config["api_schema"]["request_body_schema"]["properties"]

    history = properties["conversation_history"]
    assert history["dynamic_variable"] == "system__conversation_history"


def test_the_write_tool_description_explains_confirmation() -> None:
    """The model learns the retry rule from the description, not a side channel."""
    assert "confirmation_required" in CALL_SERVICE_DESCRIPTION
    assert "confirmation_code" in CALL_SERVICE_DESCRIPTION


async def test_conversation_id_and_history_carry_only_the_dynamic_variable(
    hass,
) -> None:
    """
    No description: a property cannot carry both.

    SDK 2.51's LiteralJsonSchemaProperty makes description and dynamic_variable
    mutually exclusive. A property carrying both either 422s on sync or lets the
    platform's "description wins" behaviour hand the field back to the model,
    defeating the gate. These two system-filled properties must carry only
    type and dynamic_variable, exactly like the `tool` discriminator.
    """
    perms = Permissions(read=frozenset(), control=frozenset(), exceptions={})
    config = build_call_service(
        hass,
        perms,
        DESCRIPTIONS,
        {},
        "https://example/hook",
        "sekrit",
        20,
        settings=AGENT_SETTING_DEFAULTS,
        with_history=True,
    )
    properties = config["api_schema"]["request_body_schema"]["properties"]

    assert properties["conversation_id"] == {
        "type": "string",
        "dynamic_variable": "system__conversation_id",
    }
    assert properties["conversation_history"] == {
        "type": "string",
        "dynamic_variable": "system__conversation_history",
    }


async def test_the_synced_definitions_carry_history_only_when_a_mark_confirms(
    hass, setup_entry
) -> None:
    """async_build_definitions ties with_history to whether any mark confirms."""
    without_marks = await async_build_definitions(hass, setup_entry)
    schema = without_marks[TOOL_CALL_SERVICE]["api_schema"]["request_body_schema"]
    assert "conversation_history" not in schema["properties"]

    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_SENSITIVE: {"lock.front": {"confirm": True}}}
    )

    with_marks = await async_build_definitions(hass, setup_entry)
    schema = with_marks[TOOL_CALL_SERVICE]["api_schema"]["request_body_schema"]
    assert "conversation_history" in schema["properties"]


def test_the_call_service_description_says_how_to_read_a_no_change():
    """A slow device reads as no_change while it still moves."""
    assert (
        "If the outcome is no_change and the state is not what the user asked for, "
        "say the command was sent and the device has not reported the change yet."
    ) in CALL_SERVICE_DESCRIPTION


def test_the_call_service_description_says_how_to_correct_an_invalid_value():
    """The detail of invalid_value names what to change."""
    assert (
        "If the error is invalid_value, correct the service_data from the detail "
        "and call once more."
    ) in CALL_SERVICE_DESCRIPTION


def test_the_call_service_description_says_how_to_read_moving():
    """A moving device reports a position on its way, not where it stops."""
    assert (
        "If the response carries moving true, the device is still moving: "
        "say it is on its way to what the user asked for."
    ) in CALL_SERVICE_DESCRIPTION
