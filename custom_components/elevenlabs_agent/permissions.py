"""Permission model: what the agent may read and what it may do."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)

from .const import (
    ERROR_ACTION_NOT_ALLOWED,
    ERROR_NOT_FOUND,
    OPT_CONTROL_ENTITIES,
    OPT_EXCEPTIONS,
    OPT_READ_ENTITIES,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant


@dataclass(frozen=True)
class Permissions:
    """Entity registry IDs the user selected, and the services they removed."""

    read: frozenset[str]
    control: frozenset[str]
    exceptions: Mapping[str, frozenset[str]]


def from_options(options: Mapping[str, Any]) -> Permissions:
    """Build a Permissions from stored options."""
    return Permissions(
        read=frozenset(options.get(OPT_READ_ENTITIES, ())),
        control=frozenset(options.get(OPT_CONTROL_ENTITIES, ())),
        exceptions={
            key: frozenset(value)
            for key, value in options.get(OPT_EXCEPTIONS, {}).items()
        },
    )


def resolve_target(hass: HomeAssistant, target: dict[str, Any]) -> set[str]:
    """
    Resolve a target selector value to entity registry IDs.

    An entity without a registry entry has no stable ID and is skipped.
    """
    selected = async_extract_referenced_entity_ids(hass, TargetSelection(target))
    entities = er.async_get(hass)

    resolved: set[str] = set()
    for entity_id in selected.referenced | selected.indirectly_referenced:
        if (entry := entities.async_get(entity_id)) is not None:
            resolved.add(entry.id)
    return resolved


def matches_supported_features(state_features: int, target: dict[str, Any]) -> bool:
    """
    Test one target entity filter against an entity's supported_features.

    Each entry is a mask. The entity matches when it holds every bit of
    any one mask.
    """
    required = target.get("supported_features")
    if not required:
        return True
    return any(state_features & int(mask) == int(mask) for mask in required)


def _entity_id_for(hass: HomeAssistant, registry_id: str) -> str | None:
    """Return the current entity_id of a registry entry."""
    entry = er.async_get(hass).entities.get_entry(registry_id)
    return None if entry is None else entry.entity_id


def _registry_id_for(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return the registry ID of an entity_id."""
    entry = er.async_get(hass).async_get(entity_id)
    return None if entry is None else entry.id


def granted_services(
    hass: HomeAssistant,
    perms: Permissions,
    registry_id: str,
    descriptions: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    """
    Return the services a controlled entity accepts, less its exceptions.

    Only services of the entity's own domain are considered. Cross-domain
    services such as homeassistant.turn_off are never offered.
    """
    entity_id = _entity_id_for(hass, registry_id)
    if entity_id is None:
        return set()

    domain = entity_id.partition(".")[0]
    state = hass.states.get(entity_id)
    features = (
        0 if state is None else int(state.attributes.get("supported_features", 0))
    )

    granted: set[str] = set()
    for service, description in descriptions.get(domain, {}).items():
        filters = description.get("target", {}).get("entity")
        # A service with no entity target does not act on an entity.
        if not filters:
            continue
        if any(matches_supported_features(features, one) for one in filters):
            granted.add(f"{domain}.{service}")

    return granted - set(perms.exceptions.get(registry_id, ()))


def authorise_read(
    hass: HomeAssistant, perms: Permissions, entity_id: str
) -> str | None:
    """Return an error code if the entity may not be read, else None."""
    registry_id = _registry_id_for(hass, entity_id)
    if registry_id is None or registry_id not in (perms.read | perms.control):
        return ERROR_NOT_FOUND
    return None


def authorise_action(
    hass: HomeAssistant,
    perms: Permissions,
    entity_id: str,
    action: str,
    descriptions: Mapping[str, Mapping[str, Any]],
) -> str | None:
    """
    Return an error code if the action is not permitted, else None.

    The entity check runs before the action check. An unselected entity
    returns not_found whatever action was requested.
    """
    registry_id = _registry_id_for(hass, entity_id)
    if registry_id is None or registry_id not in perms.control:
        return ERROR_NOT_FOUND
    if action not in granted_services(hass, perms, registry_id, descriptions):
        return ERROR_ACTION_NOT_ALLOWED
    return None
