"""Tests for the WebSocket commands the panel calls."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elevenlabs_agent.const import (
    CONF_TOOL_IDS,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DEFAULT_CONFIRM_TIMEOUT,
    DOMAIN,
    ISSUE_ROTATION_UNSYNCED,
    OPT_AGENT_SETTINGS,
    OPT_AUTO_CONTROL,
    OPT_AUTO_EXPOSE,
    OPT_AUTO_EXPOSE_AREAS,
    OPT_CONFIRM_TIMEOUT,
    OPT_CONTROL_ENTITIES,
    OPT_EXCEPTIONS,
    OPT_READ_ENTITIES,
    TOOL_GET_STATE,
)
from custom_components.elevenlabs_agent.elevenlabs_client import (
    ElevenLabsAuthError,
    ElevenLabsError,
)
from custom_components.elevenlabs_agent.tool_sync import ToolSyncError

if TYPE_CHECKING:
    from pytest_homeassistant_custom_component.typing import WebSocketGenerator

# homeassistant 2026.9.1's http server sets self.app["hass"] as a plain key.
# aiohttp 3.14.3 raises NotAppKeyWarning for that pattern. pyproject.toml
# turns every warning into an error, so this module ignores this one category.
pytestmark = pytest.mark.filterwarnings(
    "ignore::aiohttp.web_exceptions.NotAppKeyWarning"
)


async def test_config_get_returns_the_stored_options(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """The panel reads what is stored without touching the network."""
    hass.config_entries.async_update_entry(
        setup_entry,
        options={OPT_READ_ENTITIES: ["light.a"], OPT_CONTROL_ENTITIES: ["light.b"]},
    )
    await hass.async_block_till_done()

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "elevenlabs_agent/config/get", "entry_id": setup_entry.entry_id}
    )
    result = (await client.receive_json())["result"]

    assert result["read_entities"] == ["light.a"]
    assert result["control_entities"] == ["light.b"]
    assert result["confirm_timeout"] == DEFAULT_CONFIRM_TIMEOUT
    assert result["configured"] is True


async def test_config_get_returns_entity_id_forms_for_the_panels_table(
    hass, setup_entry, entity_registry, hass_ws_client: WebSocketGenerator
) -> None:
    """The panel's table is keyed by entity_id, not by permission key."""
    read_entry = entity_registry.async_get_or_create("light", "demo", "a")
    control_entry = entity_registry.async_get_or_create("light", "demo", "b")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={
            OPT_READ_ENTITIES: [read_entry.id],
            OPT_CONTROL_ENTITIES: [control_entry.id],
            OPT_EXCEPTIONS: {control_entry.id: ["light.toggle"]},
        },
    )
    await hass.async_block_till_done()

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "elevenlabs_agent/config/get", "entry_id": setup_entry.entry_id}
    )
    result = (await client.receive_json())["result"]

    assert result["read_entity_ids"] == [read_entry.entity_id]
    assert result["control_entity_ids"] == [control_entry.entity_id]
    assert result["exception_entity_ids"] == {control_entry.entity_id: ["light.toggle"]}


async def test_config_get_drops_a_stale_key_from_the_entity_id_form(
    hass, setup_entry, entity_registry, hass_ws_client: WebSocketGenerator
) -> None:
    """An entity removed from the registry cannot be shown, but its key stays."""
    entry = entity_registry.async_get_or_create("light", "demo", "gone")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={
            OPT_READ_ENTITIES: [entry.id],
            OPT_EXCEPTIONS: {entry.id: ["light.toggle"]},
        },
    )
    await hass.async_block_till_done()
    entity_registry.async_remove(entry.entity_id)

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "elevenlabs_agent/config/get", "entry_id": setup_entry.entry_id}
    )
    result = (await client.receive_json())["result"]

    assert result["read_entities"] == [entry.id]
    assert result["read_entity_ids"] == []
    assert result["exceptions"] == {entry.id: ["light.toggle"]}
    assert result["exception_entity_ids"] == {}


async def test_config_get_returns_both_auto_expose_switches(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """The panel must reflect the control switch too, not just the read one."""
    hass.config_entries.async_update_entry(
        setup_entry,
        options={OPT_AUTO_EXPOSE: True, OPT_AUTO_CONTROL: True},
    )
    await hass.async_block_till_done()

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "elevenlabs_agent/config/get", "entry_id": setup_entry.entry_id}
    )
    result = (await client.receive_json())["result"]

    assert result["auto_expose"] is True
    assert result["auto_control"] is True


async def test_config_get_says_nothing_is_configured_when_nothing_is_granted(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """The empty state is what the panel uses to show the wizard."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "elevenlabs_agent/config/get", "entry_id": setup_entry.entry_id}
    )
    result = (await client.receive_json())["result"]

    assert result["configured"] is False


async def test_config_get_refuses_an_unknown_entry(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """An entry id from another integration is not addressable."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "elevenlabs_agent/config/get", "entry_id": "does_not_exist"}
    )
    message = await client.receive_json()

    assert message["success"] is False
    assert message["error"]["code"] == "not_found"


async def test_config_get_refuses_an_entry_from_another_integration(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """A real entry id is not addressable when it belongs to another domain."""
    foreign_entry = MockConfigEntry(domain="demo")
    foreign_entry.add_to_hass(hass)

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "elevenlabs_agent/config/get", "entry_id": foreign_entry.entry_id}
    )
    message = await client.receive_json()

    assert message["success"] is False
    assert message["error"]["code"] == "not_found"


async def test_config_get_refuses_a_non_admin(
    hass, setup_entry, hass_read_only_access_token, hass_ws_client: WebSocketGenerator
) -> None:
    """The screen that grants actions on a lock is for administrators."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(
        {"type": "elevenlabs_agent/config/get", "entry_id": setup_entry.entry_id}
    )
    message = await client.receive_json()

    assert message["success"] is False
    assert message["error"]["code"] == "unauthorized"


async def test_saving_entities_stores_keys_and_synchronises(
    hass, setup_entry, entity_registry, hass_ws_client: WebSocketGenerator
) -> None:
    """The browser sends entity ids; the server stores what it resolved."""
    entry = entity_registry.async_get_or_create("light", "demo", "one")
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_sync_tools", AsyncMock()
    ) as sync:
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/config/save",
                "entry_id": setup_entry.entry_id,
                "block": "entities",
                "data": {"read": [entry.entity_id], "control": []},
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "ok"
    assert setup_entry.options[OPT_READ_ENTITIES] == [entry.id]
    assert sync.await_count == 1


async def test_an_entity_with_no_registry_entry_is_keyed_by_entity_id(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """Locks and YAML entities reach the allowlist like any other."""
    hass.states.async_set("lock.yaml_lock", "locked")
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_sync_tools", AsyncMock()
    ):
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/config/save",
                "entry_id": setup_entry.entry_id,
                "block": "entities",
                "data": {"read": [], "control": ["lock.yaml_lock"]},
            }
        )
        await client.receive_json()

    assert setup_entry.options[OPT_CONTROL_ENTITIES] == ["lock.yaml_lock"]


async def test_a_failed_sync_still_stores_the_permission(
    hass, setup_entry, entity_registry, hass_ws_client: WebSocketGenerator
) -> None:
    """The local check is what protects, so it is written even when the sync fails."""
    entry = entity_registry.async_get_or_create("light", "demo", "one")
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_sync_tools",
        AsyncMock(side_effect=ElevenLabsError("boom")),
    ):
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/config/save",
                "entry_id": setup_entry.entry_id,
                "block": "entities",
                "data": {"read": [entry.entity_id], "control": []},
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "saved_not_sent"
    assert "boom" in result["detail"]
    assert setup_entry.options[OPT_READ_ENTITIES] == [entry.id]


async def test_a_dropped_field_still_stores_the_permission(
    hass, setup_entry, entity_registry, hass_ws_client: WebSocketGenerator
) -> None:
    """A silently dropped field is also a failed synchronisation, not a crash."""
    entry = entity_registry.async_get_or_create("light", "demo", "one")
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_sync_tools",
        AsyncMock(side_effect=ToolSyncError("field dropped")),
    ):
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/config/save",
                "entry_id": setup_entry.entry_id,
                "block": "entities",
                "data": {"read": [entry.entity_id], "control": []},
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "saved_not_sent"
    assert "field dropped" in result["detail"]
    assert setup_entry.options[OPT_READ_ENTITIES] == [entry.id]


async def test_saving_to_an_entry_that_is_not_loaded_reports_saved_not_sent(
    hass, setup_entry, entity_registry, hass_ws_client: WebSocketGenerator
) -> None:
    """The entry may be broken, but the permission is still written."""
    entry = entity_registry.async_get_or_create("light", "demo", "one")
    setup_entry.mock_state(hass, ConfigEntryState.SETUP_ERROR)
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_sync_tools", AsyncMock()
    ) as sync:
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/config/save",
                "entry_id": setup_entry.entry_id,
                "block": "entities",
                "data": {"read": [entry.entity_id], "control": []},
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "saved_not_sent"
    assert setup_entry.options[OPT_READ_ENTITIES] == [entry.id]
    assert sync.await_count == 0

    # Restore the real state so teardown can unload the entry and cancel
    # the timer expose.py armed when the entity above was registered.
    setup_entry.mock_state(hass, ConfigEntryState.LOADED)


async def test_config_save_refuses_a_non_admin(
    hass, setup_entry, hass_read_only_access_token, hass_ws_client: WebSocketGenerator
) -> None:
    """Granting a voice agent control of a lock is for administrators."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(
        {
            "type": "elevenlabs_agent/config/save",
            "entry_id": setup_entry.entry_id,
            "block": "auto_expose",
            "data": {"read": True, "control": True, "areas": []},
        }
    )
    message = await client.receive_json()

    assert message["success"] is False
    assert message["error"]["code"] == "unauthorized"


async def test_saving_exceptions_stores_by_permission_key_and_synchronises(
    hass, setup_entry, entity_registry, hass_ws_client: WebSocketGenerator
) -> None:
    """The exceptions block keys removed services by permission key, not entity id."""
    entry = entity_registry.async_get_or_create("light", "demo", "one")
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_sync_tools", AsyncMock()
    ) as sync:
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/config/save",
                "entry_id": setup_entry.entry_id,
                "block": "exceptions",
                "data": {entry.entity_id: ["light.toggle"]},
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "ok"
    assert setup_entry.options[OPT_EXCEPTIONS] == {entry.id: ["light.toggle"]}
    assert sync.await_count == 1


async def test_saving_exceptions_fans_out_to_every_key_a_group_resolves_to(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """A group in the exceptions target gives the same exception to every member."""
    hass.states.async_set(
        "group.demo_group", "on", {"entity_id": ["light.a", "light.b"]}
    )
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_sync_tools", AsyncMock()
    ):
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/config/save",
                "entry_id": setup_entry.entry_id,
                "block": "exceptions",
                "data": {"group.demo_group": ["light.toggle"]},
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "ok"
    assert setup_entry.options[OPT_EXCEPTIONS] == {
        "light.a": ["light.toggle"],
        "light.b": ["light.toggle"],
    }


async def test_saving_exceptions_drops_a_target_that_resolves_to_nothing(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """An empty group carries no permission key, so it stores no exception."""
    hass.states.async_set("group.empty_group", "on", {"entity_id": []})
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_sync_tools", AsyncMock()
    ):
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/config/save",
                "entry_id": setup_entry.entry_id,
                "block": "exceptions",
                "data": {"group.empty_group": ["light.toggle"]},
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "ok"
    assert setup_entry.options[OPT_EXCEPTIONS] == {}


async def test_saving_auto_expose_does_not_synchronise(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """The switches change nothing the agent is offered."""
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_sync_tools", AsyncMock()
    ) as sync:
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/config/save",
                "entry_id": setup_entry.entry_id,
                "block": "auto_expose",
                "data": {"read": True, "control": True, "areas": ["salon"]},
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "ok"
    assert setup_entry.options[OPT_AUTO_EXPOSE] is True
    assert setup_entry.options[OPT_AUTO_CONTROL] is True
    assert setup_entry.options[OPT_AUTO_EXPOSE_AREAS] == ["salon"]
    assert sync.await_count == 0


async def test_saving_auto_expose_defaults_to_off_and_no_areas(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """A block with no keys turns both switches off and watches nothing."""
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_sync_tools", AsyncMock()
    ):
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/config/save",
                "entry_id": setup_entry.entry_id,
                "block": "auto_expose",
                "data": {},
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "ok"
    assert setup_entry.options[OPT_AUTO_EXPOSE] is False
    assert setup_entry.options[OPT_AUTO_CONTROL] is False
    assert setup_entry.options[OPT_AUTO_EXPOSE_AREAS] == []


async def test_saving_auto_expose_honours_each_switch_independently(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """Turning on control alone must not also turn on read."""
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_sync_tools", AsyncMock()
    ):
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/config/save",
                "entry_id": setup_entry.entry_id,
                "block": "auto_expose",
                "data": {"control": True, "areas": ["salon"]},
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "ok"
    assert setup_entry.options[OPT_AUTO_EXPOSE] is False
    assert setup_entry.options[OPT_AUTO_CONTROL] is True


async def test_a_confirmation_timeout_at_or_above_the_agents_is_refused(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """The agent hangs up before Home Assistant answers, so the numbers cannot cross."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "elevenlabs_agent/config/save",
            "entry_id": setup_entry.entry_id,
            "block": "settings",
            "data": {"confirm_timeout": 20},
        }
    )
    result = (await client.receive_json())["result"]

    assert result["outcome"] == "failed"
    assert result["detail"] == "timeout_not_below_agent"
    assert OPT_CONFIRM_TIMEOUT not in setup_entry.options


async def test_saving_agent_settings_synchronises(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """A behaviour change has to reach the tool to mean anything."""
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_sync_tools", AsyncMock()
    ) as sync:
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/config/save",
                "entry_id": setup_entry.entry_id,
                "block": "settings",
                "data": {
                    "confirm_timeout": 5,
                    "agent_settings": {"pre_tool_speech": "force"},
                },
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "ok"
    assert sync.await_count == 1
    assert setup_entry.options[OPT_AGENT_SETTINGS] == {"pre_tool_speech": "force"}


async def test_an_unknown_block_is_refused_by_the_schema(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """The input boundary is declared, not written."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "elevenlabs_agent/config/save",
            "entry_id": setup_entry.entry_id,
            "block": "everything",
            "data": {},
        }
    )
    message = await client.receive_json()

    assert message["success"] is False
    assert message["error"]["code"] == "invalid_format"


def _agent_with_prompt(text: str) -> MagicMock:
    """Build the nested response shape the SDK returns."""
    agent = MagicMock()
    agent.conversation_config.agent.prompt.prompt = text
    return agent


async def test_agent_get_returns_the_prompt_that_is_there(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """The editor opens on the user's own text, not on a blank box."""
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_get_agent",
        AsyncMock(return_value=_agent_with_prompt("Eres un asistente.")),
    ):
        await client.send_json_auto_id(
            {"type": "elevenlabs_agent/agent/get", "entry_id": setup_entry.entry_id}
        )
        result = (await client.receive_json())["result"]

    assert result["prompt"] == "Eres un asistente."


async def test_agent_get_falls_back_to_the_default_settings_when_never_synced(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """Before any tool is synced there is nothing in the account to read."""
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_get_agent",
        AsyncMock(return_value=_agent_with_prompt("Eres un asistente.")),
    ):
        await client.send_json_auto_id(
            {"type": "elevenlabs_agent/agent/get", "entry_id": setup_entry.entry_id}
        )
        result = (await client.receive_json())["result"]

    assert result["settings"] == {"pre_tool_speech": "auto"}


async def test_agent_get_returns_the_accounts_own_setting(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """A value the user changed by hand must not be shown as the default."""
    hass.config_entries.async_update_entry(
        setup_entry,
        data={**setup_entry.data, CONF_TOOL_IDS: {TOOL_GET_STATE: "tool_123"}},
    )
    tool = MagicMock()
    tool.tool_config.pre_tool_speech = "off"
    client = await hass_ws_client(hass)

    with (
        patch(
            "custom_components.elevenlabs_agent.websocket.async_get_agent",
            AsyncMock(return_value=_agent_with_prompt("Eres un asistente.")),
        ),
        patch(
            "custom_components.elevenlabs_agent.websocket.async_get_tool",
            AsyncMock(return_value=tool),
        ) as get_tool,
    ):
        await client.send_json_auto_id(
            {"type": "elevenlabs_agent/agent/get", "entry_id": setup_entry.entry_id}
        )
        result = (await client.receive_json())["result"]

    assert result["settings"] == {"pre_tool_speech": "off"}
    assert get_tool.await_args.args[1] == "tool_123"


async def test_agent_get_refuses_a_non_admin(
    hass, setup_entry, hass_read_only_access_token, hass_ws_client: WebSocketGenerator
) -> None:
    """Reading the agent's system prompt is for administrators."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(
        {"type": "elevenlabs_agent/agent/get", "entry_id": setup_entry.entry_id}
    )
    message = await client.receive_json()

    assert message["success"] is False
    assert message["error"]["code"] == "unauthorized"


async def test_agent_get_refuses_an_entry_that_is_not_loaded(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """runtime_data only exists while the entry is loaded."""
    setup_entry.mock_state(hass, ConfigEntryState.SETUP_ERROR)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {"type": "elevenlabs_agent/agent/get", "entry_id": setup_entry.entry_id}
    )
    message = await client.receive_json()

    assert message["success"] is False
    assert message["error"]["code"] == "not_loaded"

    setup_entry.mock_state(hass, ConfigEntryState.LOADED)


async def test_agent_save_writes_the_prompt(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """Submitting the editor is the only thing that writes it."""
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_patch_agent", AsyncMock()
    ) as patched:
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/agent/save",
                "entry_id": setup_entry.entry_id,
                "prompt": "# Tools\n...",
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "ok"
    sent = patched.await_args.kwargs["conversation_config"]
    assert sent["agent"]["prompt"]["prompt"] == "# Tools\n..."


async def test_agent_save_refuses_a_non_admin(
    hass, setup_entry, hass_read_only_access_token, hass_ws_client: WebSocketGenerator
) -> None:
    """Rewriting the agent's system prompt is for administrators."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(
        {
            "type": "elevenlabs_agent/agent/save",
            "entry_id": setup_entry.entry_id,
            "prompt": "x",
        }
    )
    message = await client.receive_json()

    assert message["success"] is False
    assert message["error"]["code"] == "unauthorized"


async def test_agent_save_reports_a_refused_write(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """A stale key must not look like a saved prompt."""
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_patch_agent",
        AsyncMock(side_effect=ElevenLabsAuthError("unauthorised")),
    ):
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/agent/save",
                "entry_id": setup_entry.entry_id,
                "prompt": "x",
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "failed"
    assert "unauthorised" in result["detail"]


async def test_agent_save_refuses_an_entry_that_is_not_loaded(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """A write that cannot reach the account must not be reported as ok."""
    setup_entry.mock_state(hass, ConfigEntryState.SETUP_ERROR)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "elevenlabs_agent/agent/save",
            "entry_id": setup_entry.entry_id,
            "prompt": "x",
        }
    )
    result = (await client.receive_json())["result"]

    assert result["outcome"] == "failed"

    setup_entry.mock_state(hass, ConfigEntryState.LOADED)


async def test_rotate_refuses_a_non_admin(
    hass, setup_entry, hass_read_only_access_token, hass_ws_client: WebSocketGenerator
) -> None:
    """Rotating the shared secret protecting the webhook is for administrators."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(
        {
            "type": "elevenlabs_agent/rotate",
            "entry_id": setup_entry.entry_id,
            "what": "secret",
        }
    )
    message = await client.receive_json()

    assert message["success"] is False
    assert message["error"]["code"] == "unauthorized"


async def test_rotating_the_secret_replaces_it_and_syncs(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """A rotated secret reaches the tool definition in the same breath."""
    before = setup_entry.data[CONF_WEBHOOK_SECRET]
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_sync_tools", AsyncMock()
    ) as sync:
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/rotate",
                "entry_id": setup_entry.entry_id,
                "what": "secret",
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "ok"
    assert setup_entry.data[CONF_WEBHOOK_SECRET] != before
    assert sync.await_count == 1


async def test_a_failed_webhook_rotation_restores_the_old_id(
    hass, setup_entry, issue_registry, hass_ws_client: WebSocketGenerator
) -> None:
    """A half-rotated webhook is a URL nobody answers."""
    before = setup_entry.data[CONF_WEBHOOK_ID]
    client = await hass_ws_client(hass)

    with (
        patch(
            "custom_components.elevenlabs_agent.websocket.webhook.async_generate_id",
            return_value="new_webhook_id",
        ),
        patch(
            "custom_components.elevenlabs_agent.websocket.async_sync_tools",
            AsyncMock(side_effect=ElevenLabsError("no")),
        ),
    ):
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/rotate",
                "entry_id": setup_entry.entry_id,
                "what": "webhook",
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "failed"
    assert setup_entry.data[CONF_WEBHOOK_ID] == before
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ROTATION_UNSYNCED) is not None
    # The old path must still answer: only the id that never worked is retired.
    handlers = hass.data[webhook.DOMAIN]
    assert before in handlers
    assert "new_webhook_id" not in handlers


async def test_a_dropped_field_during_webhook_rotation_restores_the_old_id(
    hass, setup_entry, issue_registry, hass_ws_client: WebSocketGenerator
) -> None:
    """A silently dropped field is also a failed synchronisation, not a crash."""
    before = setup_entry.data[CONF_WEBHOOK_ID]
    client = await hass_ws_client(hass)

    with (
        patch(
            "custom_components.elevenlabs_agent.websocket.webhook.async_generate_id",
            return_value="new_webhook_id",
        ),
        patch(
            "custom_components.elevenlabs_agent.websocket.async_sync_tools",
            AsyncMock(side_effect=ToolSyncError("field dropped")),
        ),
    ):
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/rotate",
                "entry_id": setup_entry.entry_id,
                "what": "webhook",
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "failed"
    assert setup_entry.data[CONF_WEBHOOK_ID] == before
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ROTATION_UNSYNCED) is not None
    # The old path must still answer: only the id that never worked is retired.
    handlers = hass.data[webhook.DOMAIN]
    assert before in handlers
    assert "new_webhook_id" not in handlers


async def test_rotate_refuses_an_entry_that_is_not_loaded(
    hass, setup_entry, hass_ws_client: WebSocketGenerator
) -> None:
    """runtime_data only exists while loaded, so rotation must not start."""
    before = setup_entry.data[CONF_WEBHOOK_ID]
    handlers_before = set(hass.data.get(webhook.DOMAIN, {}))
    setup_entry.mock_state(hass, ConfigEntryState.SETUP_ERROR)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "elevenlabs_agent/rotate",
            "entry_id": setup_entry.entry_id,
            "what": "webhook",
        }
    )
    result = (await client.receive_json())["result"]

    assert result["outcome"] == "failed"
    assert setup_entry.data[CONF_WEBHOOK_ID] == before
    # Nothing was registered: the guard fired before any local mutation.
    assert set(hass.data.get(webhook.DOMAIN, {})) == handlers_before

    setup_entry.mock_state(hass, ConfigEntryState.LOADED)


async def test_a_successful_webhook_rotation_clears_the_issue(
    hass, setup_entry, issue_registry, hass_ws_client: WebSocketGenerator
) -> None:
    """A later success retires the warning a failure raised."""
    before = setup_entry.data[CONF_WEBHOOK_ID]
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_ROTATION_UNSYNCED,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_ROTATION_UNSYNCED,
    )
    client = await hass_ws_client(hass)

    with (
        patch(
            "custom_components.elevenlabs_agent.websocket.webhook.async_generate_id",
            return_value="new_webhook_id",
        ),
        patch(
            "custom_components.elevenlabs_agent.websocket.async_sync_tools", AsyncMock()
        ),
    ):
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/rotate",
                "entry_id": setup_entry.entry_id,
                "what": "webhook",
            }
        )
        result = (await client.receive_json())["result"]

    assert result["outcome"] == "ok"
    assert setup_entry.data[CONF_WEBHOOK_ID] == "new_webhook_id"
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ROTATION_UNSYNCED) is None
    # The rotation is complete: the new path answers, the old one no longer does.
    handlers = hass.data[webhook.DOMAIN]
    assert before not in handlers
    assert "new_webhook_id" in handlers


async def test_the_issue_survives_until_the_sync_actually_finishes(
    hass, setup_entry, issue_registry, hass_ws_client: WebSocketGenerator
) -> None:
    """A rotation still in flight must not clear a warning about a prior one."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_ROTATION_UNSYNCED,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_ROTATION_UNSYNCED,
    )
    before = setup_entry.data[CONF_WEBHOOK_SECRET]
    client = await hass_ws_client(hass)
    seen_during_sync: list[bool] = []
    secret_during_sync: list[bool] = []

    async def _sync_tools(*_args: object, **_kwargs: object) -> None:
        """Record the issue and the secret write while the sync runs."""
        seen_during_sync.append(
            issue_registry.async_get_issue(DOMAIN, ISSUE_ROTATION_UNSYNCED) is not None
        )
        secret_during_sync.append(setup_entry.data[CONF_WEBHOOK_SECRET] != before)

    with patch(
        "custom_components.elevenlabs_agent.websocket.async_sync_tools", _sync_tools
    ):
        await client.send_json_auto_id(
            {
                "type": "elevenlabs_agent/rotate",
                "entry_id": setup_entry.entry_id,
                "what": "secret",
            }
        )
        await client.receive_json()

    assert seen_during_sync == [True]
    # The new secret is written before the sync starts, not after it finishes.
    assert secret_during_sync == [True]
