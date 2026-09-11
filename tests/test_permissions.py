"""Tests for the permission model: target resolution and the authorisation boundary."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elevenlabs_agent.const import (
    ERROR_ACTION_NOT_ALLOWED,
    ERROR_NOT_FOUND,
)
from custom_components.elevenlabs_agent.permissions import (
    Permissions,
    authorise_action,
    authorise_read,
    granted_services,
    matches_supported_features,
    resolve_target,
)


async def test_resolve_target_includes_entity_area_and_device_area(
    hass, entity_registry, device_registry, area_registry
):
    """A target resolves entities by their own area and by their device's."""
    area = area_registry.async_create("Salon")

    own = entity_registry.async_get_or_create(
        "light", "demo", "own", suggested_object_id="own"
    )
    entity_registry.async_update_entity(own.entity_id, area_id=area.id)

    config_entry = MockConfigEntry(domain="demo")
    config_entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={("demo", "dev")},
    )
    device_registry.async_update_device(device.id, area_id=area.id)
    inherited = entity_registry.async_get_or_create(
        "light", "demo", "inherited", device_id=device.id
    )

    outside = entity_registry.async_get_or_create("light", "demo", "outside")

    resolved = resolve_target(hass, {"area_id": [area.id]})

    assert own.id in resolved
    assert inherited.id in resolved
    assert outside.id not in resolved


async def test_resolve_target_skips_entities_with_no_registry_entry(hass):
    """An entity that is not in the registry cannot be selected."""
    hass.states.async_set("light.unregistered", "on")

    resolved = resolve_target(hass, {"entity_id": ["light.unregistered"]})

    assert resolved == set()


@pytest.mark.parametrize(
    ("features", "target", "expected"),
    [
        # Entries are masks. Home Assistant ORs a nested YAML list into one
        # int before this code ever sees it, so [1, 2] means mask 1 or mask 2.
        (1, {"supported_features": [1, 2]}, True),
        (2, {"supported_features": [1, 2]}, True),
        (4, {"supported_features": [1, 2]}, False),
        # A combined mask needs every bit, not any bit.
        (3, {"supported_features": [3]}, True),
        (1, {"supported_features": [3]}, False),
        # No filter accepts anything.
        (0, {}, True),
    ],
)
def test_matches_supported_features(features, target, expected):
    """An entry is a mask and every bit of it must be set."""
    assert matches_supported_features(features, target) is expected


def test_a_mask_is_not_matched_by_one_of_its_bits():
    """
    climate.set_temperature publishes [1, 2], never [[1, 2]].

    Verified 11 September 2026 against the running instance: the API returns
    supported_features as a flat list of already-combined masks, because
    _validate_supported_features ORs each entry during schema validation.
    An earlier version of this function treated a nested list as the
    conjunction case and used a truthy bitwise-and for the rest, which
    granted an action to an entity holding only one bit of a combined mask.
    """
    assert matches_supported_features(1, {"supported_features": [3]}) is False


async def test_granted_services_is_the_entity_domain_minus_exceptions(
    hass, entity_registry
):
    """Selecting an entity grants its domain's services, less what was removed."""
    entry = entity_registry.async_get_or_create("light", "demo", "one")
    hass.states.async_set(entry.entity_id, "off", {"supported_features": 0})

    descriptions: dict[str, dict[str, Any]] = {
        "light": {
            "turn_on": {"target": {"entity": [{"domain": ["light"]}]}},
            "turn_off": {"target": {"entity": [{"domain": ["light"]}]}},
            "toggle": {"target": {"entity": [{"domain": ["light"]}]}},
            # A reload service carries no target. It is not an entity
            # service and must never be granted.
            "reload": {},
        },
        # Present in the instance and deliberately never offered.
        "homeassistant": {"turn_off": {}},
    }

    perms = Permissions(
        read=frozenset(),
        control=frozenset({entry.id}),
        exceptions={entry.id: frozenset({"light.toggle"})},
    )

    assert granted_services(hass, perms, entry.id, descriptions) == {
        "light.turn_on",
        "light.turn_off",
    }


async def test_a_service_without_an_entity_target_is_never_granted(
    hass, entity_registry
):
    """
    Selecting a script must not grant script.reload.

    Verified 11 September 2026 against the running instance: 54 services have
    no target, and seven of them are in domains a user would expose —
    scene.reload, scene.apply, scene.create, group.reload, group.set,
    group.remove and script.reload. An earlier version defaulted a missing
    target to [{}], which matched unconditionally and granted every one.
    """
    entry = entity_registry.async_get_or_create("script", "demo", "night")
    hass.states.async_set(entry.entity_id, "off", {"supported_features": 0})

    descriptions = {
        "script": {
            "turn_on": {"target": {"entity": [{"domain": ["script"]}]}},
            "reload": {},
        }
    }
    perms = Permissions(read=frozenset(), control=frozenset({entry.id}), exceptions={})

    granted = granted_services(hass, perms, entry.id, descriptions)

    assert granted == {"script.turn_on"}
    assert "script.reload" not in granted


async def test_unselected_entity_is_not_found_whatever_was_asked(hass, entity_registry):
    """An unselected entity never reveals that it exists."""
    lock = entity_registry.async_get_or_create("lock", "demo", "front")
    hass.states.async_set(lock.entity_id, "locked", {"supported_features": 0})

    descriptions = {"lock": {"unlock": {"target": {"entity": [{"domain": ["lock"]}]}}}}
    perms = Permissions(read=frozenset(), control=frozenset(), exceptions={})

    assert (
        authorise_action(hass, perms, lock.entity_id, "lock.unlock", descriptions)
        == ERROR_NOT_FOUND
    )
    assert authorise_read(hass, perms, lock.entity_id) == ERROR_NOT_FOUND


async def test_unselected_entity_hides_even_an_ungranted_action(
    hass, entity_registry
) -> None:
    """
    The entity check must run before the action check.

    The existing not-found test asks for an action the domain does grant, so
    it passes under either ordering. This one asks for an action the entity
    cannot take: with the checks reversed the handler answers
    action_not_allowed, which confirms the lock exists.
    """
    lock = entity_registry.async_get_or_create("lock", "demo", "front")
    hass.states.async_set(lock.entity_id, "locked", {"supported_features": 0})

    descriptions: dict[str, dict[str, Any]] = {
        "lock": {
            "open": {
                "target": {"entity": [{"domain": ["lock"], "supported_features": [1]}]}
            }
        }
    }
    perms = Permissions(read=frozenset(), control=frozenset(), exceptions={})

    assert (
        authorise_action(hass, perms, lock.entity_id, "lock.open", descriptions)
        == ERROR_NOT_FOUND
    )


async def test_selected_entity_with_a_removed_service_says_action_not_allowed(
    hass, entity_registry
):
    """A selected entity discloses nothing new, so the real reason is given."""
    lock = entity_registry.async_get_or_create("lock", "demo", "front")
    hass.states.async_set(lock.entity_id, "locked", {"supported_features": 0})

    descriptions = {
        "lock": {
            "lock": {"target": {"entity": [{"domain": ["lock"]}]}},
            "unlock": {"target": {"entity": [{"domain": ["lock"]}]}},
        }
    }
    perms = Permissions(
        read=frozenset(),
        control=frozenset({lock.id}),
        exceptions={lock.id: frozenset({"lock.unlock"})},
    )

    assert (
        authorise_action(hass, perms, lock.entity_id, "lock.unlock", descriptions)
        == ERROR_ACTION_NOT_ALLOWED
    )
    assert (
        authorise_action(hass, perms, lock.entity_id, "lock.lock", descriptions) is None
    )


async def test_control_implies_read(hass, entity_registry):
    """An entity selected only for control is still readable."""
    entry = entity_registry.async_get_or_create("light", "demo", "one")
    hass.states.async_set(entry.entity_id, "on", {})

    perms = Permissions(read=frozenset(), control=frozenset({entry.id}), exceptions={})

    assert authorise_read(hass, perms, entry.entity_id) is None


async def test_read_does_not_imply_control(hass, entity_registry):
    """An entity selected only for reading has no service to call."""
    entry = entity_registry.async_get_or_create("light", "demo", "one")
    hass.states.async_set(entry.entity_id, "on", {"supported_features": 0})

    descriptions = {
        "light": {"turn_on": {"target": {"entity": [{"domain": ["light"]}]}}}
    }
    perms = Permissions(read=frozenset({entry.id}), control=frozenset(), exceptions={})

    assert (
        authorise_action(hass, perms, entry.entity_id, "light.turn_on", descriptions)
        == ERROR_NOT_FOUND
    )
