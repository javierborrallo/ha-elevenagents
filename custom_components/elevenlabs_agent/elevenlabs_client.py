"""
Client wrapper for the official ElevenLabs SDK.

This is the only module that talks to the ElevenLabs API.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

from elevenlabs import AsyncElevenLabs
from elevenlabs.core import ApiError
from elevenlabs.types import (
    GetAgentResponseModel,
    GetAgentsPageResponseModel,
    ToolRequestModel,
    ToolResponseModel,
    ToolsResponseModel,
)
from homeassistant.helpers.httpx_client import get_async_client

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class ElevenLabsError(Exception):
    """Base error for a failed call to the ElevenLabs API."""


class ElevenLabsAuthError(ElevenLabsError):
    """The API key is rejected. Matches HTTP 401."""


class ElevenLabsScopeError(ElevenLabsError):
    """The API key lacks the required scope. Matches HTTP 403."""


class ElevenLabsNotFoundError(ElevenLabsError):
    """The requested resource does not exist. Matches HTTP 404."""


def _translate(err: ApiError) -> ElevenLabsError:
    """Map an ApiError status code onto the matching exception class."""
    if err.status_code == HTTPStatus.UNAUTHORIZED:
        return ElevenLabsAuthError(str(err))
    if err.status_code == HTTPStatus.FORBIDDEN:
        return ElevenLabsScopeError(str(err))
    if err.status_code == HTTPStatus.NOT_FOUND:
        return ElevenLabsNotFoundError(str(err))
    return ElevenLabsError(str(err))


def async_get_client(hass: HomeAssistant, api_key: str) -> AsyncElevenLabs:
    """Return an ElevenLabs client using the shared Home Assistant httpx client."""
    return AsyncElevenLabs(api_key=api_key, httpx_client=get_async_client(hass))


async def async_list_tools(client: AsyncElevenLabs) -> ToolsResponseModel:
    """List the tools in the user's workspace."""
    try:
        result = await client.conversational_ai.tools.list()
    except ApiError as err:
        raise _translate(err) from err
    return cast("ToolsResponseModel", result)


async def async_get_tool(client: AsyncElevenLabs, tool_id: str) -> ToolResponseModel:
    """Read back one tool by its ID."""
    try:
        result = await client.conversational_ai.tools.get(tool_id)
    except ApiError as err:
        raise _translate(err) from err
    return cast("ToolResponseModel", result)


async def async_create_tool(
    client: AsyncElevenLabs, config: dict[str, Any]
) -> ToolResponseModel:
    """Create a tool from its config and return the created tool."""
    request = ToolRequestModel.model_validate({"tool_config": config})
    try:
        result = await client.conversational_ai.tools.create(request=request)
    except ApiError as err:
        raise _translate(err) from err
    return cast("ToolResponseModel", result)


async def async_update_tool(
    client: AsyncElevenLabs, tool_id: str, config: dict[str, Any]
) -> ToolResponseModel:
    """Update an existing tool with a new config."""
    request = ToolRequestModel.model_validate({"tool_config": config})
    try:
        result = await client.conversational_ai.tools.update(tool_id, request=request)
    except ApiError as err:
        raise _translate(err) from err
    return cast("ToolResponseModel", result)


async def async_delete_tool(client: AsyncElevenLabs, tool_id: str) -> Any:
    """Delete a tool by its ID."""
    try:
        return await client.conversational_ai.tools.delete(tool_id)
    except ApiError as err:
        raise _translate(err) from err


async def async_list_agents(client: AsyncElevenLabs) -> GetAgentsPageResponseModel:
    """List the agents in the user's workspace."""
    try:
        result = await client.conversational_ai.agents.list()
    except ApiError as err:
        raise _translate(err) from err
    return cast("GetAgentsPageResponseModel", result)


async def async_get_agent(
    client: AsyncElevenLabs, agent_id: str
) -> GetAgentResponseModel:
    """Read back one agent by its ID."""
    try:
        result = await client.conversational_ai.agents.get(agent_id)
    except ApiError as err:
        raise _translate(err) from err
    return cast("GetAgentResponseModel", result)


async def async_patch_agent(
    client: AsyncElevenLabs, agent_id: str, **fields: Any
) -> GetAgentResponseModel:
    """Update an agent with the given keyword fields."""
    try:
        result = await client.conversational_ai.agents.update(agent_id, **fields)
    except ApiError as err:
        raise _translate(err) from err
    return cast("GetAgentResponseModel", result)
