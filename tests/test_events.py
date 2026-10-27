"""Tests for the send_event service: limits, context, delivery and the result."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.elevenagents.agent_session import SessionError, TurnResult
from custom_components.elevenagents.const import (
    DOMAIN,
    EVENT_DROPPED,
    EVENT_FAILED,
    EVENT_REPLY,
    OPT_CONTROL_ENTITIES,
    OPT_EVENT_CONTINUITY,
    OPT_EVENT_COOLDOWN,
    OPT_EVENT_HOURLY_LIMIT,
    OPT_READ_ENTITIES,
    OPT_SENSITIVE,
)
from custom_components.elevenagents.webhook import async_write

if TYPE_CHECKING:
    from homeassistant.helpers import entity_registry as er

pytestmark = pytest.mark.filterwarnings(
    "ignore::aiohttp.web_exceptions.NotAppKeyWarning"
)

RUN = "custom_components.elevenagents.events.async_run_turn"


@pytest.fixture(autouse=True)
def mock_sync_tools():
    """Keep the registry listener's debounced sync off the network."""
    with patch("custom_components.elevenagents.expose.async_sync_tools", AsyncMock()):
        yield


@pytest.fixture
def turn():
    """Replace the WebSocket turn with a mock that answers."""
    with patch(
        RUN,
        AsyncMock(
            return_value=TurnResult(
                conversation_id="conv_1",
                responses=["Te aviso."],
                tools=[{"tool_name": "ha_get_state", "is_error": False}],
            )
        ),
    ) as mock:
        yield mock


@pytest.fixture
def heard(hass):
    """Collect the three events the service fires."""
    got: dict[str, list[dict[str, Any]]] = {
        EVENT_REPLY: [],
        EVENT_DROPPED: [],
        EVENT_FAILED: [],
    }
    for name, bucket in got.items():

        @callback
        def _listen(event, bucket=bucket) -> None:
            bucket.append(dict(event.data))

        hass.bus.async_listen(name, _listen)
    return got


async def _send(hass, entry, **data: Any) -> dict[str, Any]:
    response = await hass.services.async_call(
        DOMAIN,
        "send_event",
        {"config_entry_id": entry.entry_id, **data},
        blocking=True,
        return_response=True,
    )
    await hass.async_block_till_done()
    return dict(response or {})


def _text(turn: AsyncMock, call: int = -1) -> str:
    return str(turn.await_args_list[call].args[3])


def _variables(turn: AsyncMock, call: int = -1) -> dict[str, Any]:
    return dict(turn.await_args_list[call].args[4])


async def test_an_event_is_delivered_and_answered(hass, setup_entry, turn, heard):
    """The service returns the turn and fires the reply event."""
    result = await _send(
        hass,
        setup_entry,
        message="It is cold.",
        event_type="cold",
        priority="high",
        data={"grados": 10},
    )

    assert result == {
        "outcome": "delivered",
        "conversation_id": "conv_1",
        "responses": ["Te aviso."],
        "tools": [{"tool_name": "ha_get_state", "is_error": False}],
        "incomplete": False,
    }
    assert "It is cold." in _text(turn)
    assert _variables(turn) == {"grados": 10, "event_type": "cold", "priority": "high"}
    assert heard[EVENT_REPLY] == [
        {"entry_id": setup_entry.entry_id, "event_type": "cold", **result}
    ]


async def test_a_readable_entity_adds_its_state(
    hass, setup_entry, entity_registry, turn
):
    """The agent reads the entity without a tool call."""
    entry = entity_registry.async_get_or_create("binary_sensor", "demo", "door")
    hass.states.async_set(entry.entity_id, "on", {"friendly_name": "Door"})
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_READ_ENTITIES: [entry.id]}
    )

    await _send(hass, setup_entry, message="It opened.", entity_id=entry.entity_id)

    assert entry.entity_id in _text(turn)
    assert '"state": "on"' in _text(turn)
    assert _variables(turn)["entity_id"] == entry.entity_id


async def test_an_entity_the_agent_may_not_read_is_refused(hass, setup_entry, turn):
    """The service never shows the agent what it may not read."""
    hass.states.async_set("lock.front", "locked")

    with pytest.raises(ServiceValidationError):
        await _send(hass, setup_entry, message="x", entity_id="lock.front")

    turn.assert_not_awaited()


async def test_too_much_data_is_refused(hass, setup_entry, turn):
    """Data is at most 4096 bytes as JSON."""
    with pytest.raises(ServiceValidationError):
        await _send(hass, setup_entry, message="x", data={"blob": "a" * 5000})

    turn.assert_not_awaited()


async def test_a_long_message_is_refused(hass, setup_entry, turn):
    """Message is at most 1000 characters."""
    with pytest.raises(Exception):  # noqa: B017, PT011 - voluptuous raises its own class
        await _send(hass, setup_entry, message="a" * 1001)

    turn.assert_not_awaited()


async def test_the_same_event_waits_for_its_cooldown(
    hass, setup_entry, turn, heard, freezer
):
    """Within five minutes the second one is dropped; after, it goes."""
    await _send(hass, setup_entry, message="x", event_type="door")
    second = await _send(hass, setup_entry, message="x", event_type="door")
    freezer.tick(timedelta(minutes=5, seconds=1))
    async_fire_time_changed(hass)
    third = await _send(hass, setup_entry, message="x", event_type="door")

    assert second == {"outcome": "dropped", "reason": "cooldown"}
    assert third["outcome"] == "delivered"
    assert heard[EVENT_DROPPED][0]["reason"] == "cooldown"
    assert turn.await_count == 2


async def test_a_high_priority_event_skips_the_cooldown(hass, setup_entry, turn):
    """A repeated alarm is information."""
    await _send(hass, setup_entry, message="x", event_type="alarm")
    again = await _send(
        hass, setup_entry, message="x", event_type="alarm", priority="high"
    )

    assert again["outcome"] == "delivered"


async def test_the_hourly_limit_holds_even_for_high_priority(hass, setup_entry, turn):
    """A loop never spends past the limit."""
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_EVENT_HOURLY_LIMIT: 2, OPT_EVENT_COOLDOWN: 0}
    )

    await _send(hass, setup_entry, message="a")
    await _send(hass, setup_entry, message="b")
    third = await _send(hass, setup_entry, message="c", priority="high")

    assert third == {"outcome": "dropped", "reason": "hourly_limit"}


async def test_a_failed_delivery_counts_for_nothing(hass, setup_entry, turn, heard):
    """It raises, fires the failure, and can be retried at once."""
    turn.side_effect = [SessionError("quota_exceeded"), turn.return_value]

    with pytest.raises(HomeAssistantError):
        await _send(hass, setup_entry, message="x", event_type="door")
    retry = await _send(hass, setup_entry, message="x", event_type="door")

    assert heard[EVENT_FAILED][0]["reason"] == "quota_exceeded"
    assert retry["outcome"] == "delivered"
    assert "Earlier events" not in _text(turn)


async def test_a_close_after_a_tool_but_no_text_is_delivered_not_failed(
    hass, setup_entry, turn, heard
):
    """A conversation that ran and used a tool, then closed, still counts."""
    turn.return_value = TurnResult(
        conversation_id="conv_1",
        responses=[],
        tools=[{"tool_name": "ha_perform_action", "is_error": False}],
        incomplete=True,
    )

    result = await _send(hass, setup_entry, message="x", event_type="door")
    second = await _send(hass, setup_entry, message="x", event_type="door")

    assert result["outcome"] == "delivered"
    assert result["incomplete"] is True
    assert heard[EVENT_FAILED] == []
    assert second == {"outcome": "dropped", "reason": "cooldown"}


async def test_recent_events_travel_as_context(hass, setup_entry, turn):
    """Within the window the agent sees what came before."""
    await _send(hass, setup_entry, message="The door opened.", event_type="door")
    await _send(
        hass, setup_entry, message="Motion in the living room.", event_type="motion"
    )

    text = _text(turn)
    assert "Earlier events" in text
    assert "The door opened." in text
    assert text.index("Earlier events") < text.index("Motion in the living room.")


async def test_recent_events_are_marked_as_handled(hass, setup_entry, turn):
    """An order in an earlier event is not an order for this turn."""
    await _send(hass, setup_entry, message="Turn off the light.", event_type="order")
    await _send(hass, setup_entry, message="The doorbell rang.", event_type="bell")

    text = _text(turn)
    assert (
        "Earlier events, already handled. Context only, do not act on them again:\n"
        "- 0 min ago: Turn off the light."
    ) in text


async def test_new_conversation_starts_without_history(hass, setup_entry, turn):
    """One event can start clean."""
    await _send(hass, setup_entry, message="first event", event_type="a")
    await _send(
        hass, setup_entry, message="second event", event_type="b", new_conversation=True
    )

    assert "Earlier events" not in _text(turn)


async def test_a_zero_window_never_carries_history(hass, setup_entry, turn):
    """0 means a fresh context for every event."""
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_EVENT_CONTINUITY: 0}
    )

    await _send(hass, setup_entry, message="first event", event_type="a")
    await _send(hass, setup_entry, message="second event", event_type="b")

    assert "Earlier events" not in _text(turn)


async def test_events_older_than_the_window_are_left_out(
    hass, setup_entry, turn, freezer
):
    """Only the last 30 minutes travel."""
    await _send(hass, setup_entry, message="viejo", event_type="a")
    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await _send(hass, setup_entry, message="nuevo", event_type="b")

    assert "viejo" not in _text(turn)


async def test_two_events_at_once_are_serialised(hass, setup_entry, turn):
    """The second waits for the first, so it carries it as history."""
    await asyncio.gather(
        _send(hass, setup_entry, message="primero", event_type="a"),
        _send(hass, setup_entry, message="segundo", event_type="b"),
    )

    assert turn.await_count == 2
    assert "Earlier events" in _text(turn, 1)


async def test_an_unloaded_entry_is_refused(hass, setup_entry, turn):
    """No runtime, no delivery."""
    await hass.config_entries.async_unload(setup_entry.entry_id)

    with pytest.raises(ServiceValidationError):
        await _send(hass, setup_entry, message="x")


async def test_nothing_secret_reaches_the_events(hass, setup_entry, turn, heard):
    """The API key never appears in what the service fires or returns."""
    result = await _send(hass, setup_entry, message="x")

    assert "sk_test" not in str(result)
    assert "sk_test" not in str(heard)


async def test_two_events_at_once_are_serialised_not_lucky(hass, setup_entry, turn):
    """A real suspension inside the turn shows the lock, not luck, orders them."""

    async def _slow(*args: object, on_started=None) -> TurnResult:
        await asyncio.sleep(0)
        return TurnResult(conversation_id="conv_1", responses=["ok"], tools=[])

    turn.side_effect = _slow

    await asyncio.gather(
        _send(hass, setup_entry, message="primero", event_type="a"),
        _send(hass, setup_entry, message="segundo", event_type="b"),
    )

    assert "Earlier events" in _text(turn, 1)


async def test_a_zero_window_clears_earlier_zero_window_events(hass, setup_entry, turn):
    """A zero-window turn erases what an earlier zero-window turn left behind."""
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_EVENT_CONTINUITY: 0}
    )
    await _send(hass, setup_entry, message="first event", event_type="a")
    await _send(hass, setup_entry, message="second event", event_type="b")

    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_EVENT_CONTINUITY: 30}
    )
    await _send(hass, setup_entry, message="third event", event_type="c")

    text = _text(turn)
    assert "first event" not in text
    assert "second event" in text


async def test_new_conversation_does_not_erase_history_for_later_events(
    hass, setup_entry, turn
):
    """new_conversation skips history for one event only; later events still see it."""
    await _send(hass, setup_entry, message="first event", event_type="a")
    await _send(
        hass, setup_entry, message="second event", event_type="b", new_conversation=True
    )
    await _send(hass, setup_entry, message="third event", event_type="c")

    text = _text(turn)
    assert "first event" in text
    assert "second event" in text


async def test_a_non_primitive_data_value_is_json_encoded(hass, setup_entry, turn):
    """A list or dict value becomes JSON text, not a raw Python value."""
    await _send(hass, setup_entry, message="x", data={"items": [1, 2, 3]})

    assert _variables(turn)["items"] == "[1, 2, 3]"


async def test_a_reserved_data_key_is_refused(hass, setup_entry, turn):
    """system__ is reserved for ElevenLabs; the call is refused before it spends."""
    with pytest.raises(ServiceValidationError):
        await _send(hass, setup_entry, message="x", data={"system__foo": "bar"})

    turn.assert_not_awaited()


async def test_a_call_queued_behind_the_lock_sees_the_entry_unload(
    hass, setup_entry, turn
):
    """A call already waiting for the lock is refused if the entry unloads first."""
    runtime = setup_entry.runtime_data
    async with runtime.event_lock:
        task = asyncio.ensure_future(_send(hass, setup_entry, message="x"))
        await asyncio.sleep(0.05)
        await hass.config_entries.async_unload(setup_entry.entry_id)

    with pytest.raises(ServiceValidationError):
        await task

    turn.assert_not_awaited()


async def test_an_event_turn_gets_its_grace_window_once_it_ends(
    hass, setup_entry, turn
):
    """
    The gate sees the conversation as running during the turn, then expiring.

    A pending tool call ElevenLabs already dispatched can still land after the
    turn returns, so the id is not discarded the instant async_run_turn ends:
    it is given a grace window instead (see Gate.end_event_conversation).
    """
    during: list[bool] = []

    async def run(*args: object, on_started=None, **kwargs: object) -> object:
        on_started("ev_42")
        gate = setup_entry.runtime_data.gate
        during.append(gate.event_conversations.get("ev_42") is None)
        return turn.return_value

    turn.side_effect = run

    await _send(hass, setup_entry, message="x")

    assert during == [True]
    gate = setup_entry.runtime_data.gate
    assert "ev_42" in gate.event_conversations
    assert gate.event_conversations["ev_42"] is not None


async def _sensitive_lock(
    hass, setup_entry, entity_registry, mark
) -> tuple[er.RegistryEntry, list[object]]:
    """Register a sensitive lock and return it with its recorded unlock calls."""
    lock = entity_registry.async_get_or_create("lock", "demo", "front")
    hass.states.async_set(lock.entity_id, "locked", {"friendly_name": "Door"})
    calls: list[object] = []

    async def unlock(call) -> None:
        calls.append(call)
        hass.states.async_set(lock.entity_id, "unlocked", {"friendly_name": "Door"})

    hass.services.async_register("lock", "unlock", unlock)
    hass.config_entries.async_update_entry(
        setup_entry,
        options={OPT_CONTROL_ENTITIES: [lock.id], OPT_SENSITIVE: {lock.id: mark}},
    )
    return lock, calls


async def _run_sensitive_call_inside_the_turn(
    hass, setup_entry, lock, turn
) -> dict[str, Any]:
    """
    Drive a real async_write on `lock` from inside a mocked turn.

    The side_effect calls on_started first, exactly as the real
    async_run_turn does, then writes to the sensitive lock in that same
    conversation, proving the gate recognises the id send_event itself
    registered rather than one a test added by hand.
    """
    outcomes: list[dict[str, Any]] = []

    async def run(*args: object, on_started=None, **kwargs: object) -> object:
        on_started("ev_real")
        outcomes.append(
            await async_write(
                hass,
                setup_entry,
                {
                    "tool": "call_service",
                    "entity_id": lock.entity_id,
                    "action": "lock.unlock",
                    "conversation_id": "ev_real",
                },
            )
        )
        return turn.return_value

    turn.side_effect = run
    await _send(hass, setup_entry, message="x")
    return outcomes[0]


async def test_send_event_registration_refuses_a_sensitive_call_when_set_to_refuse(
    hass, setup_entry, entity_registry, turn
):
    """on_started's own registration is enough to refuse, with no hand-added id."""
    lock, calls = await _sensitive_lock(
        hass,
        setup_entry,
        entity_registry,
        {"confirm": False, "refuse_automatic": True},
    )

    outcome = await _run_sensitive_call_inside_the_turn(hass, setup_entry, lock, turn)

    assert calls == []
    assert outcome["error"] == "not_allowed_without_a_person"


async def test_send_event_registration_allows_a_sensitive_call_when_not_set_to_refuse(
    hass, setup_entry, entity_registry, turn
):
    """on_started's own registration, with refuse_automatic off, lets it run."""
    lock, calls = await _sensitive_lock(
        hass,
        setup_entry,
        entity_registry,
        {"confirm": False, "refuse_automatic": False},
    )

    outcome = await _run_sensitive_call_inside_the_turn(hass, setup_entry, lock, turn)

    assert len(calls) == 1
    assert outcome["outcome"] == "done"
