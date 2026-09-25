"""Tests for the ElevenLabs client wrapper."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from elevenlabs.core import ApiError

from custom_components.elevenlabs_agent.elevenlabs_client import (
    ElevenLabsAuthError,
    ElevenLabsError,
    ElevenLabsNotFoundError,
    ElevenLabsScopeError,
    async_create_tool,
    async_delete_tool,
    async_get_agent,
    async_get_client,
    async_get_tool,
    async_list_agents,
    async_list_tools,
    async_patch_agent,
    async_update_tool,
)

_TOOL_CONFIG = {
    "type": "webhook",
    "name": "ha_get_state",
    "description": "Get the state of a Home Assistant entity.",
    "api_schema": {"url": "https://example.invalid/webhook"},
}


async def test_client_uses_the_shared_home_assistant_httpx_client(hass):
    """No second connection pool is created."""
    with patch(
        "custom_components.elevenlabs_agent.elevenlabs_client.get_async_client"
    ) as shared:
        async_get_client(hass, "sk_test")

    assert shared.called


async def test_update_sends_a_real_request_model(hass):
    """
    The wrapper builds the type the SDK declares, not a bare dict.

    This is the test a mock cannot do. It asserts the object handed to the
    SDK is the type its signature requires, so a wrong keyword or a missing
    wrapper fails here instead of in production.
    """
    from elevenlabs.types import ToolRequestModel

    client = AsyncMock()

    await async_update_tool(client, "tool_x", _TOOL_CONFIG)

    sent = client.conversational_ai.tools.update.call_args
    assert sent.args == ("tool_x",) or sent.kwargs["tool_id"] == "tool_x"
    assert isinstance(sent.kwargs["request"], ToolRequestModel)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ElevenLabsAuthError),
        (403, ElevenLabsScopeError),
        (404, ElevenLabsNotFoundError),
    ],
)
async def test_api_errors_are_translated(hass, status, expected):
    """Sync treats 404 as a recreate, so each status needs its own class."""
    client = AsyncMock()
    client.conversational_ai.tools.update.side_effect = ApiError(
        status_code=status, body={"detail": "no"}
    )

    with pytest.raises(expected):
        await async_update_tool(client, "tool_x", _TOOL_CONFIG)


async def test_list_agents_calls_the_sdk_with_no_arguments(hass):
    """The config flow lists the whole workspace, unfiltered."""
    client = AsyncMock()
    client.conversational_ai.agents.list.return_value = "sentinel"

    result = await async_list_agents(client)

    client.conversational_ai.agents.list.assert_awaited_once_with()
    assert result == "sentinel"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ElevenLabsAuthError),
        (403, ElevenLabsScopeError),
        (404, ElevenLabsNotFoundError),
    ],
)
async def test_list_agents_translates_api_errors(hass, status, expected):
    """Listing agents maps status codes the same way every other call does."""
    client = AsyncMock()
    client.conversational_ai.agents.list.side_effect = ApiError(
        status_code=status, body={"detail": "no"}
    )

    with pytest.raises(expected):
        await async_list_agents(client)


# --- M4: every call site wraps a raw httpx.HTTPError -------------------------


async def _list_tools(client: Any) -> Any:
    return await async_list_tools(client)


async def _get_tool(client: Any) -> Any:
    return await async_get_tool(client, "tool_x")


async def _create_tool(client: Any) -> Any:
    return await async_create_tool(client, _TOOL_CONFIG)


async def _update_tool(client: Any) -> Any:
    return await async_update_tool(client, "tool_x", _TOOL_CONFIG)


async def _delete_tool(client: Any) -> Any:
    return await async_delete_tool(client, "tool_x")


async def _list_agents(client: Any) -> Any:
    return await async_list_agents(client)


async def _get_agent(client: Any) -> Any:
    return await async_get_agent(client, "agent_x")


async def _patch_agent(client: Any) -> Any:
    return await async_patch_agent(client, "agent_x", name="new")


@pytest.mark.parametrize(
    "call",
    [
        _list_tools,
        _get_tool,
        _create_tool,
        _update_tool,
        _delete_tool,
        _list_agents,
        _get_agent,
        _patch_agent,
    ],
)
async def test_a_transport_error_is_wrapped_in_elevenlabs_error(hass, call):
    """A DNS or connect failure reaches the caller as ElevenLabsError, not raw httpx."""
    client = AsyncMock()
    for endpoint in (
        client.conversational_ai.tools.list,
        client.conversational_ai.tools.get,
        client.conversational_ai.tools.create,
        client.conversational_ai.tools.update,
        client.conversational_ai.tools.delete,
        client.conversational_ai.agents.list,
        client.conversational_ai.agents.get,
        client.conversational_ai.agents.update,
    ):
        endpoint.side_effect = httpx.ConnectError("down")

    with pytest.raises(ElevenLabsError):
        await call(client)
