"""Response shapes sent to the agent."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.util import dt as dt_util

from .const import (
    ERROR_EXECUTION_FAILED,
    ERROR_INVALID_VALUE,
    ERROR_MALFORMED_REQUEST,
    ERROR_UNKNOWN_FIELD,
    INSTANCE_TEMPERATURE_DOMAINS,
    OUTCOME_UNCONFIRMED,
    PROJECTION_ATTRIBUTES,
)

# The voluptuous message for a key that the schema does not declare.
EXTRA_KEYS_MESSAGE = "extra keys not allowed"
# The state of a text entity in password mode.
HIDDEN_VALUE = "<hidden>"

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _area_name(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return the area name of an entity, or None when it has no area."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return None

    area_id = entry.area_id
    if area_id is None and entry.device_id is not None:
        device = dr.async_get(hass).async_get(entry.device_id)
        area_id = None if device is None else device.area_id

    if area_id is None:
        return None

    area = ar.async_get(hass).async_get_area(area_id)
    return None if area is None else area.name


def _brightness_pct(brightness: Any) -> Any:
    """
    Convert a brightness of 0-255 to a percentage of 0-100.

    A value above zero gives at least 1. A value that is not a number
    stays as it is.
    """
    if isinstance(brightness, bool) or not isinstance(brightness, int | float):
        return brightness
    if brightness <= 0:
        return 0
    return max(1, round(brightness * 100 / 255))


def _local_time(value: Any) -> Any:
    """
    Convert a time with a time zone to local time, in ISO format.

    The time is a datetime or a string in ISO format. A value that is not
    such a time stays as it is.
    """
    if isinstance(value, datetime):
        moment: datetime | None = value
    elif isinstance(value, str):
        try:
            moment = dt_util.parse_datetime(value)
        except ValueError:
            return value
    else:
        return value
    if moment is None or moment.tzinfo is None:
        return value
    return dt_util.as_local(moment).isoformat(timespec="seconds")


def _time_left(finishes_at: Any) -> str | None:
    """
    Return the time from now to finishes_at, as H:MM:SS.

    Return None when finishes_at is not a time with a time zone.
    """
    if not isinstance(finishes_at, str):
        return None
    try:
        end = dt_util.parse_datetime(finishes_at)
    except ValueError:
        return None
    if end is None or end.tzinfo is None:
        return None
    seconds = max(0, round((end - dt_util.utcnow()).total_seconds()))
    return str(timedelta(seconds=seconds))


def project(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    """
    Return the curated projection of one entity.

    The shape is flat. Attributes outside the per-domain table are dropped.
    """
    state = hass.states.get(entity_id)
    if state is None:
        return {}

    projected: dict[str, Any] = {
        "entity_id": entity_id,
        "name": state.attributes.get("friendly_name", entity_id),
        "area": _area_name(hass, entity_id),
        "state": state.state,
    }

    domain = entity_id.partition(".")[0]
    projected["state"] = _local_time(state.state)
    if domain in ("text", "input_text") and state.attributes.get("mode") == "password":
        projected["state"] = HIDDEN_VALUE

    for attribute in PROJECTION_ATTRIBUTES.get(domain, ()):
        if attribute in state.attributes:
            projected[attribute] = _local_time(state.attributes[attribute])

    if domain == "timer" and state.state == "active":
        left = _time_left(state.attributes.get("finishes_at"))
        if left is not None:
            projected["remaining"] = left

    if domain == "humidifier" and "action" in projected:
        projected["humidifier_action"] = projected.pop("action")

    if "brightness" in projected:
        projected["brightness_pct"] = _brightness_pct(projected.pop("brightness"))

    if domain in INSTANCE_TEMPERATURE_DOMAINS:
        projected["temperature_unit"] = hass.config.units.temperature_unit

    if state.attributes.get("assumed_state"):
        projected["assumed_state"] = True

    return projected


def write_result(
    outcome: str,
    entity_id: str,
    action: str,
    projection: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build the response for one write.

    There is no ok field. The outcome names what was observed.
    """
    result: dict[str, Any] = {
        "outcome": outcome,
        "entity_id": entity_id,
        "action": action,
    }
    if outcome == OUTCOME_UNCONFIRMED:
        result["check_state_next"] = True
    elif projection:
        result.update({k: v for k, v in projection.items() if k not in result})
    if extra:
        result.update(extra)
    return result


def changed_at(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return when the entity's state last changed, in local time."""
    state = hass.states.get(entity_id)
    return None if state is None else _local_time(state.last_changed)


def read_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap per-entity read results with the local time. There is no top-level ok."""
    return {"results": results, "now": _local_time(dt_util.utcnow())}


def error_fields(exc: Exception) -> tuple[str, dict[str, Any]]:
    """
    Map an exception to an error code and its extra fields.

    A ServiceValidationError is relayed under its own translation_key.
    """
    if isinstance(exc, ServiceValidationError):
        key = exc.translation_key
        if key:
            placeholders = exc.translation_placeholders or {}
            return key, dict(placeholders)
        return ERROR_EXECUTION_FAILED, {}

    if isinstance(exc, vol.Invalid):
        path = exc.path
        field = str(path[0]) if path else ""
        if exc.error_message == EXTRA_KEYS_MESSAGE:
            return ERROR_UNKNOWN_FIELD, {"field": field} if field else {}
        # An inclusion group gives a path of the form <group>, not a field.
        extra = {"field": field} if field and not field.startswith("<") else {}
        return ERROR_INVALID_VALUE, {**extra, "detail": exc.error_message}

    if isinstance(exc, ValueError):
        return ERROR_INVALID_VALUE, {"detail": str(exc)}

    return ERROR_EXECUTION_FAILED, {}


def malformed(detail: str) -> dict[str, Any]:
    """Build the call-level failure shape."""
    return {"error": ERROR_MALFORMED_REQUEST, "detail": detail}
