"""Tests for automatic exposure."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import floor_registry as fr
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.elevenlabs_agent.const import (
    OPT_AUTO_CONTROL,
    OPT_AUTO_EXPOSE,
    OPT_AUTO_EXPOSE_AREAS,
    OPT_CONTROL_ENTITIES,
    OPT_CONTROL_TARGET,
    OPT_READ_ENTITIES,
    OPT_READ_TARGET,
)

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


async def _new_entity_in(
    hass, entity_registry, area_id, domain="light", name="new"
) -> er.RegistryEntry:
    """Register an entity and place it in an area."""
    entry = entity_registry.async_get_or_create(domain, "demo", name)
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
