"""
Permission model: what the agent may read and what it may do.

A permission key identifies one entity. It is the entity registry ID when
the entity has a registry entry, and the entity_id when it does not. An
entity enters the registry only if its integration gives it a unique_id,
so entities from YAML, templates and groups stay outside it. The two key
forms never collide: a registry ID holds no dot, an entity_id always does.
"""

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
    from collections.abc import Iterable, Mapping

    from homeassistant.core import HomeAssistant


@dataclass(frozen=True)
class Permissions:
    """Permission keys the user selected, and the services they removed."""

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
    """Resolve a target selector value to permission keys."""
    selected = async_extract_referenced_entity_ids(hass, TargetSelection(target))
    entities = er.async_get(hass)

    resolved: set[str] = set()
    for entity_id in selected.referenced | selected.indirectly_referenced:
        entry = entities.async_get(entity_id)
        resolved.add(entity_id if entry is None else entry.id)
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


def entity_id_for(hass: HomeAssistant, key: str) -> str | None:
    """
    Return the entity_id a permission key points to.

    A registry key whose entry is gone returns None. A key that is itself
    an entity_id returns unchanged.
    """
    entry = er.async_get(hass).entities.get_entry(key)
    if entry is not None:
        return entry.entity_id
    return key if "." in key else None


def stored_keys(options: Mapping[str, Any]) -> set[str]:
    """Return every permission key in the two lists and the exceptions."""
    return (
        set(options.get(OPT_READ_ENTITIES, ()))
        | set(options.get(OPT_CONTROL_ENTITIES, ()))
        | set(options.get(OPT_EXCEPTIONS, {}))
    )


def is_missing(hass: HomeAssistant, key: str) -> bool:
    """
    Return True when the entity a permission key names does not exist now.

    A registry key is missing when its registry entry is gone. An entity_id
    key is missing when it holds no state.
    """
    if "." in key:
        return hass.states.get(key) is None
    return er.async_get(hass).entities.get_entry(key) is None


def removed_registry_keys(hass: HomeAssistant, options: Mapping[str, Any]) -> set[str]:
    """Return the stored registry keys whose registry entry is gone."""
    registry = er.async_get(hass)
    return {
        key
        for key in stored_keys(options)
        if "." not in key and registry.entities.get_entry(key) is None
    }


def without_keys(options: Mapping[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    """Return a copy of options with keys dropped from both lists and the exceptions."""
    drop = set(keys)
    result = dict(options)
    for list_key in (OPT_READ_ENTITIES, OPT_CONTROL_ENTITIES):
        if list_key in result:
            result[list_key] = [key for key in result[list_key] if key not in drop]
    if OPT_EXCEPTIONS in result:
        result[OPT_EXCEPTIONS] = {
            key: services
            for key, services in result[OPT_EXCEPTIONS].items()
            if key not in drop
        }
    return result


def _key_for(hass: HomeAssistant, entity_id: str) -> str | None:
    """
    Return the permission key of an entity_id.

    An entity outside the registry is keyed by its entity_id. It must hold
    a state, so a name the caller invented never becomes a key.
    """
    entry = er.async_get(hass).async_get(entity_id)
    if entry is not None:
        return entry.id
    return entity_id if hass.states.get(entity_id) is not None else None


def granted_services(
    hass: HomeAssistant,
    perms: Permissions,
    key: str,
    descriptions: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    """
    Return the services a controlled entity accepts, less its exceptions.

    Only services of the entity's own domain are considered. Cross-domain
    services such as homeassistant.turn_off are never offered.
    """
    entity_id = entity_id_for(hass, key)
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

    return granted - set(perms.exceptions.get(key, ()))


def authorise_read(
    hass: HomeAssistant, perms: Permissions, entity_id: str
) -> str | None:
    """Return an error code if the entity may not be read, else None."""
    key = _key_for(hass, entity_id)
    if key is None or key not in (perms.read | perms.control):
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
    key = _key_for(hass, entity_id)
    if key is None or key not in perms.control:
        return ERROR_NOT_FOUND
    if action not in granted_services(hass, perms, key, descriptions):
        return ERROR_ACTION_NOT_ALLOWED
    return None
