"""Tests for the entry lifecycle, the sync lock, and the sync_tools service."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from elevenlabs.types import GetAgentResponseModel
from elevenlabs.types.agent_config import AgentConfig
from elevenlabs.types.agent_metadata_response_model import AgentMetadataResponseModel
from elevenlabs.types.conversational_config import ConversationalConfig
from elevenlabs.types.prompt_agent_api_model_output import PromptAgentApiModelOutput
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.setup import async_setup_component

from custom_components.elevenlabs_agent.const import (
    CONF_TOOL_IDS,
    CONF_WEBHOOK_ID,
    DOMAIN,
)
from custom_components.elevenlabs_agent.elevenlabs_client import (
    ElevenLabsAuthError,
    ElevenLabsError,
)
from custom_components.elevenlabs_agent.services import SERVICE_SYNC_TOOLS
from custom_components.elevenlabs_agent.tool_sync import ToolSyncError

# homeassistant 2026.9.1's http server sets self.app["hass"] as a plain key.
# aiohttp 3.14.3 raises NotAppKeyWarning for that pattern. pyproject.toml
# turns every warning into an error, so this module ignores this one category.
pytestmark = pytest.mark.filterwarnings(
    "ignore::aiohttp.web_exceptions.NotAppKeyWarning"
)


def _agent_with_attached_tools(tool_ids: list[str]) -> GetAgentResponseModel:
    """Build a real GetAgentResponseModel whose prompt carries the given tool ids."""
    return GetAgentResponseModel(
        agent_id="agent_test",
        name="Test agent",
        conversation_config=ConversationalConfig(
            agent=AgentConfig(prompt=PromptAgentApiModelOutput(tool_ids=tool_ids))
        ),
        metadata=AgentMetadataResponseModel(
            created_at_unix_secs=0, updated_at_unix_secs=0
        ),
        platform_settings=None,
    )


# --- Task 16: setup, unload, removal --------------------------------------


async def test_unload_removes_the_webhook_and_allows_reload(hass, setup_entry):
    """Without this, every options change would need a restart."""
    from homeassistant.components import webhook

    async def _probe(*_args: object) -> None:
        return None

    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()

    # async_unregister is a silent no-op, so re-registering is the proof
    # that the route was released.
    webhook.async_register(
        hass, "test", "probe", setup_entry.data[CONF_WEBHOOK_ID], _probe
    )


async def test_setup_registers_no_update_listener(hass, setup_entry):
    """
    A write to entry.data during sync must not reload the entry mid-setup.

    Nothing needs the reload. The panel synchronises the tools itself when
    it writes a block, and the webhook reads entry.options on every request.
    """
    assert setup_entry.update_listeners == []


async def test_remove_detaches_before_deleting(hass, setup_entry):
    """A deleted-but-attached tool leaves the agent pointing at nothing."""
    order: list[str] = []

    hass.config_entries.async_update_entry(
        setup_entry,
        data={**setup_entry.data, CONF_TOOL_IDS: {"ha_get_state": "tool_a"}},
    )

    async def patch_agent(*_args: object, **_kwargs: object) -> None:
        order.append("detach")

    async def delete_tool(*_args: object, **_kwargs: object) -> None:
        order.append("delete")

    with (
        patch(
            "custom_components.elevenlabs_agent.async_get_client",
            return_value=AsyncMock(),
        ),
        patch(
            "custom_components.elevenlabs_agent.async_get_agent",
            return_value=_agent_with_attached_tools(["tool_user_made", "tool_a"]),
        ),
        patch(
            "custom_components.elevenlabs_agent.async_patch_agent",
            side_effect=patch_agent,
        ),
        patch(
            "custom_components.elevenlabs_agent.async_delete_tool",
            side_effect=delete_tool,
        ),
    ):
        await hass.config_entries.async_remove(setup_entry.entry_id)
        await hass.async_block_till_done()

    assert order == ["detach", "delete"]


async def test_remove_leaves_a_tool_the_user_attached_by_hand(hass, setup_entry):
    """The detach PATCH keeps every tool_id this integration does not own."""
    hass.config_entries.async_update_entry(
        setup_entry,
        data={**setup_entry.data, CONF_TOOL_IDS: {"ha_get_state": "tool_a"}},
    )

    with (
        patch(
            "custom_components.elevenlabs_agent.async_get_client",
            return_value=AsyncMock(),
        ),
        patch(
            "custom_components.elevenlabs_agent.async_get_agent",
            return_value=_agent_with_attached_tools(["tool_user_made", "tool_a"]),
        ),
        patch("custom_components.elevenlabs_agent.async_patch_agent") as patch_agent,
        patch("custom_components.elevenlabs_agent.async_delete_tool"),
    ):
        await hass.config_entries.async_remove(setup_entry.entry_id)
        await hass.async_block_till_done()

    conversation_config = patch_agent.call_args.kwargs["conversation_config"]
    remaining = conversation_config["agent"]["prompt"]["tool_ids"]
    assert remaining == ["tool_user_made"]


async def test_remove_with_nothing_stored_calls_neither_client_function(
    hass, setup_entry
):
    """An entry synced before the first sync completed removes cleanly."""
    hass.config_entries.async_update_entry(
        setup_entry, data={**setup_entry.data, CONF_TOOL_IDS: {}}
    )

    with (
        patch("custom_components.elevenlabs_agent.async_get_agent") as get_agent,
        patch("custom_components.elevenlabs_agent.async_delete_tool") as delete_tool,
    ):
        await hass.config_entries.async_remove(setup_entry.entry_id)
        await hass.async_block_till_done()

    assert get_agent.called is False
    assert delete_tool.called is False


async def test_an_auth_error_during_setup_starts_a_reauth_flow(hass, config_entry):
    """An expired or revoked key must be reachable from a repair, not a log line."""
    with patch(
        "custom_components.elevenlabs_agent.async_sync_tools",
        AsyncMock(side_effect=ElevenLabsAuthError("revoked")),
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == config_entries.SOURCE_REAUTH
    assert flows[0]["context"]["entry_id"] == config_entry.entry_id


# --- I1: a failed setup sync must not disable the entry ---------------------


def _entities_for(hass, entry) -> list[er.RegistryEntry]:
    """Return the entity registry entries this config entry's platforms created."""
    return er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


async def test_a_5xx_during_setup_leaves_the_entry_usable(hass, config_entry):
    """The status device must exist precisely when it has something to report."""
    with patch(
        "custom_components.elevenlabs_agent.async_sync_tools",
        AsyncMock(side_effect=ElevenLabsError("boom")),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert len(_entities_for(hass, config_entry)) == 9


async def test_a_tool_sync_error_during_setup_leaves_the_entry_usable(
    hass, config_entry
):
    """A read-back divergence during the first sync is not fatal either."""
    with patch(
        "custom_components.elevenlabs_agent.async_sync_tools",
        AsyncMock(side_effect=ToolSyncError("diverged")),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert len(_entities_for(hass, config_entry)) == 9


async def test_a_transport_error_during_setup_leaves_the_entry_usable(
    hass, config_entry
):
    """DNS or connect failures during a restart must not disable the entry."""
    with patch(
        "custom_components.elevenlabs_agent.async_sync_tools",
        AsyncMock(side_effect=httpx.ConnectError("down")),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert len(_entities_for(hass, config_entry)) == 9


async def test_no_external_url_during_setup_is_not_ready(hass, config_entry):
    """The spec says setup raises ConfigEntryNotReady when the URL has gone."""
    with patch(
        "custom_components.elevenlabs_agent.async_sync_tools",
        AsyncMock(side_effect=NoURLAvailableError),
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_an_auth_error_during_setup_still_leaves_the_platforms_forwarded(
    hass, config_entry
):
    """Even the reauth path must not remove the sensors an existing install has."""
    with patch(
        "custom_components.elevenlabs_agent.async_sync_tools",
        AsyncMock(side_effect=ElevenLabsAuthError("revoked")),
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert len(_entities_for(hass, config_entry)) == 9


# --- Task 16: the sync_tools service ---------------------------------------


async def test_sync_tools_service_exists_without_a_loaded_entry(hass):
    """The service must be callable to retry a setup that failed."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, SERVICE_SYNC_TOOLS)


async def test_sync_tools_rejects_an_entry_that_is_not_loaded(hass, config_entry):
    """A config entry that never loaded has no runtime_data to sync from."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SYNC_TOOLS,
            {ATTR_CONFIG_ENTRY_ID: config_entry.entry_id},
            blocking=True,
        )


async def test_sync_tools_syncs_a_loaded_entry(hass, setup_entry):
    """Calling the service runs the same sync a reload would run."""
    with patch(
        "custom_components.elevenlabs_agent.services.async_sync_tools",
        AsyncMock(),
    ) as sync:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SYNC_TOOLS,
            {ATTR_CONFIG_ENTRY_ID: setup_entry.entry_id},
            blocking=True,
        )

    assert sync.call_args.args[1] == setup_entry


async def test_no_external_url_forwards_no_platform_and_recovers(hass, config_entry):
    """A retry after the URL comes back sets the platforms up once."""
    await hass.config.async_update(external_url=None)
    with patch("custom_components.elevenlabs_agent.async_sync_tools", AsyncMock()):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert _entities_for(hass, config_entry) == []

    await hass.config.async_update(external_url="https://home.example")
    with patch("custom_components.elevenlabs_agent.async_sync_tools", AsyncMock()):
        await hass.config_entries.async_reload(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert len(_entities_for(hass, config_entry)) == 9


async def test_a_reload_after_an_auth_error_loads_cleanly(hass, config_entry, caplog):
    """Reauth reloads the entry; the platforms must set up again without an error."""
    with patch(
        "custom_components.elevenlabs_agent.async_sync_tools",
        AsyncMock(side_effect=ElevenLabsAuthError("revoked")),
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    with patch("custom_components.elevenlabs_agent.async_sync_tools", AsyncMock()):
        await hass.config_entries.async_reload(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert len(_entities_for(hass, config_entry)) == 9
    assert "already been setup" not in caplog.text
