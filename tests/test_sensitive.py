"""Tests for the sensitive marks, the confirmation history check and the gate."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.elevenlabs_agent.const import EVENT_CONVERSATION_GRACE_SECONDS
from custom_components.elevenlabs_agent.sensitive import (
    Gate,
    Mark,
    any_confirm,
    heard_after,
    sensitive_mark,
)

ASK = Mark(confirm=True, refuse_automatic=True)
ARGS: dict[str, Any] = {
    "entity_id": "lock.front",
    "name": "Puerta",
    "action": "lock.unlock",
    "service_data": {},
}


def _history(code: str, *, user_after: bool) -> str:
    entries: list[dict[str, Any]] = [
        {"role": "user", "message": "Abre la puerta"},
        {"role": "agent", "tool_requests": [{"tool_name": "ha_call_service"}]},
        {
            "role": "agent",
            "tool_results": [
                {
                    "tool_name": "ha_call_service",
                    "result_value": f'{{"confirmation_code": "{code}"}}',
                }
            ],
        },
        {"role": "agent", "message": "¿Seguro?"},
    ]
    if user_after:
        entries.append({"role": "user", "message": "Sí"})
    return json.dumps({"x-elevenlabs-history": True, "entries": entries})


def _ask(gate: Gate, now: float = 0.0, conversation: str = "c1") -> str:
    answer = gate.check(
        ASK, conversation_id=conversation, history=None, code=None, now=now, **ARGS
    )
    assert answer is not None
    assert answer["outcome"] == "confirmation_required"
    assert answer["name"] == "Puerta"
    return str(answer["confirmation_code"])


def test_mark_from_raw_defaults_both_options_to_true() -> None:
    """from_raw is the one place the confirm and refuse defaults live."""
    assert Mark.from_raw({}) == Mark(confirm=True, refuse_automatic=True)
    assert Mark.from_raw({"confirm": False}) == Mark(
        confirm=False, refuse_automatic=True
    )
    assert Mark.from_raw({"refuse_automatic": False}) == Mark(
        confirm=True, refuse_automatic=False
    )


def test_marks_are_read_with_both_options_on_by_default() -> None:
    """A mark defaults both options to True. A missing key is not sensitive."""
    options: dict[str, Any] = {"sensitive": {"k": {}}}
    assert sensitive_mark(options, "k") == Mark(confirm=True, refuse_automatic=True)
    assert sensitive_mark(options, "other") is None
    assert sensitive_mark(options, None) is None


def test_any_confirm_sees_one_asking_mark() -> None:
    """any_confirm is True when one mark asks for confirmation."""
    assert any_confirm({"sensitive": {"a": {"confirm": False}, "b": {}}}) is True
    assert any_confirm({"sensitive": {"a": {"confirm": False}}}) is False
    assert any_confirm({}) is False


def test_heard_after_needs_a_user_turn_after_the_code() -> None:
    """heard_after needs a user message after the tool result that carries the code."""
    assert heard_after(_history("ab12cd", user_after=True), "ab12cd") is True
    assert heard_after(_history("ab12cd", user_after=False), "ab12cd") is False
    assert heard_after(_history("other", user_after=True), "ab12cd") is False
    assert heard_after("not json", "ab12cd") is False
    assert heard_after(None, "ab12cd") is False


def test_a_confirmed_call_runs() -> None:
    """A matching code with a user turn after it lets the call run."""
    gate = Gate()
    code = _ask(gate)
    result = gate.check(
        ASK,
        conversation_id="c1",
        history=_history(code, user_after=True),
        code=code,
        now=5.0,
        **ARGS,
    )
    assert result is None


def test_the_agent_cannot_confirm_by_itself() -> None:
    """A code without a user turn after it does not confirm the call."""
    gate = Gate()
    code = _ask(gate)
    result = gate.check(
        ASK,
        conversation_id="c1",
        history=_history(code, user_after=False),
        code=code,
        now=5.0,
        **ARGS,
    )
    assert result is not None
    assert result["error"] == "no_confirmation_heard"


def test_a_code_is_single_use() -> None:
    """A code that already confirmed one call is gone on a second use."""
    gate = Gate()
    code = _ask(gate)
    history = _history(code, user_after=True)
    assert (
        gate.check(
            ASK, conversation_id="c1", history=history, code=code, now=5.0, **ARGS
        )
        is None
    )
    again = gate.check(
        ASK, conversation_id="c1", history=history, code=code, now=6.0, **ARGS
    )
    assert again is not None
    assert again["error"] == "confirmation_expired"


def test_a_code_cannot_move_to_another_device_or_data() -> None:
    """A code bound to one entity, action and data does not match a changed one."""
    for change in (
        {"entity_id": "lock.back"},
        {"service_data": {"code": "1234"}},
        {"action": "lock.open"},
    ):
        gate = Gate()
        code = _ask(gate)
        args = {**ARGS, **change}
        result = gate.check(
            ASK,
            conversation_id="c1",
            history=_history(code, user_after=True),
            code=code,
            now=5.0,
            **args,
        )
        assert result is not None
        assert result["error"] == "confirmation_expired"


def test_a_code_belongs_to_one_conversation() -> None:
    """A code asked in one conversation does not confirm a call in another."""
    gate = Gate()
    code = _ask(gate, conversation="c1")
    result = gate.check(
        ASK,
        conversation_id="c2",
        history=_history(code, user_after=True),
        code=code,
        now=5.0,
        **ARGS,
    )
    assert result is not None
    assert result["error"] == "confirmation_expired"


def test_a_code_expires_after_the_window() -> None:
    """A code past the confirmation window is expired."""
    gate = Gate()
    code = _ask(gate, now=0.0)
    result = gate.check(
        ASK,
        conversation_id="c1",
        history=_history(code, user_after=True),
        code=code,
        now=61.0,
        **ARGS,
    )
    assert result is not None
    assert result["error"] == "confirmation_expired"


def test_an_event_turn_is_refused_or_allowed() -> None:
    """An event turn is refused when refuse_automatic is on, allowed when off."""
    gate = Gate(event_conversations={"ev1": None})
    refused = gate.check(
        ASK, conversation_id="ev1", history=None, code=None, now=0.0, **ARGS
    )
    assert refused is not None
    assert refused["error"] == "not_allowed_without_a_person"
    allow = Mark(confirm=True, refuse_automatic=False)
    assert (
        gate.check(
            allow, conversation_id="ev1", history=None, code=None, now=0.0, **ARGS
        )
        is None
    )


def test_no_confirmation_option_runs_in_a_conversation() -> None:
    """A mark with confirm off lets a conversation call run without a code."""
    mark = Mark(confirm=False, refuse_automatic=True)
    assert (
        Gate().check(
            mark, conversation_id="c1", history=None, code=None, now=0.0, **ARGS
        )
        is None
    )


def test_no_conversation_id_fails_closed() -> None:
    """A missing conversation_id fails closed with conversation_unknown."""
    result = Gate().check(
        ASK, conversation_id=None, history=None, code=None, now=0.0, **ARGS
    )
    assert result is not None
    assert result["error"] == "conversation_unknown"


def test_old_codes_are_forgotten_when_a_new_one_is_issued() -> None:
    """Issuing a new code prunes codes past the confirmation window."""
    gate = Gate()
    _ask(gate, now=0.0)
    _ask(gate, now=100.0)
    assert len(gate.pending) == 1


def test_a_mark_without_confirm_still_refuses_event_turns() -> None:
    """A mark that only refuses automatic runs still refuses an event turn."""
    gate = Gate(event_conversations={"ev1": None})
    mark = Mark(confirm=False, refuse_automatic=True)
    result = gate.check(
        mark, conversation_id="ev1", history=None, code=None, now=0.0, **ARGS
    )
    assert result is not None
    assert result["error"] == "not_allowed_without_a_person"


def test_a_mark_without_confirm_still_fails_closed_without_a_conversation() -> None:
    """A mark that does not ask for confirmation still needs a conversation_id."""
    mark = Mark(confirm=False, refuse_automatic=True)
    result = Gate().check(
        mark, conversation_id=None, history=None, code=None, now=0.0, **ARGS
    )
    assert result is not None
    assert result["error"] == "conversation_unknown"


def test_a_non_string_conversation_id_fails_closed() -> None:
    """A conversation_id that is not a non-empty string is treated as unknown."""
    result = Gate().check(
        ASK, conversation_id=123, history=None, code=None, now=0.0, **ARGS
    )
    assert result is not None
    assert result["error"] == "conversation_unknown"


# The history of a live conversation, captured on 9 Oct 2026. The code is changed.
LIVE_RESULT = {
    "outcome": "confirmation_required",
    "entity_id": "lock.openable_lock",
    "action": "lock.unlock",
    "name": "Openable Lock",
    "confirmation_code": "ab12cd",
}
LIVE_CALL = {"entity_id": "lock.openable_lock", "action": "lock.unlock"}
LIVE_HISTORY = json.dumps(
    {
        "x-elevenlabs-history": True,
        "entries": [
            {"role": "user", "message": "Desbloquea la cerradura Openable Lock."},
            {
                "role": "agent",
                "tool_requests": [
                    {
                        "tool_name": "ha_call_service",
                        "params_as_json": {
                            **LIVE_CALL,
                            "tool": "call_service",
                            "conversation_id": "conv_1",
                            "conversation_history": "[conversation_history (2 turns)]",
                        },
                    }
                ],
            },
            {
                "role": "agent",
                "tool_results": [
                    {
                        "tool_name": "ha_call_service",
                        "result_value": json.dumps(LIVE_RESULT),
                    }
                ],
            },
            {"role": "agent", "message": "Necesito que confirmes la acción."},
            {"role": "user", "message": "Sí, confirmo, desbloquéala."},
            {
                "role": "agent",
                "tool_requests": [
                    {
                        "tool_name": "ha_call_service",
                        "params_as_json": {**LIVE_CALL, "confirmation_code": "ab12cd"},
                    }
                ],
            },
        ],
    }
)


def test_heard_after_reads_a_live_history() -> None:
    """The tool result arrives in an agent entry. A user turn after it counts."""
    assert heard_after(LIVE_HISTORY, "ab12cd") is True
    assert heard_after(LIVE_HISTORY, "ffffff") is False


@pytest.mark.parametrize(
    "result_value",
    [
        '{"outcome": "done", "name": "ab12cd"}',
        '{"outcome": "confirmation_required", "confirmation_code": "ab12cdef"}',
        "ab12cd",
        '["ab12cd"]',
    ],
)
def test_heard_after_needs_the_exact_code_field(result_value: str) -> None:
    """Only a confirmation_code field equal to the code marks the question."""
    entries = [
        {"role": "agent", "tool_results": [{"result_value": result_value}]},
        {"role": "agent", "message": "¿Seguro?"},
        {"role": "user", "message": "Sí"},
    ]
    history = json.dumps({"x-elevenlabs-history": True, "entries": entries})
    assert heard_after(history, "ab12cd") is False


def test_heard_after_ignores_a_code_in_a_user_entry() -> None:
    """A code inside a user entry does not count as the question."""
    entries = [
        {"role": "user", "tool_results": [{"result_value": "ab12cd"}]},
        {"role": "user", "message": "Sí"},
    ]
    history = json.dumps({"x-elevenlabs-history": True, "entries": entries})
    assert heard_after(history, "ab12cd") is False


def _entries_after_code(*after: dict[str, Any]) -> str:
    entries: list[dict[str, Any]] = [
        {
            "role": "agent",
            "tool_results": [
                {
                    "tool_name": "ha_call_service",
                    "result_value": '{"confirmation_code": "ab12cd"}',
                }
            ],
        },
        *after,
    ]
    return json.dumps({"x-elevenlabs-history": True, "entries": entries})


def test_heard_after_needs_the_agent_to_ask_before_the_user_answers() -> None:
    """A user turn between the code and the agent's question does not count."""
    before_question = _entries_after_code(
        {"role": "user", "message": "y enciende la luz"},
    )
    question_after = _entries_after_code(
        {"role": "user", "message": "y enciende la luz"},
        {"role": "agent", "message": "¿Abro la puerta?"},
    )
    agent_without_text = _entries_after_code(
        {"role": "agent", "tool_requests": [{"tool_name": "ha_call_service"}]},
        {"role": "agent", "message": "  "},
        {"role": "user", "message": "Sí"},
    )
    asked_then_answered = _entries_after_code(
        {"role": "user", "message": "y enciende la luz"},
        {"role": "agent", "message": "¿Abro la puerta?"},
        {"role": "user", "message": "Sí"},
    )

    assert heard_after(before_question, "ab12cd") is False
    assert heard_after(question_after, "ab12cd") is False
    assert heard_after(agent_without_text, "ab12cd") is False
    assert heard_after(asked_then_answered, "ab12cd") is True


def test_heard_after_ignores_a_user_entry_without_text() -> None:
    """A user entry with a null or missing message does not count as heard."""
    entries = [
        {
            "role": "agent",
            "tool_results": [
                {
                    "tool_name": "ha_call_service",
                    "result_value": '{"confirmation_code": "ab12cd"}',
                }
            ],
        },
        {"role": "agent", "message": "¿Seguro?"},
        {"role": "user", "message": None},
        {"role": "user"},
    ]
    history = json.dumps({"x-elevenlabs-history": True, "entries": entries})
    assert heard_after(history, "ab12cd") is False


def test_a_code_expires_at_exactly_the_window() -> None:
    """A code verified at exactly the window age is expired, not valid."""
    gate = Gate()
    code = _ask(gate, now=0.0)
    result = gate.check(
        ASK,
        conversation_id="c1",
        history=_history(code, user_after=True),
        code=code,
        now=60.0,
        **ARGS,
    )
    assert result is not None
    assert result["error"] == "confirmation_expired"


def test_a_code_is_expired_when_the_clock_steps_back() -> None:
    """A negative age, from a clock that stepped back, is expired."""
    gate = Gate()
    code = _ask(gate, now=100.0)
    result = gate.check(
        ASK,
        conversation_id="c1",
        history=_history(code, user_after=True),
        code=code,
        now=50.0,
        **ARGS,
    )
    assert result is not None
    assert result["error"] == "confirmation_expired"


def test_register_event_conversation_has_no_expiry_while_running() -> None:
    """A conversation just registered has no expiry: the turn is still running."""
    gate = Gate()
    gate.register_event_conversation("ev1")
    assert gate.event_conversations == {"ev1": None}


def test_end_event_conversation_sets_a_grace_window() -> None:
    """Ending a conversation starts its grace window instead of forgetting it."""
    gate = Gate()
    gate.register_event_conversation("ev1")
    gate.end_event_conversation("ev1", now=100.0)
    assert gate.event_conversations["ev1"] == 100.0 + EVENT_CONVERSATION_GRACE_SECONDS


def test_end_event_conversation_ignores_an_unknown_id() -> None:
    """Ending an id that was never registered (or already pruned) is a no-op."""
    gate = Gate()
    gate.end_event_conversation("ghost", now=100.0)
    assert gate.event_conversations == {}


def test_ending_an_event_conversation_prunes_expired_ones() -> None:
    """A finished turn clears ids past their grace window, even with no marks."""
    gate = Gate()
    gate.register_event_conversation("old")
    gate.end_event_conversation("old", now=100.0)
    gate.register_event_conversation("new")
    gate.end_event_conversation(
        "new", now=100.0 + EVENT_CONVERSATION_GRACE_SECONDS + 1.0
    )
    assert "old" not in gate.event_conversations
    assert "new" in gate.event_conversations


def test_a_running_event_conversation_has_no_expiry_and_is_never_pruned() -> None:
    """Expiry None means the turn is still running: check must never prune it."""
    gate = Gate(event_conversations={"ev1": None})
    result = gate.check(
        ASK, conversation_id="ev1", history=None, code=None, now=10_000.0, **ARGS
    )
    assert result is not None
    assert result["error"] == "not_allowed_without_a_person"
    assert "ev1" in gate.event_conversations


def test_an_event_conversation_stays_known_within_its_grace_window() -> None:
    """An id kept with a future expiry still counts as automatic before then."""
    gate = Gate(event_conversations={"ev1": 300.0})
    result = gate.check(
        ASK, conversation_id="ev1", history=None, code=None, now=299.0, **ARGS
    )
    assert result is not None
    assert result["error"] == "not_allowed_without_a_person"


def test_an_event_conversation_is_forgotten_once_its_grace_window_passes() -> None:
    """Check prunes an id whose expiry has passed; it stops acting as automatic."""
    gate = Gate(event_conversations={"ev1": 300.0})
    result = gate.check(
        ASK, conversation_id="ev1", history=None, code=None, now=301.0, **ARGS
    )
    assert result is not None
    assert result["outcome"] == "confirmation_required"
    assert "ev1" not in gate.event_conversations


def test_ask_regenerates_a_colliding_code() -> None:
    """A newly drawn code that collides with a pending one is drawn again."""
    gate = Gate()
    with patch(
        "custom_components.elevenlabs_agent.sensitive.secrets.token_hex"
    ) as token_hex:
        token_hex.side_effect = ["abc123", "abc123", "def456"]
        first = _ask(gate, now=0.0, conversation="c1")
        second_answer = gate.check(
            ASK, conversation_id="c2", history=None, code=None, now=0.0, **ARGS
        )
    assert first == "abc123"
    assert second_answer is not None
    assert second_answer["confirmation_code"] == "def456"
    assert set(gate.pending) == {"abc123", "def456"}
    assert gate.pending["abc123"].conversation_id == "c1"
