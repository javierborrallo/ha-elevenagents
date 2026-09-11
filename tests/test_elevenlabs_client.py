"""Tests for the ElevenLabs client wrapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from elevenlabs.core import ApiError

from custom_components.elevenlabs_agent.elevenlabs_client import (
    ElevenLabsAuthError,
    ElevenLabsNotFoundError,
    ElevenLabsScopeError,
    async_get_client,
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
