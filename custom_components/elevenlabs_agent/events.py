"""The send_event service: limits, context, delivery to the agent, the result."""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.const import ATTR_CONFIG_ENTRY_ID, ATTR_ENTITY_ID
from homeassistant.core import DOMAIN as HOMEASSISTANT_DOMAIN
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service
from homeassistant.util import dt as dt_util

from .agent_session import SessionError, async_run_turn
from .const import (
    CONF_AGENT_ID,
    DEFAULT_EVENT_CONTINUITY,
    DEFAULT_EVENT_COOLDOWN,
    DEFAULT_EVENT_HOURLY_LIMIT,
    DOMAIN,
    EVENT_DROPPED,
    EVENT_FAILED,
    EVENT_REPLY,
    MAX_EVENT_DATA_BYTES,
    MAX_EVENT_MESSAGE,
    OPT_EVENT_CONTINUITY,
    OPT_EVENT_COOLDOWN,
    OPT_EVENT_HOURLY_LIMIT,
)
from .permissions import authorise_read, from_options
from .response import project

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse

    from . import ElevenLabsAgentConfigEntry

_LOGGER = logging.getLogger(__name__)

PRIORITY_HIGH = "high"
HOUR = 3600.0
HISTORY_SIZE = 20

SEND_EVENT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required("message"): vol.All(
            cv.string, vol.Length(min=1, max=MAX_EVENT_MESSAGE)
        ),
        vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional("event_type"): cv.string,
        vol.Optional("priority"): vol.In(["low", "normal", PRIORITY_HIGH]),
        vol.Optional("new_conversation", default=False): cv.boolean,
        vol.Optional("data", default={}): dict,
    }
)


@dataclass
class EventLimiter:
    """A cooldown per event key and a limit per hour, in seconds since the epoch."""

    last_sent: dict[str, float] = field(default_factory=dict)
    sent_times: deque[float] = field(default_factory=deque)

    def refusal(
        self, key: str, now: float, *, cooldown: float, hourly_limit: int, high: bool
    ) -> str | None:
        """Return hourly_limit or cooldown when the event may not go, else None."""
        while self.sent_times and now - self.sent_times[0] >= HOUR:
            self.sent_times.popleft()
        if len(self.sent_times) >= hourly_limit:
            return "hourly_limit"
        last = self.last_sent.get(key)
        if not high and last is not None and now - last < cooldown:
            return "cooldown"
        return None

    def record(self, key: str, now: float) -> None:
        """Count one delivered event."""
        self.last_sent[key] = now
        self.sent_times.append(now)


@dataclass
class EventHistory:
    """The last delivered events, as (time, message) pairs."""

    entries: list[tuple[float, str]] = field(default_factory=list)

    def recent(self, now: float, window: float) -> list[tuple[float, str]]:
        """Return the events younger than the window, oldest first."""
        return [entry for entry in self.entries if now - entry[0] < window]

    def add(self, now: float, message: str) -> None:
        """Keep one delivered event. Keep at most the last twenty."""
        self.entries = [*self.entries, (now, message)][-HISTORY_SIZE:]

    def clear(self) -> None:
        """Forget every event."""
        self.entries = []


def compose_message(
    message: str,
    projection: dict[str, Any] | None,
    recent: list[tuple[float, str]],
    now: float,
) -> str:
    """Return the user message: earlier events, the event, the entity state."""
    parts: list[str] = []
    if recent:
        lines = [
            f"- {int((now - moment) // 60)} min ago: {text}" for moment, text in recent
        ]
        parts.append("Earlier events:\n" + "\n".join(lines))
    parts.append(f"Event: {message}")
    if projection:
        parts.append("Entity: " + json.dumps(projection, ensure_ascii=False))
    return "\n\n".join(parts)


def _dynamic_value(value: Any) -> str | int | float | bool:
    """Return str/int/float/bool unchanged; encode anything else as JSON text."""
    if isinstance(value, str | int | float | bool):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _variables(call: ServiceCall) -> dict[str, Any]:
    """Return data plus the optional fields, as dynamic variables."""
    variables = {key: _dynamic_value(value) for key, value in call.data["data"].items()}
    for name in ("event_type", "priority", ATTR_ENTITY_ID):
        if name in call.data:
            variables[name] = call.data[name]
    return variables


def _check_projection(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry, entity_id: str | None
) -> dict[str, Any] | None:
    """Return the entity's projection, refusing an entity the agent may not read."""
    if entity_id is None:
        return None
    if authorise_read(hass, from_options(entry.options), entity_id) is not None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="event_entity_not_readable",
            translation_placeholders={"entity_id": entity_id},
        )
    return project(hass, entity_id)


async def _deliver(
    hass: HomeAssistant,
    entry: ElevenLabsAgentConfigEntry,
    call: ServiceCall,
    projection: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply the limits, deliver the turn under the lock, return its result."""
    runtime = entry.runtime_data
    event_type = call.data.get("event_type")
    entity_id = call.data.get(ATTR_ENTITY_ID)
    key = event_type or entity_id or call.data["message"]
    async with runtime.event_lock:
        entry = service.async_get_config_entry(hass, DOMAIN, entry.entry_id)
        if entry.runtime_data is not runtime:
            raise ServiceValidationError(
                translation_domain=HOMEASSISTANT_DOMAIN,
                translation_key="service_config_entry_not_loaded",
                translation_placeholders={
                    "domain": DOMAIN,
                    "entry_title": entry.title,
                },
            )
        options = entry.options
        now = dt_util.utcnow().timestamp()
        refusal = runtime.event_limiter.refusal(
            key,
            now,
            cooldown=60.0 * options.get(OPT_EVENT_COOLDOWN, DEFAULT_EVENT_COOLDOWN),
            hourly_limit=options.get(
                OPT_EVENT_HOURLY_LIMIT, DEFAULT_EVENT_HOURLY_LIMIT
            ),
            high=call.data.get("priority") == PRIORITY_HIGH,
        )
        if refusal is not None:
            _LOGGER.info("An event was dropped: %s", refusal)
            hass.bus.async_fire(
                EVENT_DROPPED,
                {
                    "entry_id": entry.entry_id,
                    "event_type": event_type,
                    "reason": refusal,
                },
            )
            return {"outcome": "dropped", "reason": refusal}
        window = 60.0 * options.get(OPT_EVENT_CONTINUITY, DEFAULT_EVENT_CONTINUITY)
        if window == 0:
            runtime.event_history.clear()
        recent = (
            []
            if call.data["new_conversation"]
            else runtime.event_history.recent(now, window)
        )
        text = compose_message(call.data["message"], projection, recent, now)
        started: list[str] = []

        def _started(conversation_id: str) -> None:
            runtime.gate.register_event_conversation(conversation_id)
            started.append(conversation_id)

        try:
            turn = await async_run_turn(
                hass,
                runtime.client,
                entry.data[CONF_AGENT_ID],
                text,
                _variables(call),
                on_started=_started,
            )
        except SessionError as err:
            _LOGGER.warning("An event could not reach the agent: %s", err.reason)
            hass.bus.async_fire(
                EVENT_FAILED,
                {
                    "entry_id": entry.entry_id,
                    "event_type": event_type,
                    "reason": err.reason,
                },
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="event_failed",
                translation_placeholders={"reason": err.reason},
            ) from err
        finally:
            end = dt_util.utcnow().timestamp()
            for conversation_id in started:
                runtime.gate.end_event_conversation(conversation_id, end)
        runtime.event_limiter.record(key, now)
        runtime.event_history.add(now, call.data["message"])

    return {
        "outcome": "delivered",
        "conversation_id": turn.conversation_id,
        "responses": turn.responses,
        "tools": turn.tools,
        "incomplete": turn.incomplete,
    }


async def async_handle_send_event(call: ServiceCall) -> ServiceResponse:
    """Deliver one event to the agent and return its turn."""
    hass = call.hass
    entry = service.async_get_config_entry(
        hass, DOMAIN, call.data[ATTR_CONFIG_ENTRY_ID]
    )
    if len(json.dumps(call.data["data"], default=str)) > MAX_EVENT_DATA_BYTES:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="event_data_too_large"
        )
    for reserved_key in call.data["data"]:
        if reserved_key.startswith("system__"):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="event_data_reserved_key",
                translation_placeholders={"key": reserved_key},
            )
    projection = _check_projection(hass, entry, call.data.get(ATTR_ENTITY_ID))

    result = await _deliver(hass, entry, call, projection)
    if result["outcome"] != "delivered":
        return result

    event_type = call.data.get("event_type")
    hass.bus.async_fire(
        EVENT_REPLY, {"entry_id": entry.entry_id, "event_type": event_type, **result}
    )
    return result
