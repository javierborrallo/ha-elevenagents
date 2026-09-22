"""Tests for the permission model: target resolution and the authorisation boundary."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elevenlabs_agent.const import (
    ERROR_ACTION_NOT_ALLOWED,
    ERROR_NOT_FOUND,
    OPT_CONFIRM_TIMEOUT,
    OPT_CONTROL_ENTITIES,
    OPT_EXCEPTIONS,
    OPT_READ_ENTITIES,
)
from custom_components.elevenlabs_agent.permissions import (
    Permissions,
    authorise_action,
    authorise_read,
    granted_services,
    is_missing,
    matches_supported_features,
    removed_registry_keys,
    resolve_target,
    stored_keys,
    without_keys,
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


async def test_resolve_target_keys_an_unregistered_entity_by_entity_id(hass):
    """An entity outside the registry is keyed by its entity_id."""
    hass.states.async_set("light.unregistered", "on")

    resolved = resolve_target(hass, {"entity_id": ["light.unregistered"]})

    assert resolved == {"light.unregistered"}


async def test_an_unregistered_entity_can_be_read_and_controlled(hass):
    """The two key forms reach the same authorisation path."""
    hass.states.async_set("lock.yaml_lock", "locked")
    descriptions = {"lock": {"lock": {"target": {"entity": [{"domain": ["lock"]}]}}}}
    perms = Permissions(
        read=frozenset(),
        control=frozenset({"lock.yaml_lock"}),
        exceptions={},
    )

    assert authorise_read(hass, perms, "lock.yaml_lock") is None
    assert (
        authorise_action(hass, perms, "lock.yaml_lock", "lock.lock", descriptions)
        is None
    )
    assert granted_services(hass, perms, "lock.yaml_lock", descriptions) == {
        "lock.lock"
    }


async def test_an_exception_applies_to_an_unregistered_entity(hass):
    """Exceptions are keyed the same way as the grant."""
    hass.states.async_set("lock.yaml_lock", "locked")
    descriptions = {
        "lock": {
            "lock": {"target": {"entity": [{"domain": ["lock"]}]}},
            "unlock": {"target": {"entity": [{"domain": ["lock"]}]}},
        }
    }
    perms = Permissions(
        read=frozenset(),
        control=frozenset({"lock.yaml_lock"}),
        exceptions={"lock.yaml_lock": frozenset({"lock.unlock"})},
    )

    assert granted_services(hass, perms, "lock.yaml_lock", descriptions) == {
        "lock.lock"
    }
    assert (
        authorise_action(hass, perms, "lock.yaml_lock", "lock.unlock", descriptions)
        == ERROR_ACTION_NOT_ALLOWED
    )


async def test_an_entity_id_that_holds_no_state_is_not_found(hass):
    """A name the caller invented is not a permission key."""
    perms = Permissions(
        read=frozenset({"light.ghost"}), control=frozenset(), exceptions={}
    )

    assert authorise_read(hass, perms, "light.ghost") == ERROR_NOT_FOUND


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


def test_stored_keys_reads_both_lists_and_the_exceptions():
    """A key held only by a restriction is still a stored key."""
    options = {
        OPT_READ_ENTITIES: ["a"],
        OPT_CONTROL_ENTITIES: ["b"],
        OPT_EXCEPTIONS: {"c": ["lock.unlock"]},
    }
    assert stored_keys(options) == {"a", "b", "c"}


async def test_a_registry_key_is_missing_once_its_entry_is_removed(
    hass, entity_registry
):
    """The registry is the only witness for a registry key."""
    entry = entity_registry.async_get_or_create("light", "demo", "gone")
    assert not is_missing(hass, entry.id)
    entity_registry.async_remove(entry.entity_id)
    assert is_missing(hass, entry.id)


async def test_an_entity_id_key_is_missing_without_a_state(hass):
    """An entity outside the registry exists only while it holds a state."""
    hass.states.async_set("sensor.yaml_only", "1")
    assert not is_missing(hass, "sensor.yaml_only")
    assert is_missing(hass, "sensor.never_there")


async def test_removed_registry_keys_never_names_an_entity_id_key(
    hass, entity_registry
):
    """A reload hides a YAML entity's state; that must never prune it."""
    entry = entity_registry.async_get_or_create("light", "demo", "gone")
    kept = entity_registry.async_get_or_create("light", "demo", "kept")
    entity_registry.async_remove(entry.entity_id)
    options = {
        OPT_READ_ENTITIES: [entry.id, kept.id, "sensor.absent_during_reload"],
        OPT_EXCEPTIONS: {entry.id: ["light.turn_on"]},
    }

    assert removed_registry_keys(hass, options) == {entry.id}


def test_without_keys_drops_from_every_place_and_touches_nothing_else():
    """A forgotten key leaves no restriction behind to be inherited."""
    options = {
        OPT_READ_ENTITIES: ["a", "b"],
        OPT_CONTROL_ENTITIES: ["a"],
        OPT_EXCEPTIONS: {"a": ["lock.unlock"], "b": ["light.turn_on"]},
        OPT_CONFIRM_TIMEOUT: 8,
    }

    result = without_keys(options, {"a"})

    assert result == {
        OPT_READ_ENTITIES: ["b"],
        OPT_CONTROL_ENTITIES: [],
        OPT_EXCEPTIONS: {"b": ["light.turn_on"]},
        OPT_CONFIRM_TIMEOUT: 8,
    }
    assert options[OPT_READ_ENTITIES] == ["a", "b"]


def test_without_keys_leaves_an_absent_list_absent():
    """Writing an empty list where none existed changes the stored shape."""
    assert without_keys({OPT_CONFIRM_TIMEOUT: 8}, {"a"}) == {OPT_CONFIRM_TIMEOUT: 8}
