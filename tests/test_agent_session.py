"""Tests for one text-only agent turn over the WebSocket API, against a fake server."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.elevenlabs_agent.agent_session import (
    SessionError,
    TurnResult,
    async_run_turn,
)
from custom_components.elevenlabs_agent.const import CONF_AGENT_ID
from custom_components.elevenlabs_agent.elevenlabs_client import (
    ElevenLabsAuthError,
    ElevenLabsError,
)

# homeassistant 2026.9.1's http server sets self.app["hass"] as a plain key.
# aiohttp 3.14.3 raises NotAppKeyWarning for that pattern. pyproject.toml
# turns every warning into an error, so this module ignores this one category.
pytestmark = pytest.mark.filterwarnings(
    "ignore::aiohttp.web_exceptions.NotAppKeyWarning"
)

SIGNED = "custom_components.elevenlabs_agent.agent_session.async_get_signed_url"
Behaviour = Callable[[web.WebSocketResponse, list[dict[str, Any]]], Awaitable[None]]


def _run(
    hass, entry, text: str, dynamic_variables: dict[str, Any]
) -> Awaitable[TurnResult]:
    """Call async_run_turn with the entry's client and agent id."""
    return async_run_turn(
        hass,
        entry.runtime_data.client,
        entry.data[CONF_AGENT_ID],
        text,
        dynamic_variables,
    )


@pytest.fixture(autouse=True)
def fast_turns():
    """Shorten the idle and maximum turn times."""
    with (
        patch(
            "custom_components.elevenlabs_agent.agent_session.TURN_IDLE_SECONDS", 0.2
        ),
        patch("custom_components.elevenlabs_agent.agent_session.TURN_MAX_SECONDS", 2.0),
    ):
        yield


@pytest.fixture
async def fake(socket_enabled) -> AsyncGenerator[SimpleNamespace]:
    """Serve a fake ElevenLabs WebSocket endpoint. Each test sets `behave`."""
    state = SimpleNamespace(behave=None, received=[])

    async def handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await state.behave(ws, state.received)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/ws", handler)
    server = TestServer(app)
    await server.start_server()
    state.url = str(server.make_url("/ws"))
    with patch(SIGNED, AsyncMock(return_value=state.url)) as signed:
        state.signed = signed
        yield state
    await server.close()


def _metadata(conversation_id: str = "conv_1") -> dict[str, Any]:
    return {
        "type": "conversation_initiation_metadata",
        "conversation_initiation_metadata_event": {"conversation_id": conversation_id},
    }


def _response(text: str) -> dict[str, Any]:
    return {"type": "agent_response", "agent_response_event": {"agent_response": text}}


def _tool(name: str) -> dict[str, Any]:
    return {
        "type": "agent_tool_response",
        "agent_tool_response_event": {"tool_name": name, "is_error": False},
    }


async def _hold(ws: web.WebSocketResponse) -> None:
    """Keep the socket open until the client closes it."""
    await ws.receive()


def _standard(*frames: dict[str, Any]) -> Behaviour:
    """Read the initiation, send metadata, read the user message, send frames."""

    async def behave(ws: web.WebSocketResponse, received: list[dict[str, Any]]) -> None:
        received.append(await ws.receive_json())
        await ws.send_json(_metadata())
        received.append(await ws.receive_json())
        for frame in frames:
            await ws.send_json(frame)
        await _hold(ws)

    return behave


async def test_a_turn_returns_the_answer_and_the_tools(hass, setup_entry, fake):
    """One user message in; the agent's text, its tools and the id out."""
    fake.behave = _standard(_tool("ha_get_state"), _response(" Son 10 grados. "))

    result = await _run(hass, setup_entry, "Evento: frío", {"priority": "high"})

    assert result.conversation_id == "conv_1"
    assert result.responses == ["Son 10 grados."]
    assert result.tools == [{"tool_name": "ha_get_state", "is_error": False}]
    assert result.incomplete is False


async def test_the_first_frames_ask_for_text_only_and_carry_the_variables(
    hass, setup_entry, fake
):
    """The initiation sets the text-only override; then the user message follows."""
    fake.behave = _standard(_response("Vale."))

    await _run(hass, setup_entry, "Evento: puerta", {"event_type": "door"})

    init, user = fake.received
    assert init == {
        "type": "conversation_initiation_client_data",
        "conversation_config_override": {
            "conversation": {"text_only": True},
            "agent": {"first_message": ""},
        },
        "dynamic_variables": {"event_type": "door"},
    }
    assert user == {"type": "user_message", "text": "Evento: puerta"}


async def test_a_ping_gets_its_pong(hass, setup_entry, fake):
    """The server's keep-alive is answered with the same event id."""

    async def behave(ws, received) -> None:
        received.append(await ws.receive_json())
        await ws.send_json({"type": "ping", "ping_event": {"event_id": 7}})
        received.append(await ws.receive_json())
        await ws.send_json(_metadata())
        received.append(await ws.receive_json())
        await ws.send_json(_response("Vale."))
        await _hold(ws)

    fake.behave = behave

    await _run(hass, setup_entry, "x", {})

    assert fake.received[1] == {"type": "pong", "event_id": 7}


async def test_a_message_after_the_answer_keeps_the_turn_open(hass, setup_entry, fake):
    """The idle timer restarts on every message, so a late tool still counts."""

    async def behave(ws, received) -> None:
        received.append(await ws.receive_json())
        await ws.send_json(_metadata())
        received.append(await ws.receive_json())
        await ws.send_json(_response("Un momento."))
        await asyncio.sleep(0.1)
        await ws.send_json(_tool("ha_call_service"))
        await asyncio.sleep(0.1)
        await ws.send_json(_response("Hecho."))
        await _hold(ws)

    fake.behave = behave

    result = await _run(hass, setup_entry, "x", {})

    assert result.responses == ["Un momento.", "Hecho."]
    assert result.tools == [{"tool_name": "ha_call_service", "is_error": False}]


async def test_the_turn_ends_at_the_idle_deadline_not_the_maximum(
    hass, setup_entry, fake
):
    """After the answer, the turn ends at the idle deadline, not the maximum."""
    fake.behave = _standard(_response("Vale."))

    start = asyncio.get_running_loop().time()
    await _run(hass, setup_entry, "x", {})
    elapsed = asyncio.get_running_loop().time() - start

    assert elapsed < 1.0


async def test_no_answer_within_the_maximum_is_incomplete(hass, setup_entry, fake):
    """At the maximum the turn returns what arrived, marked incomplete."""

    async def behave(ws, received) -> None:
        received.append(await ws.receive_json())
        await ws.send_json(_metadata())
        received.append(await ws.receive_json())
        await _hold(ws)

    fake.behave = behave

    result = await _run(hass, setup_entry, "x", {})

    assert result.incomplete is True
    assert result.responses == []


async def test_exhausted_credits_are_named(hass, setup_entry, fake):
    """The close ElevenLabs sends without credits becomes quota_exceeded."""

    async def behave(ws, received) -> None:
        received.append(await ws.receive_json())
        await ws.close(
            code=3000, message=b"[quota_exceeded] You've run out of credits."
        )

    fake.behave = behave

    with pytest.raises(SessionError) as err:
        await _run(hass, setup_entry, "x", {})

    assert err.value.reason == "quota_exceeded"


async def test_an_agent_response_before_the_metadata_is_not_the_answer(
    hass, setup_entry, fake
):
    """
    A frame that arrives before the user message is sent is not the reply.

    The override in the initiation is the real defence against the agent's
    own greeting (see test_the_initiation_overrides_the_greeting_away). This
    covers the narrower, always-sound guarantee: nothing is recorded before
    this turn's user message went out. A frame the server pushes only after
    metadata, ahead of the real answer, cannot be told apart from a
    genuinely fast answer without the unconfirmed user_transcript echo
    (spec OPEN item 2), so that race is not covered here.
    """

    async def behave(ws, received) -> None:
        received.append(await ws.receive_json())
        await ws.send_json(_response("Hola, ¿en qué te ayudo?"))
        await ws.send_json(_metadata())
        received.append(await ws.receive_json())
        await ws.send_json(_response("Son 10 grados."))
        await _hold(ws)

    fake.behave = behave

    result = await _run(hass, setup_entry, "x", {})

    assert result.responses == ["Son 10 grados."]


async def test_the_initiation_overrides_the_greeting_away(hass, setup_entry, fake):
    """The override silences the agent's own greeting, not only text_only."""
    fake.behave = _standard(_response("Vale."))

    await _run(hass, setup_entry, "x", {})

    init, _user = fake.received
    assert init["conversation_config_override"] == {
        "conversation": {"text_only": True},
        "agent": {"first_message": ""},
    }


async def test_pings_do_not_keep_the_turn_open(hass, setup_entry, fake):
    """Frequent pings are not content: they do not reset the idle timer."""

    async def behave(ws, received) -> None:
        received.append(await ws.receive_json())
        await ws.send_json(_metadata())
        received.append(await ws.receive_json())
        await ws.send_json(_response("Vale."))
        for event_id in range(60):
            await asyncio.sleep(0.05)
            await ws.send_json({"type": "ping", "ping_event": {"event_id": event_id}})
        await _hold(ws)

    fake.behave = behave

    start = asyncio.get_running_loop().time()
    result = await _run(hass, setup_entry, "x", {})
    elapsed = asyncio.get_running_loop().time() - start

    assert result.responses == ["Vale."]
    assert result.incomplete is False
    assert elapsed < 1.0


async def test_a_pending_tool_keeps_the_turn_open_past_the_idle_time(
    hass, setup_entry, fake
):
    """A tool call in flight holds the turn open longer than the idle time."""

    async def behave(ws, received) -> None:
        received.append(await ws.receive_json())
        await ws.send_json(_metadata())
        received.append(await ws.receive_json())
        await ws.send_json(_response("Un momento."))
        await ws.send_json(
            {
                "type": "agent_tool_request",
                "agent_tool_request_event": {"tool_name": "ha_call_service"},
            }
        )
        await asyncio.sleep(0.5)
        await ws.send_json(_tool("ha_call_service"))
        await _hold(ws)

    fake.behave = behave

    result = await _run(hass, setup_entry, "x", {})

    assert result.responses == ["Un momento."]
    assert result.tools == [{"tool_name": "ha_call_service", "is_error": False}]
    assert result.incomplete is False


async def test_a_pending_tool_past_the_maximum_is_incomplete(hass, setup_entry, fake):
    """The idle rule never closes a pending tool, but the maximum still does."""

    async def behave(ws, received) -> None:
        received.append(await ws.receive_json())
        await ws.send_json(_metadata())
        received.append(await ws.receive_json())
        await ws.send_json(_response("Un momento."))
        await ws.send_json(
            {
                "type": "agent_tool_request",
                "agent_tool_request_event": {"tool_call_id": "call_1"},
            }
        )
        await _hold(ws)

    fake.behave = behave

    result = await _run(hass, setup_entry, "x", {})

    assert result.incomplete is True
    assert result.responses == ["Un momento."]
    assert result.tools == []


async def test_agent_response_complete_ends_the_turn_before_the_idle_time(
    hass, setup_entry, fake
):
    """agent_response_complete ends the turn at once when nothing is pending."""

    async def behave(ws, received) -> None:
        received.append(await ws.receive_json())
        await ws.send_json(_metadata())
        received.append(await ws.receive_json())
        await ws.send_json(_response("Vale."))
        await ws.send_json({"type": "agent_response_complete"})
        await _hold(ws)

    fake.behave = behave

    start = asyncio.get_running_loop().time()
    result = await _run(hass, setup_entry, "x", {})
    elapsed = asyncio.get_running_loop().time() - start

    assert result.responses == ["Vale."]
    assert elapsed < 0.1


async def test_agent_response_complete_waits_for_a_pending_tool(
    hass, setup_entry, fake
):
    """agent_response_complete does not end the turn while a tool is pending."""

    async def behave(ws, received) -> None:
        received.append(await ws.receive_json())
        await ws.send_json(_metadata())
        received.append(await ws.receive_json())
        await ws.send_json(_response("Un momento."))
        await ws.send_json(
            {
                "type": "agent_tool_request",
                "agent_tool_request_event": {"tool_name": "ha_call_service"},
            }
        )
        await ws.send_json({"type": "agent_response_complete"})
        await asyncio.sleep(0.3)
        await ws.send_json(_tool("ha_call_service"))
        await _hold(ws)

    fake.behave = behave

    result = await _run(hass, setup_entry, "x", {})

    assert result.tools == [{"tool_name": "ha_call_service", "is_error": False}]
    assert result.incomplete is False


async def test_a_close_after_a_tool_but_no_text_is_incomplete_not_failed(
    hass, setup_entry, fake
):
    """A conversation that ran and used a tool, then closed, is not a failure."""

    async def behave(ws, received) -> None:
        received.append(await ws.receive_json())
        await ws.send_json(_metadata())
        received.append(await ws.receive_json())
        await ws.send_json(_tool("ha_call_service"))
        await ws.close(code=1000, message=b"done")

    fake.behave = behave

    result = await _run(hass, setup_entry, "x", {})

    assert result.conversation_id == "conv_1"
    assert result.tools == [{"tool_name": "ha_call_service", "is_error": False}]
    assert result.responses == []
    assert result.incomplete is True


async def test_a_close_right_after_the_answer_still_returns_it(hass, setup_entry, fake):
    """The server closing once it has answered is not a failed delivery."""

    async def behave(ws, received) -> None:
        received.append(await ws.receive_json())
        await ws.send_json(_metadata())
        received.append(await ws.receive_json())
        await ws.send_json(_response("Vale."))
        await ws.close(code=1000, message=b"done")

    fake.behave = behave

    result = await _run(hass, setup_entry, "x", {})

    assert result.responses == ["Vale."]


async def test_another_close_before_an_answer_is_closed(hass, setup_entry, fake):
    """Any other early close is a failed delivery."""

    async def behave(ws, received) -> None:
        received.append(await ws.receive_json())
        await ws.close(code=1011, message=b"internal")

    fake.behave = behave

    with pytest.raises(SessionError) as err:
        await _run(hass, setup_entry, "x", {})

    assert err.value.reason == "closed"


@pytest.mark.parametrize(
    ("error", "reason"),
    [(ElevenLabsAuthError("401"), "refused"), (ElevenLabsError("500"), "unreachable")],
)
async def test_a_signed_url_failure_is_named(hass, setup_entry, fake, error, reason):
    """No URL, no conversation."""
    fake.signed.side_effect = error

    with pytest.raises(SessionError) as err:
        await _run(hass, setup_entry, "x", {})

    assert err.value.reason == reason


async def test_a_server_that_is_not_there_is_unreachable(hass, setup_entry, fake):
    """A refused connection is a failed delivery."""
    fake.signed.return_value = "ws://127.0.0.1:9/ws"

    with pytest.raises(SessionError) as err:
        await _run(hass, setup_entry, "x", {})

    assert err.value.reason == "unreachable"


async def test_the_signed_url_never_reaches_the_log(hass, setup_entry, fake, caplog):
    """The token in the URL opens a conversation with the agent."""
    fake.behave = _standard(_response("Vale."))

    await _run(hass, setup_entry, "x", {})

    assert fake.url not in caplog.text


async def test_a_non_json_frame_is_a_protocol_error(hass, setup_entry, fake):
    """A frame that is not JSON ends the turn as a protocol error, not a crash."""

    async def behave(ws, received) -> None:
        received.append(await ws.receive_json())
        await ws.send_json(_metadata())
        received.append(await ws.receive_json())
        await ws.send_str("not json")
        await _hold(ws)

    fake.behave = behave

    with pytest.raises(SessionError) as err:
        await _run(hass, setup_entry, "x", {})

    assert err.value.reason == "protocol"


async def test_a_ping_without_an_event_id_is_a_protocol_error(hass, setup_entry, fake):
    """A malformed ping is a protocol error, not an unhandled KeyError."""

    async def behave(ws, received) -> None:
        received.append(await ws.receive_json())
        await ws.send_json(_metadata())
        received.append(await ws.receive_json())
        await ws.send_json({"type": "ping", "ping_event": {}})
        await _hold(ws)

    fake.behave = behave

    with pytest.raises(SessionError) as err:
        await _run(hass, setup_entry, "x", {})

    assert err.value.reason == "protocol"


async def test_an_agent_response_without_text_is_a_protocol_error(
    hass, setup_entry, fake
):
    """A response frame missing its text field is a protocol error."""

    async def behave(ws, received) -> None:
        received.append(await ws.receive_json())
        await ws.send_json(_metadata())
        received.append(await ws.receive_json())
        await ws.send_json({"type": "agent_response", "agent_response_event": {}})
        await _hold(ws)

    fake.behave = behave

    with pytest.raises(SessionError) as err:
        await _run(hass, setup_entry, "x", {})

    assert err.value.reason == "protocol"


@pytest.mark.parametrize(
    ("status", "reason"),
    [(401, "refused"), (403, "refused"), (429, "busy")],
)
async def test_a_handshake_failure_is_named(
    hass, setup_entry, socket_enabled, status, reason
):
    """A rejected handshake is refused or busy, not a generic unreachable."""

    async def handler(request: web.Request) -> web.Response:
        return web.Response(status=status)

    app = web.Application()
    app.router.add_get("/ws", handler)
    server = TestServer(app)
    await server.start_server()
    url = str(server.make_url("/ws"))

    with (
        patch(SIGNED, AsyncMock(return_value=url)),
        pytest.raises(SessionError) as err,
    ):
        await _run(hass, setup_entry, "x", {})
    await server.close()

    assert err.value.reason == reason


class _HangingConnect:
    """An async context manager whose entry never returns, to test the outer bound."""

    async def __aenter__(self) -> None:
        await asyncio.sleep(3600)

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


async def test_a_stalled_connect_is_bounded_past_the_maximum(hass, setup_entry, fake):
    """A connect that never finishes still ends within the maximum plus its margin."""
    fake.signed.return_value = fake.url

    class _Session:
        def ws_connect(self, url: str) -> _HangingConnect:  # noqa: ARG002
            return _HangingConnect()

    with patch(
        "custom_components.elevenlabs_agent.agent_session.async_get_clientsession",
        return_value=_Session(),
    ):
        start = asyncio.get_running_loop().time()
        with pytest.raises(SessionError) as err:
            await _run(hass, setup_entry, "x", {})
        elapsed = asyncio.get_running_loop().time() - start

    assert err.value.reason == "unreachable"
    assert elapsed < 10.0
