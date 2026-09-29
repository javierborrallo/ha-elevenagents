"""Run one text-only agent turn over the ElevenLabs WebSocket API."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_AGENT_ID, TURN_IDLE_SECONDS, TURN_MAX_SECONDS
from .elevenlabs_client import (
    ElevenLabsAuthError,
    ElevenLabsError,
    async_get_signed_url,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import ElevenLabsAgentConfigEntry

_LOGGER = logging.getLogger(__name__)

TEXT_ONLY = {"conversation": {"text_only": True}}
_ENDED = (
    aiohttp.WSMsgType.CLOSE,
    aiohttp.WSMsgType.CLOSING,
    aiohttp.WSMsgType.CLOSED,
    aiohttp.WSMsgType.ERROR,
)


class SessionError(Exception):
    """
    A turn that could not run.

    `reason` is refused, unreachable, quota_exceeded or closed.
    """

    def __init__(self, reason: str) -> None:
        """Store the reason code."""
        super().__init__(reason)
        self.reason = reason


@dataclass
class TurnResult:
    """What the agent said and did in one turn."""

    conversation_id: str | None = None
    responses: list[str] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    incomplete: bool = False


def _close_reason(message: aiohttp.WSMessage) -> str:
    """Return quota_exceeded for the no-credits close, else closed."""
    extra = message.extra if isinstance(message.extra, str) else ""
    return "quota_exceeded" if "quota_exceeded" in extra else "closed"


async def _collect(ws: aiohttp.ClientWebSocketResponse, text: str) -> TurnResult:
    """Send the user message after the metadata, then read until the turn ends."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + TURN_MAX_SECONDS
    result = TurnResult()
    sent = False
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            result.incomplete = True
            return result
        wait = min(remaining, TURN_IDLE_SECONDS) if result.responses else remaining
        try:
            message = await ws.receive(timeout=wait)
        except TimeoutError:
            if not result.responses:
                result.incomplete = True
            return result
        if message.type in _ENDED:
            if result.responses:
                return result
            raise SessionError(_close_reason(message))
        if message.type is not aiohttp.WSMsgType.TEXT:
            continue
        payload = json.loads(message.data)
        kind = payload.get("type")
        if kind == "ping":
            await ws.send_json(
                {"type": "pong", "event_id": payload["ping_event"]["event_id"]}
            )
        elif kind == "conversation_initiation_metadata":
            event = payload.get("conversation_initiation_metadata_event", {})
            result.conversation_id = event.get("conversation_id")
            if not sent:
                await ws.send_json({"type": "user_message", "text": text})
                sent = True
        elif kind == "agent_response":
            result.responses.append(
                str(payload["agent_response_event"]["agent_response"]).strip()
            )
        elif kind == "agent_tool_response":
            event = payload.get("agent_tool_response_event") or {}
            result.tools.append(
                {
                    "tool_name": event.get("tool_name"),
                    "is_error": bool(event.get("is_error")),
                }
            )


async def async_run_turn(
    hass: HomeAssistant,
    entry: ElevenLabsAgentConfigEntry,
    text: str,
    dynamic_variables: dict[str, Any],
) -> TurnResult:
    """
    Open a text-only conversation, send one user message, return the turn.

    Raise SessionError when the conversation cannot start or closes before
    the agent answers.
    """
    try:
        url = await async_get_signed_url(
            entry.runtime_data.client, entry.data[CONF_AGENT_ID]
        )
    except ElevenLabsAuthError as err:
        reason = "refused"
        raise SessionError(reason) from err
    except ElevenLabsError as err:
        reason = "unreachable"
        raise SessionError(reason) from err
    try:
        async with async_get_clientsession(hass).ws_connect(url) as ws:
            await ws.send_json(
                {
                    "type": "conversation_initiation_client_data",
                    "conversation_config_override": TEXT_ONLY,
                    "dynamic_variables": dynamic_variables,
                }
            )
            return await _collect(ws, text)
    except aiohttp.ClientError as err:
        _LOGGER.debug("The agent session failed: %s", type(err).__name__)
        reason = "unreachable"
        raise SessionError(reason) from err
