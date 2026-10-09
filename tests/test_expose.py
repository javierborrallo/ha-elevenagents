"""Tests for automatic exposure."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.network import NoURLAvailableError
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.elevenlabs_agent.const import (
    DOMAIN,
    ERROR_NOT_FOUND,
    ISSUE_MISSING_ENTITIES,
    OPT_AUTO_CONTROL,
    OPT_AUTO_EXPOSE,
    OPT_AUTO_EXPOSE_AREAS,
    OPT_CONTROL_ENTITIES,
    OPT_CONTROL_TARGET,
    OPT_EXCEPTIONS,
    OPT_READ_ENTITIES,
    OPT_READ_TARGET,
)
from custom_components.elevenlabs_agent.expose import _synchronise
from custom_components.elevenlabs_agent.permissions import (
    authorise_read,
    from_options,
)
from custom_components.elevenlabs_agent.tool_sync import ToolSyncError, entity_catalogue

if TYPE_CHECKING:
    from homeassistant.helpers import entity_registry as er

# homeassistant 2026.9.1's http server sets self.app["hass"] as a plain key.
# aiohttp 3.14.3 raises NotAppKeyWarning for that pattern. pyproject.toml
# turns every warning into an error, so this module ignores this one category.
pytestmark = pytest.mark.filterwarnings(
    "ignore::aiohttp.web_exceptions.NotAppKeyWarning"
)

DEBOUNCE = 11


@pytest.fixture
def freezer(hass, freezer):
    """
    Wrap freezegun's tick so it also drives Home Assistant's scheduled timers.

    async_call_later schedules through the event loop's own clock, which a
    frozen wall clock does not advance by itself. Firing a time-changed event
    after each tick lets any timer that is now due actually run.
    """
    original_tick = freezer.tick

    def _tick(*args: object, **kwargs: object) -> None:
        original_tick(*args, **kwargs)
        async_fire_time_changed(hass)

    freezer.tick = _tick
    return freezer


@pytest.fixture(autouse=True)
def mock_sync_tools():
    """Keep the debounced flush off the network in every test in this module."""
    with patch(
        "custom_components.elevenlabs_agent.expose.async_sync_tools", AsyncMock()
    ) as mock:
        yield mock


def _record_catalogue(hass, entry) -> None:
    """Store the catalogue as if a sync had just sent it."""
    perms = from_options(entry.options)
    entry.runtime_data.catalogue = entity_catalogue(hass, perms.read | perms.control)


@pytest.fixture
def setup_entry(hass, setup_entry):
    """Record the catalogue the patched initial sync would have sent."""
    _record_catalogue(hass, setup_entry)
    return setup_entry


async def _new_entity_in(
    hass, entity_registry, area_id, domain="light", name="new"
) -> er.RegistryEntry:
    """Register an entity and place it in an area."""
    entry: er.RegistryEntry = entity_registry.async_get_or_create(domain, "demo", name)
    entity_registry.async_update_entity(entry.entity_id, area_id=area_id)
    return entry


async def test_a_new_entity_in_a_watched_area_becomes_readable(
    hass, setup_entry, entity_registry, area_registry, freezer
) -> None:
    """The switch is what the user turned on, so it must do this."""
    area = area_registry.async_create("Salon")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={
            OPT_READ_TARGET: {"area_id": [area.id]},
            OPT_AUTO_EXPOSE: True,
        },
    )
    await hass.async_block_till_done()

    entry = await _new_entity_in(hass, entity_registry, area.id)
    freezer.tick(DEBOUNCE)
    await hass.async_block_till_done()

    assert entry.id in setup_entry.options[OPT_READ_ENTITIES]


async def test_reading_alone_never_grants_control(
    hass, setup_entry, entity_registry, area_registry, freezer
) -> None:
    """The two switches are separate decisions. This is the whole point of them."""
    area = area_registry.async_create("Salon")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={
            OPT_READ_TARGET: {"area_id": [area.id]},
            OPT_CONTROL_TARGET: {"area_id": [area.id]},
            OPT_AUTO_EXPOSE: True,
            OPT_AUTO_CONTROL: False,
        },
    )
    await hass.async_block_till_done()

    entry = await _new_entity_in(hass, entity_registry, area.id, domain="lock")
    freezer.tick(DEBOUNCE)
    await hass.async_block_till_done()

    assert entry.id in setup_entry.options[OPT_READ_ENTITIES]
    assert entry.id not in setup_entry.options.get(OPT_CONTROL_ENTITIES, [])


async def test_the_control_switch_grants_control(
    hass, setup_entry, entity_registry, area_registry, freezer
) -> None:
    """Turned on deliberately, it does what it says."""
    area = area_registry.async_create("Garaje")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={
            OPT_CONTROL_TARGET: {"area_id": [area.id]},
            OPT_AUTO_CONTROL: True,
        },
    )
    await hass.async_block_till_done()

    entry = await _new_entity_in(hass, entity_registry, area.id, domain="switch")
    freezer.tick(DEBOUNCE)
    await hass.async_block_till_done()

    assert entry.id in setup_entry.options[OPT_CONTROL_ENTITIES]


async def test_an_entity_outside_the_selected_areas_is_ignored(
    hass, setup_entry, entity_registry, area_registry, freezer
) -> None:
    """The switch says "the areas you already selected", not "everything"."""
    watched = area_registry.async_create("Salon")
    other = area_registry.async_create("Garaje")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={OPT_READ_TARGET: {"area_id": [watched.id]}, OPT_AUTO_EXPOSE: True},
    )
    await hass.async_block_till_done()

    entry = await _new_entity_in(hass, entity_registry, other.id)
    freezer.tick(DEBOUNCE)
    await hass.async_block_till_done()

    assert entry.id not in setup_entry.options.get(OPT_READ_ENTITIES, [])


async def test_nothing_happens_when_both_switches_are_off(
    hass, setup_entry, entity_registry, area_registry, freezer
) -> None:
    """Off by default means off."""
    area = area_registry.async_create("Salon")
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_READ_TARGET: {"area_id": [area.id]}}
    )
    await hass.async_block_till_done()

    entry = await _new_entity_in(hass, entity_registry, area.id)
    freezer.tick(DEBOUNCE)
    await hass.async_block_till_done()

    assert entry.id not in setup_entry.options.get(OPT_READ_ENTITIES, [])


async def test_a_burst_produces_one_write(
    hass, setup_entry, entity_registry, area_registry, freezer
) -> None:
    """Installing an integration must not mean dozens of writes to someone's account."""
    area = area_registry.async_create("Salon")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={OPT_READ_TARGET: {"area_id": [area.id]}, OPT_AUTO_EXPOSE: True},
    )
    await hass.async_block_till_done()

    with patch(
        "custom_components.elevenlabs_agent.expose.async_sync_tools", AsyncMock()
    ) as mock_sync:
        created = []
        for index in range(5):
            entry = await _new_entity_in(
                hass, entity_registry, area.id, name=f"b{index}"
            )
            created.append(entry.id)
            freezer.tick(1)
            await hass.async_block_till_done()

        assert OPT_READ_ENTITIES not in setup_entry.options

        freezer.tick(DEBOUNCE)
        await hass.async_block_till_done()

    assert set(created) <= set(setup_entry.options[OPT_READ_ENTITIES])
    mock_sync.assert_awaited_once()


async def test_an_entity_with_no_area_is_ignored(
    hass, setup_entry, entity_registry, area_registry, freezer
) -> None:
    """A new entity nobody placed anywhere is in no selected area."""
    area = area_registry.async_create("Salon")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={OPT_READ_TARGET: {"area_id": [area.id]}, OPT_AUTO_EXPOSE: True},
    )
    await hass.async_block_till_done()

    entry = entity_registry.async_get_or_create("light", "demo", "homeless")
    freezer.tick(DEBOUNCE)
    await hass.async_block_till_done()

    assert entry.id not in setup_entry.options.get(OPT_READ_ENTITIES, [])


async def test_a_written_allowlist_is_synchronised_with_the_agent(
    hass, setup_entry, entity_registry, area_registry, freezer
) -> None:
    """
    A widened allowlist the agent never hears about is half a feature.

    Without this assertion, removing the synchronisation call leaves every
    other test in this module passing while the tool schema silently stops
    matching the options.
    """
    area = area_registry.async_create("Salon")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={OPT_READ_TARGET: {"area_id": [area.id]}, OPT_AUTO_EXPOSE: True},
    )
    await hass.async_block_till_done()

    with patch(
        "custom_components.elevenlabs_agent.expose.async_sync_tools", AsyncMock()
    ) as mock_sync:
        await _new_entity_in(hass, entity_registry, area.id)
        freezer.tick(DEBOUNCE)
        await hass.async_block_till_done()

    mock_sync.assert_awaited_once()


async def test_moving_an_existing_entity_into_the_area_does_not_expose_it(
    hass, setup_entry, entity_registry, area_registry, freezer
) -> None:
    """Automatic exposure reacts to a new entity, never to a later registry update."""
    watched = area_registry.async_create("Salon")
    other = area_registry.async_create("Garaje")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={OPT_READ_TARGET: {"area_id": [watched.id]}, OPT_AUTO_EXPOSE: True},
    )
    await hass.async_block_till_done()

    entry = await _new_entity_in(hass, entity_registry, other.id)
    freezer.tick(DEBOUNCE)
    await hass.async_block_till_done()
    assert entry.id not in setup_entry.options.get(OPT_READ_ENTITIES, [])

    entity_registry.async_update_entity(entry.entity_id, area_id=watched.id)
    freezer.tick(DEBOUNCE)
    await hass.async_block_till_done()

    assert entry.id not in setup_entry.options.get(OPT_READ_ENTITIES, [])


async def test_a_target_selecting_a_floor_also_works(
    hass, setup_entry, entity_registry, area_registry, freezer
) -> None:
    """A target is resolved as a whole, not read for its area_id key alone."""
    floor = fr.async_get(hass).async_create("Upstairs")
    area = area_registry.async_create("Dormitorio")
    area_registry.async_update(area.id, floor_id=floor.floor_id)
    hass.config_entries.async_update_entry(
        setup_entry,
        options={
            OPT_READ_TARGET: {"floor_id": [floor.floor_id]},
            OPT_AUTO_EXPOSE: True,
        },
    )
    await hass.async_block_till_done()

    entry = await _new_entity_in(hass, entity_registry, area.id)
    freezer.tick(DEBOUNCE)
    await hass.async_block_till_done()

    assert entry.id in setup_entry.options[OPT_READ_ENTITIES]


async def test_tearing_down_before_the_debounce_elapses_writes_nothing(
    hass, setup_entry, entity_registry, area_registry, freezer
) -> None:
    """An armed flush must not survive the entry it belongs to."""
    area = area_registry.async_create("Salon")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={OPT_READ_TARGET: {"area_id": [area.id]}, OPT_AUTO_EXPOSE: True},
    )
    await hass.async_block_till_done()

    await _new_entity_in(hass, entity_registry, area.id)

    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()

    freezer.tick(DEBOUNCE)
    await hass.async_block_till_done()

    assert OPT_READ_ENTITIES not in setup_entry.options


async def test_the_areas_key_drives_both_switches_when_present(
    hass, setup_entry, entity_registry, area_registry, freezer
) -> None:
    """The panel writes one area list; both switches watch it independently."""
    area = area_registry.async_create("Salon")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={
            OPT_AUTO_EXPOSE: True,
            OPT_AUTO_CONTROL: True,
            OPT_AUTO_EXPOSE_AREAS: [area.id],
        },
    )
    await hass.async_block_till_done()

    entry = await _new_entity_in(hass, entity_registry, area.id)
    freezer.tick(DEBOUNCE)
    await hass.async_block_till_done()

    assert entry.id in setup_entry.options[OPT_READ_ENTITIES]
    assert entry.id in setup_entry.options[OPT_CONTROL_ENTITIES]


async def test_the_areas_key_lets_only_the_control_switch_grant_control(
    hass, setup_entry, entity_registry, area_registry, freezer
) -> None:
    """With only control on, reading stays untouched even with the areas key set."""
    area = area_registry.async_create("Salon")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={OPT_AUTO_CONTROL: True, OPT_AUTO_EXPOSE_AREAS: [area.id]},
    )
    await hass.async_block_till_done()

    entry = await _new_entity_in(hass, entity_registry, area.id, domain="switch")
    freezer.tick(DEBOUNCE)
    await hass.async_block_till_done()

    assert entry.id in setup_entry.options[OPT_CONTROL_ENTITIES]
    assert entry.id not in setup_entry.options.get(OPT_READ_ENTITIES, [])


async def test_no_areas_key_falls_back_to_the_stored_target(
    hass, setup_entry, entity_registry, area_registry, freezer
) -> None:
    """A 0.1.1 entry with no areas key keeps working from its stored target."""
    area = area_registry.async_create("Salon")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={OPT_READ_TARGET: {"area_id": [area.id]}, OPT_AUTO_EXPOSE: True},
    )
    await hass.async_block_till_done()

    entry = await _new_entity_in(hass, entity_registry, area.id)
    freezer.tick(DEBOUNCE)
    await hass.async_block_till_done()

    assert entry.id in setup_entry.options[OPT_READ_ENTITIES]


async def test_the_areas_key_overrides_a_stale_target(
    hass, setup_entry, entity_registry, area_registry, freezer
) -> None:
    """A panel-set areas list wins over a still-stored 0.1.1 target."""
    old_target_area = area_registry.async_create("Garaje")
    new_area = area_registry.async_create("Salon")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={
            OPT_READ_TARGET: {"area_id": [old_target_area.id]},
            OPT_AUTO_EXPOSE: True,
            OPT_AUTO_EXPOSE_AREAS: [new_area.id],
        },
    )
    await hass.async_block_till_done()

    in_new_area = await _new_entity_in(
        hass, entity_registry, new_area.id, name="in_new"
    )
    in_old_area = await _new_entity_in(
        hass, entity_registry, old_target_area.id, name="in_old"
    )
    freezer.tick(DEBOUNCE)
    await hass.async_block_till_done()

    assert in_new_area.id in setup_entry.options[OPT_READ_ENTITIES]
    assert in_old_area.id not in setup_entry.options.get(OPT_READ_ENTITIES, [])


async def test_an_empty_areas_key_overrides_a_stale_target(
    hass, setup_entry, entity_registry, area_registry, freezer
) -> None:
    """Clearing every area in the panel stops watching the old target's areas."""
    area = area_registry.async_create("Salon")
    hass.config_entries.async_update_entry(
        setup_entry,
        options={
            OPT_READ_TARGET: {"area_id": [area.id]},
            OPT_AUTO_EXPOSE: True,
            OPT_AUTO_EXPOSE_AREAS: [],
        },
    )
    await hass.async_block_till_done()

    entry = await _new_entity_in(hass, entity_registry, area.id)
    freezer.tick(DEBOUNCE)
    await hass.async_block_till_done()

    assert entry.id not in setup_entry.options.get(OPT_READ_ENTITIES, [])


async def _granted(
    hass, entity_registry, entry, freezer, key=OPT_READ_ENTITIES
) -> er.RegistryEntry:
    """
    Register an entity, grant it under key, and record the catalogue.

    Return after the debounce that the registration armed has fired.
    """
    registered: er.RegistryEntry = entity_registry.async_get_or_create(
        "light", "demo", "kept"
    )
    hass.config_entries.async_update_entry(entry, options={key: [registered.id]})
    _record_catalogue(hass, entry)
    await _settle(hass, freezer)
    return registered


async def _settle(hass, freezer) -> None:
    """Let the debounce fire and the flush finish."""
    freezer.tick(DEBOUNCE)
    await hass.async_block_till_done()


async def test_deleting_a_granted_entity_removes_its_permission_and_synchronises(
    hass, setup_entry, entity_registry, freezer, mock_sync_tools
) -> None:
    """A permission dies with its entity."""
    granted = await _granted(
        hass, entity_registry, setup_entry, freezer, key=OPT_CONTROL_ENTITIES
    )
    hass.config_entries.async_update_entry(
        setup_entry,
        options={
            **setup_entry.options,
            OPT_EXCEPTIONS: {granted.id: ["light.turn_on"]},
        },
    )

    entity_registry.async_remove(granted.entity_id)
    await _settle(hass, freezer)

    assert setup_entry.options[OPT_CONTROL_ENTITIES] == []
    assert setup_entry.options[OPT_EXCEPTIONS] == {}
    mock_sync_tools.assert_awaited_once()


async def test_deleting_an_entity_nobody_granted_writes_nothing(
    hass, setup_entry, entity_registry, freezer, mock_sync_tools
) -> None:
    """ElevenLabs is not written for a change the agent cannot see."""
    await _granted(hass, entity_registry, setup_entry, freezer)
    before = dict(setup_entry.options)
    other = entity_registry.async_get_or_create("light", "demo", "other")

    entity_registry.async_remove(other.entity_id)
    await _settle(hass, freezer)

    assert dict(setup_entry.options) == before
    mock_sync_tools.assert_not_awaited()


async def test_renaming_a_granted_entity_keeps_its_permission_and_synchronises(
    hass, setup_entry, entity_registry, freezer, mock_sync_tools
) -> None:
    """The key survives a rename; the enum must follow it."""
    granted = await _granted(
        hass, entity_registry, setup_entry, freezer, key=OPT_CONTROL_ENTITIES
    )

    entity_registry.async_update_entity(
        granted.entity_id, new_entity_id="light.renamed"
    )
    await _settle(hass, freezer)

    assert setup_entry.options[OPT_CONTROL_ENTITIES] == [granted.id]
    mock_sync_tools.assert_awaited_once()


async def test_moving_a_granted_entity_to_another_area_synchronises(
    hass, setup_entry, entity_registry, freezer, mock_sync_tools
) -> None:
    """The area is part of the line the model reads."""
    area = ar.async_get(hass).async_create("Cocina")
    granted = await _granted(hass, entity_registry, setup_entry, freezer)

    entity_registry.async_update_entity(granted.entity_id, area_id=area.id)
    await _settle(hass, freezer)

    mock_sync_tools.assert_awaited_once()


async def test_moving_the_device_of_a_granted_entity_synchronises(
    hass, setup_entry, entity_registry, freezer, mock_sync_tools
) -> None:
    """An entity with no area of its own inherits the area of its device."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=setup_entry.entry_id, identifiers={("demo", "lamp")}
    )
    lamp = entity_registry.async_get_or_create(
        "light", "demo", "lamp", device_id=device.id
    )
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_READ_ENTITIES: [lamp.id]}
    )
    area = ar.async_get(hass).async_create("Dormitorio")
    await _settle(hass, freezer)
    _record_catalogue(hass, setup_entry)
    mock_sync_tools.reset_mock()

    device_registry.async_update_device(device.id, area_id=area.id)
    await _settle(hass, freezer)

    mock_sync_tools.assert_awaited_once()


async def test_renaming_the_area_of_a_granted_entity_synchronises(
    hass, setup_entry, entity_registry, freezer, mock_sync_tools
) -> None:
    """The owner speaks the area's new name the next day."""
    area_registry = ar.async_get(hass)
    area = area_registry.async_create("Salon")
    granted = await _granted(hass, entity_registry, setup_entry, freezer)
    entity_registry.async_update_entity(granted.entity_id, area_id=area.id)
    await _settle(hass, freezer)
    _record_catalogue(hass, setup_entry)
    mock_sync_tools.reset_mock()

    area_registry.async_update(area.id, name="Sala de estar")
    await _settle(hass, freezer)

    mock_sync_tools.assert_awaited_once()


async def test_a_new_name_on_a_granted_entity_synchronises(
    hass, setup_entry, entity_registry, freezer, mock_sync_tools
) -> None:
    """
    The name in the catalogue comes from the state's friendly_name.

    This test writes the state itself, as the entity would. Task 6 checks
    that a real entity rewrites it inside the debounce.
    """
    granted = await _granted(hass, entity_registry, setup_entry, freezer)
    hass.states.async_set(granted.entity_id, "on", {"friendly_name": "Old"})
    _record_catalogue(hass, setup_entry)

    entity_registry.async_update_entity(granted.entity_id, name="New")
    hass.states.async_set(granted.entity_id, "on", {"friendly_name": "New"})
    await _settle(hass, freezer)

    mock_sync_tools.assert_awaited_once()


async def test_a_burst_of_deletions_produces_one_sync(
    hass, setup_entry, entity_registry, freezer, mock_sync_tools
) -> None:
    """Removing an integration deletes many entities at once."""
    first = entity_registry.async_get_or_create("light", "demo", "first")
    second = entity_registry.async_get_or_create("light", "demo", "second")
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_READ_ENTITIES: [first.id, second.id]}
    )
    _record_catalogue(hass, setup_entry)
    await _settle(hass, freezer)

    entity_registry.async_remove(first.entity_id)
    freezer.tick(2)
    entity_registry.async_remove(second.entity_id)
    await _settle(hass, freezer)

    mock_sync_tools.assert_awaited_once()


async def test_a_missing_entity_outside_the_registry_is_never_pruned(
    hass, setup_entry, entity_registry, freezer
) -> None:
    """Its state also disappears on a reload, which the listener cannot tell apart."""
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_READ_ENTITIES: ["sensor.template_gone"]}
    )
    _record_catalogue(hass, setup_entry)
    other = entity_registry.async_get_or_create("light", "demo", "other")

    entity_registry.async_remove(other.entity_id)
    await _settle(hass, freezer)

    assert setup_entry.options[OPT_READ_ENTITIES] == ["sensor.template_gone"]


async def test_a_deletion_while_not_running_is_pruned_at_setup(
    hass, setup_entry, entity_registry
) -> None:
    """The registry keeps no event for a listener that was not there."""
    gone = entity_registry.async_get_or_create("light", "demo", "gone")
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_READ_ENTITIES: [gone.id]}
    )
    await hass.config_entries.async_unload(setup_entry.entry_id)
    entity_registry.async_remove(gone.entity_id)

    with patch("custom_components.elevenlabs_agent.async_sync_tools", AsyncMock()):
        assert await hass.config_entries.async_setup(setup_entry.entry_id)
        await hass.async_block_till_done()

    assert setup_entry.options[OPT_READ_ENTITIES] == []


def _missing_issue(hass) -> ir.IssueEntry | None:
    """Return the missing entities issue, or None."""
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MISSING_ENTITIES)


async def _granted_outside_registry(hass, entry, freezer) -> None:
    """Grant an entity that has a state and no registry entry, and settle."""
    hass.states.async_set("sensor.yaml_only", "1")
    hass.config_entries.async_update_entry(
        entry, options={OPT_READ_ENTITIES: ["sensor.yaml_only"]}
    )
    _record_catalogue(hass, entry)
    await _settle(hass, freezer)


async def test_a_granted_entity_outside_the_registry_that_vanishes_raises_the_issue(
    hass, setup_entry, freezer
) -> None:
    """Its state is the only witness, so the state change triggers the check."""
    await _granted_outside_registry(hass, setup_entry, freezer)

    hass.states.async_remove("sensor.yaml_only")
    await _settle(hass, freezer)

    assert _missing_issue(hass) is not None


async def test_a_reload_inside_the_debounce_raises_nothing(
    hass, setup_entry, freezer
) -> None:
    """A template reload removes the state and restores it at once."""
    await _granted_outside_registry(hass, setup_entry, freezer)

    hass.states.async_remove("sensor.yaml_only")
    freezer.tick(1)
    hass.states.async_set("sensor.yaml_only", "1")
    await _settle(hass, freezer)

    assert _missing_issue(hass) is None


async def test_a_registry_entity_never_inherits_an_entity_id_key(
    hass, setup_entry, entity_registry, freezer, mock_sync_tools
) -> None:
    """A registry entity that takes a stored entity_id gets nothing."""
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_CONTROL_ENTITIES: ["sensor.yaml_only"]}
    )
    _record_catalogue(hass, setup_entry)

    new_entry = entity_registry.async_get_or_create(
        "sensor", "demo", "yaml_only", suggested_object_id="yaml_only"
    )
    await _settle(hass, freezer)

    assert new_entry.entity_id == "sensor.yaml_only"
    assert setup_entry.options[OPT_CONTROL_ENTITIES] == ["sensor.yaml_only"]
    perms = from_options(setup_entry.options)
    assert authorise_read(hass, perms, "sensor.yaml_only") == ERROR_NOT_FOUND
    assert entity_catalogue(hass, perms.control)[0] == []
    assert _missing_issue(hass) is not None


async def test_nothing_moves_when_no_key_matches(
    hass, setup_entry, entity_registry, freezer, mock_sync_tools
) -> None:
    """A newly registered entity nobody granted leaves the options untouched."""
    granted = await _granted(hass, entity_registry, setup_entry, freezer)
    before = dict(setup_entry.options)

    entity_registry.async_get_or_create("light", "demo", "brand_new")
    await hass.async_block_till_done()

    assert dict(setup_entry.options) == before
    assert setup_entry.options[OPT_READ_ENTITIES] == [granted.id]


async def test_a_tool_sync_error_from_the_listener_does_not_escape(
    hass, setup_entry, caplog
) -> None:
    """A read-back divergence during a registry sync is swallowed like any other."""
    with patch(
        "custom_components.elevenlabs_agent.expose.async_sync_tools",
        AsyncMock(side_effect=ToolSyncError("diverged")),
    ):
        await _synchronise(hass, setup_entry)

    assert any("could not reach ElevenLabs" in r.getMessage() for r in caplog.records)


async def test_a_missing_external_url_from_the_listener_does_not_escape(
    hass, setup_entry, caplog
) -> None:
    """The URL can vanish between the debounce firing and the sync running."""
    with patch(
        "custom_components.elevenlabs_agent.expose.async_sync_tools",
        AsyncMock(side_effect=NoURLAvailableError),
    ):
        await _synchronise(hass, setup_entry)

    assert any("could not reach ElevenLabs" in r.getMessage() for r in caplog.records)


async def test_a_missing_entity_is_found_when_home_assistant_has_started(
    hass, setup_entry, freezer
) -> None:
    """A restart sends no state change for an entity that never comes back."""
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_READ_ENTITIES: ["sensor.gone_before_start"]}
    )
    await hass.config_entries.async_unload(setup_entry.entry_id)

    with patch("custom_components.elevenlabs_agent.async_sync_tools", AsyncMock()):
        assert await hass.config_entries.async_setup(setup_entry.entry_id)
        await hass.async_block_till_done()
    await _settle(hass, freezer)

    assert _missing_issue(hass) is not None
