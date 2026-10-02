"""Confirmation for sensitive entities: the marks, the pending codes and the gate."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .const import (
    CONFIRMATION_WINDOW_SECONDS,
    DOMAIN,
    EVENT_CONVERSATION_GRACE_SECONDS,
    OPT_SENSITIVE,
    OUTCOME_CONFIRMATION_REQUIRED,
    OUTCOME_FAILED,
    REASON_CONFIRMATION_EXPIRED,
    REASON_CONVERSATION_UNKNOWN,
    REASON_NO_CONFIRMATION_HEARD,
    REASON_NOT_ALLOWED_WITHOUT_A_PERSON,
)
from .response import write_result

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant


@dataclass(frozen=True)
class Mark:
    """The two options of one sensitive entity."""

    confirm: bool
    refuse_automatic: bool


@dataclass(frozen=True)
class Call:
    """One action bound to a confirmation: the device, the action and its data."""

    entity_id: str
    action: str
    service_data: str


@dataclass(frozen=True)
class Pending:
    """One confirmation the agent asked for and the user has not given yet."""

    conversation_id: str
    call: Call
    created: float


def sensitive_mark(options: Mapping[str, Any], key: str | None) -> Mark | None:
    """Return the mark of a permission key, or None when the entity is not sensitive."""
    if key is None:
        return None
    raw = options.get(OPT_SENSITIVE, {}).get(key)
    if raw is None:
        return None
    return Mark(
        confirm=bool(raw.get("confirm", True)),
        refuse_automatic=bool(raw.get("refuse_automatic", True)),
    )


def any_confirm(options: Mapping[str, Any]) -> bool:
    """Return True when at least one mark asks for confirmation."""
    return any(
        bool(raw.get("confirm", True))
        for raw in options.get(OPT_SENSITIVE, {}).values()
    )


def _canonical(data: Mapping[str, Any]) -> str:
    """Return one comparable text for service data."""
    return json.dumps(data, sort_keys=True, default=str)


def _entries(history: Any) -> list[Any]:
    """Return the history entries, or an empty list when the history is unreadable."""
    if isinstance(history, str):
        try:
            history = json.loads(history)
        except ValueError:
            return []
    entries = history.get("entries") if isinstance(history, dict) else None
    return entries if isinstance(entries, list) else []


def _carries(entry: Any, code: str) -> bool:
    """Return True when a tool entry holds the code in one of its results."""
    if not isinstance(entry, dict) or entry.get("role") != "tool":
        return False
    results = entry.get("tool_results") or []
    return any(
        isinstance(result, dict) and code in str(result.get("result_value", ""))
        for result in results
    )


def _is_user_turn(entry: Any) -> bool:
    """Return True when an entry is a user turn that carries spoken text."""
    if not isinstance(entry, dict) or entry.get("role") != "user":
        return False
    message = entry.get("message")
    return isinstance(message, str) and bool(message.strip())


def heard_after(history: Any, code: str) -> bool:
    """Return True when a user entry follows the last tool entry that holds the code."""
    entries = _entries(history)
    asked = None
    for index, entry in enumerate(entries):
        if _carries(entry, code):
            asked = index
    if asked is None:
        return False
    return any(_is_user_turn(entry) for entry in entries[asked + 1 :])


@dataclass
class Gate:
    """
    The pending confirmations of one entry and its event conversations.

    event_conversations maps a conversation id to its expiry: None while the
    turn is still running, a wall-clock timestamp once it has ended. An id
    past its expiry is pruned the next time check() runs.
    """

    pending: dict[str, Pending] = field(default_factory=dict)
    event_conversations: dict[str, float | None] = field(default_factory=dict)

    def register_event_conversation(self, conversation_id: str) -> None:
        """Mark a conversation as an automatic turn that is still running."""
        self.event_conversations[conversation_id] = None

    def end_event_conversation(self, conversation_id: str, now: float) -> None:
        """Start the grace window during which a finished turn's id still counts."""
        if conversation_id in self.event_conversations:
            self.event_conversations[conversation_id] = (
                now + EVENT_CONVERSATION_GRACE_SECONDS
            )

    def _prune_event_conversations(self, now: float) -> None:
        """Drop ended event conversations whose grace window has passed."""
        self.event_conversations = {
            conversation_id: expiry
            for conversation_id, expiry in self.event_conversations.items()
            if expiry is None or now < expiry
        }

    def check(  # noqa: PLR0913 - every field is part of what a code binds to
        self,
        mark: Mark,
        *,
        conversation_id: Any,
        history: Any,
        code: str | None,
        entity_id: str,
        name: str,
        action: str,
        service_data: Mapping[str, Any],
        now: float,
    ) -> dict[str, Any] | None:
        """Return the answer that replaces the action, or None when it may run."""
        if not isinstance(conversation_id, str) or not conversation_id:
            return _failed(entity_id, action, REASON_CONVERSATION_UNKNOWN)
        self._prune_event_conversations(now)
        if conversation_id in self.event_conversations:
            if mark.refuse_automatic:
                return _failed(entity_id, action, REASON_NOT_ALLOWED_WITHOUT_A_PERSON)
            return None
        if not mark.confirm:
            return None
        call = Call(entity_id, action, _canonical(service_data))
        if not code:
            return self._ask(conversation_id, name, call, now)
        return self._verify(code, conversation_id, history, call, now)

    def _ask(
        self, conversation_id: str, name: str, call: Call, now: float
    ) -> dict[str, Any]:
        """Store a new code and return confirmation_required."""
        self.pending = {
            key: pending
            for key, pending in self.pending.items()
            if now - pending.created < CONFIRMATION_WINDOW_SECONDS
        }
        code = secrets.token_hex(3)
        while code in self.pending:
            code = secrets.token_hex(3)
        self.pending[code] = Pending(conversation_id, call, now)
        return write_result(
            OUTCOME_CONFIRMATION_REQUIRED,
            call.entity_id,
            call.action,
            extra={"name": name, "confirmation_code": code},
        )

    def _verify(
        self, code: str, conversation_id: str, history: Any, call: Call, now: float
    ) -> dict[str, Any] | None:
        """Use the code once. Return None when it matches and the user spoke."""
        pending = self.pending.pop(code, None)
        if pending is None or not self._matches(pending, conversation_id, call, now):
            return _failed(call.entity_id, call.action, REASON_CONFIRMATION_EXPIRED)
        if not heard_after(history, code):
            return _failed(call.entity_id, call.action, REASON_NO_CONFIRMATION_HEARD)
        return None

    @staticmethod
    def _matches(
        pending: Pending, conversation_id: str, call: Call, now: float
    ) -> bool:
        """Return True when a pending confirmation is still fresh and unchanged."""
        age = now - pending.created
        return (
            0 <= age < CONFIRMATION_WINDOW_SECONDS
            and pending.conversation_id == conversation_id
            and pending.call == call
        )


def _failed(entity_id: str, action: str, reason: str) -> dict[str, Any]:
    """Return a failed write with one reason."""
    return write_result(OUTCOME_FAILED, entity_id, action, extra={"error": reason})


def gate_for_entry(hass: HomeAssistant, entry_id: str) -> Gate:
    """
    Return this entry's Gate, creating one on first use.

    The Gate lives in hass.data, not in RuntimeData, so a reload keeps its
    pending confirmations and event conversations.
    """
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    gates: dict[str, Gate] = domain_data.setdefault("gates", {})
    return gates.setdefault(entry_id, Gate())


def discard_gate(hass: HomeAssistant, entry_id: str) -> None:
    """Drop this entry's Gate. Call only when the entry is removed, not reloaded."""
    domain_data: dict[str, Any] = hass.data.get(DOMAIN, {})
    domain_data.get("gates", {}).pop(entry_id, None)
