"""Response shapes sent to the agent."""

from __future__ import annotations

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

from .const import (
    ERROR_EXECUTION_FAILED,
    ERROR_MALFORMED_REQUEST,
    ERROR_UNKNOWN_FIELD,
    OUTCOME_UNCONFIRMED,
    PROJECTION_ATTRIBUTES,
)

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
    for attribute in PROJECTION_ATTRIBUTES.get(domain, ()):
        if attribute in state.attributes:
            projected[attribute] = state.attributes[attribute]

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
        result.update({k: v for k, v in projection.items() if k != "entity_id"})
    if extra:
        result.update(extra)
    return result


def read_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap per-entity read results. There is no top-level ok."""
    return {"results": results}


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
        return ERROR_UNKNOWN_FIELD, {"field": field} if field else {}

    return ERROR_EXECUTION_FAILED, {}


def malformed(detail: str) -> dict[str, Any]:
    """Build the call-level failure shape."""
    return {"error": ERROR_MALFORMED_REQUEST, "detail": detail}
