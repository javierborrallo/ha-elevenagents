"""Run one text-only agent turn over the ElevenLabs WebSocket API."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import TURN_IDLE_SECONDS, TURN_MAX_SECONDS
from .elevenlabs_client import (
    ElevenLabsAuthError,
    ElevenLabsError,
    async_get_signed_url,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from elevenlabs import AsyncElevenLabs
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

CONVERSATION_OVERRIDE = {
    "conversation": {"text_only": True},
    "agent": {"first_message": ""},
}
# Extra time, over the turn's own maximum, allowed for the connect and the
# close handshake around it.
CONNECT_CLOSE_MARGIN = 5.0
_ENDED = (
    aiohttp.WSMsgType.CLOSE,
    aiohttp.WSMsgType.CLOSING,
    aiohttp.WSMsgType.CLOSED,
    aiohttp.WSMsgType.ERROR,
)


class SessionError(Exception):
    """
    A turn that could not run.

    `reason` is refused, busy, unreachable, quota_exceeded, protocol or closed.
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


def _tool_id(event: dict[str, Any]) -> str | None:
    """Return the tool call's id, reading the field defensively."""
    return event.get("tool_call_id") or event.get("tool_name")


def _record_tool(state: _TurnState, event: dict[str, Any], tool_id: str | None) -> None:
    """Append the tool to the result once per call id."""
    if tool_id is not None and tool_id in state.answered_tools:
        return
    state.result.tools.append(
        {"tool_name": event.get("tool_name"), "is_error": bool(event.get("is_error"))}
    )
    if tool_id is not None:
        state.answered_tools.add(tool_id)


@dataclass
class _TurnState:
    """The turn so far: its result, whether the user message is out, what is pending."""

    result: TurnResult = field(default_factory=TurnResult)
    sent: bool = False
    last_content: float | None = None
    pending_tools: set[str] = field(default_factory=set)
    answered_tools: set[str] = field(default_factory=set)
    on_started: Callable[[str], None] | None = None


def _idle_wait(state: _TurnState, remaining: float, now: float) -> float:
    """Return how long to wait for the next frame."""
    if state.last_content is None or state.pending_tools:
        return remaining
    return min(remaining, state.last_content + TURN_IDLE_SECONDS - now)


def _mark_ended(state: _TurnState) -> None:
    """Set incomplete: nothing arrived yet, or a tool is still pending."""
    state.result.incomplete = state.last_content is None or bool(state.pending_tools)


async def _apply_frame(
    ws: aiohttp.ClientWebSocketResponse,
    state: _TurnState,
    text: str,
    payload: dict[str, Any],
) -> bool:
    """Update the turn from one frame. Return True once the turn ends."""
    loop = asyncio.get_running_loop()
    kind = payload.get("type")
    if kind == "ping":
        await ws.send_json(
            {"type": "pong", "event_id": payload["ping_event"]["event_id"]}
        )
    elif kind == "conversation_initiation_metadata":
        event = payload.get("conversation_initiation_metadata_event", {})
        conversation_id = event.get("conversation_id")
        # A start frame without an id ends the turn before the user message.
        if not isinstance(conversation_id, str) or not conversation_id:
            reason = "protocol"
            raise SessionError(reason)
        state.result.conversation_id = conversation_id
        if state.on_started is not None:
            state.on_started(conversation_id)
        if not state.sent:
            await ws.send_json({"type": "user_message", "text": text})
            state.sent = True
    elif kind == "agent_response":
        if state.sent:
            state.result.responses.append(
                str(payload["agent_response_event"]["agent_response"]).strip()
            )
            state.last_content = loop.time()
    elif kind == "agent_tool_request":
        event = payload.get("agent_tool_request") or {}
        tool_id = _tool_id(event)
        if tool_id is not None:
            state.pending_tools.add(tool_id)
    elif kind in ("agent_tool_response", "agent_tool_response_full_payload"):
        event = payload.get(kind) or {}
        tool_id = _tool_id(event)
        _record_tool(state, event, tool_id)
        state.last_content = loop.time()
        state.pending_tools.discard(tool_id)
    elif kind == "agent_response_complete" and not state.pending_tools:
        return True
    return False


async def _collect(
    ws: aiohttp.ClientWebSocketResponse,
    text: str,
    on_started: Callable[[str], None] | None,
) -> TurnResult:
    """Send the user message after the metadata, then read until the turn ends."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + TURN_MAX_SECONDS
    state = _TurnState(on_started=on_started)
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            _mark_ended(state)
            return state.result
        wait = _idle_wait(state, remaining, loop.time())
        try:
            message = await ws.receive(timeout=wait)
        except TimeoutError:
            _mark_ended(state)
            return state.result
        if message.type in _ENDED:
            if state.result.conversation_id is not None:
                state.result.incomplete = True
                return state.result
            raise SessionError(_close_reason(message))
        if message.type is not aiohttp.WSMsgType.TEXT:
            continue
        payload = json.loads(message.data)
        if await _apply_frame(ws, state, text, payload):
            return state.result


_HANDSHAKE_AUTH_STATUSES = (401, 403)
_HANDSHAKE_BUSY_STATUS = 429


def _handshake_reason(err: aiohttp.WSServerHandshakeError) -> str:
    """Return refused for an auth failure, busy for a concurrency limit."""
    if err.status in _HANDSHAKE_AUTH_STATUSES:
        return "refused"
    if err.status == _HANDSHAKE_BUSY_STATUS:
        return "busy"
    return "unreachable"


async def async_run_turn(  # noqa: PLR0913 - each argument is part of one turn
    hass: HomeAssistant,
    client: AsyncElevenLabs,
    agent_id: str,
    text: str,
    dynamic_variables: dict[str, Any],
    *,
    on_started: Callable[[str], None] | None = None,
) -> TurnResult:
    """
    Open a text-only conversation, send one user message, return the turn.

    Raise SessionError when the conversation cannot start or closes before
    the agent answers. on_started, when given, is called with the
    conversation id as soon as it is known, before the user message is sent.
    """
    try:
        url = await async_get_signed_url(client, agent_id)
    except ElevenLabsAuthError as err:
        reason = "refused"
        raise SessionError(reason) from err
    except ElevenLabsError as err:
        reason = "unreachable"
        raise SessionError(reason) from err
    try:
        async with (
            asyncio.timeout(TURN_MAX_SECONDS + CONNECT_CLOSE_MARGIN),
            async_get_clientsession(hass).ws_connect(url) as ws,
        ):
            await ws.send_json(
                {
                    "type": "conversation_initiation_client_data",
                    "conversation_config_override": CONVERSATION_OVERRIDE,
                    "dynamic_variables": dynamic_variables,
                }
            )
            return await _collect(ws, text, on_started)
    except TimeoutError as err:
        reason = "unreachable"
        raise SessionError(reason) from err
    except aiohttp.WSServerHandshakeError as err:
        reason = _handshake_reason(err)
        raise SessionError(reason) from err
    except aiohttp.ClientError as err:
        _LOGGER.debug("The agent session failed: %s", type(err).__name__)
        reason = "unreachable"
        raise SessionError(reason) from err
    except (ValueError, KeyError, TypeError) as err:
        _LOGGER.debug("The agent session sent an unexpected frame: %s", err)
        reason = "protocol"
        raise SessionError(reason) from err
