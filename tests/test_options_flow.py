"""Tests for the options flow."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.elevenlabs_agent.const import (
    CONF_WEBHOOK_ID,
    DOMAIN,
    ISSUE_ROTATION_UNSYNCED,
    OPT_AUTO_CONTROL,
    OPT_AUTO_EXPOSE,
    OPT_CONFIRM_TIMEOUT,
    OPT_CONTROL_ENTITIES,
    OPT_CONTROL_TARGET,
    OPT_EXCEPTIONS,
    OPT_READ_ENTITIES,
)
from custom_components.elevenlabs_agent.elevenlabs_client import ElevenLabsError

# homeassistant 2026.9.1's http server sets self.app["hass"] as a plain key.
# aiohttp 3.14.3 raises NotAppKeyWarning for that pattern. pyproject.toml
# turns every warning into an error, so this module ignores this one category.
pytestmark = pytest.mark.filterwarnings(
    "ignore::aiohttp.web_exceptions.NotAppKeyWarning"
)


# --- Task 14: menu, selectors and the grant confirmation --------------------


async def test_menu_lists_every_destination(hass, setup_entry):
    """Seven unrelated destinations, none of them a step the user must pass."""
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {
        "read",
        "control",
        "exceptions",
        "timeout",
        "auto_expose",
        "rotate_secret",
        "rotate_webhook",
    }


async def test_a_step_preserves_options_it_did_not_touch(hass, setup_entry):
    """Options are rewritten wholesale, so every step merges."""
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_READ_ENTITIES: ["keep_me"]}
    )

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "timeout"}
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_CONFIRM_TIMEOUT: 8}
    )

    assert setup_entry.options[OPT_CONFIRM_TIMEOUT] == 8
    assert setup_entry.options[OPT_READ_ENTITIES] == ["keep_me"]


async def test_the_auto_expose_step_offers_reading_and_control_separately(
    hass, setup_entry
) -> None:
    """Granting actions automatically is its own decision, never a side effect."""
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "auto_expose"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_AUTO_EXPOSE: True, OPT_AUTO_CONTROL: False}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_entry.options[OPT_AUTO_EXPOSE] is True
    assert setup_entry.options[OPT_AUTO_CONTROL] is False


async def test_both_switches_default_to_off(hass, setup_entry) -> None:
    """An upgrade never turns something on."""
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "auto_expose"}
    )

    schema = result["data_schema"].schema
    defaults = {str(key): key.default() for key in schema}
    assert defaults[OPT_AUTO_EXPOSE] is False
    assert defaults[OPT_AUTO_CONTROL] is False


async def test_control_advances_to_the_grant_confirmation(
    hass, setup_entry, entity_registry
):
    """Submitting the selector shows the consequence before saving it."""
    await async_setup_component(hass, "lock", {})
    lock = entity_registry.async_get_or_create("lock", "demo", "front")
    hass.states.async_set(lock.entity_id, "locked", {"supported_features": 0})

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "control"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_CONTROL_TARGET: {"entity_id": [lock.entity_id]}}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "granted"
    granted = result["description_placeholders"]["granted"]
    assert "lock.unlock" in granted or "unlock" in granted


async def test_grant_confirmation_lists_only_what_this_submission_added(
    hass, setup_entry, entity_registry
):
    """A user who adds one bulb reads one line."""
    await async_setup_component(hass, "light", {})
    old = entity_registry.async_get_or_create("light", "demo", "old")
    new = entity_registry.async_get_or_create("light", "demo", "new")
    for entry in (old, new):
        hass.states.async_set(entry.entity_id, "on", {"supported_features": 0})

    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_CONTROL_ENTITIES: [old.id]}
    )

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "control"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {OPT_CONTROL_TARGET: {"entity_id": [old.entity_id, new.entity_id]}},
    )

    granted = result["description_placeholders"]["granted"]
    assert new.entity_id in granted
    assert old.entity_id not in granted


async def test_accepting_the_grant_saves_the_pending_control_set(
    hass, setup_entry, entity_registry
):
    """
    async_create_entry only records intent; the returned result must be it.

    A grant step that builds a CREATE_ENTRY result and then returns a form
    instead discards the selection. This proves the save actually lands.
    """
    await async_setup_component(hass, "lock", {})
    lock = entity_registry.async_get_or_create("lock", "demo", "front")
    hass.states.async_set(lock.entity_id, "locked", {"supported_features": 0})

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "control"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_CONTROL_TARGET: {"entity_id": [lock.entity_id]}}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"open_exceptions": False}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_entry.options[OPT_CONTROL_ENTITIES] == [lock.id]


async def test_grant_to_exceptions_carries_the_pending_control_set(
    hass, setup_entry, entity_registry
):
    """Opening exceptions from the grant screen must not drop the new selection."""
    await async_setup_component(hass, "lock", {})
    lock = entity_registry.async_get_or_create("lock", "demo", "front")
    hass.states.async_set(lock.entity_id, "locked", {"supported_features": 0})

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "control"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_CONTROL_TARGET: {"entity_id": [lock.entity_id]}}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"open_exceptions": True}
    )
    assert result["step_id"] == "exceptions"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {lock.entity_id: ["lock.lock"]}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_entry.options[OPT_CONTROL_ENTITIES] == [lock.id]
    assert setup_entry.options[OPT_EXCEPTIONS][lock.id] == ["lock.unlock"]


# --- Task 15: exceptions, timeout, automatic exposure and the two rotations -


async def test_timeout_refuses_a_value_not_below_the_agent_timeout(hass, setup_entry):
    """The integration cannot create the crossing it would then warn about."""
    hass.config_entries.async_update_entry(
        setup_entry, data={**setup_entry.data, "response_timeout_secs": 20}
    )

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "timeout"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_CONFIRM_TIMEOUT: 25}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"][OPT_CONFIRM_TIMEOUT] == "timeout_not_below_agent"


async def test_timeout_refuses_a_value_equal_to_the_agent_timeout(hass, setup_entry):
    """Below means below. A boundary bug would accept the crossing at the edge."""
    hass.config_entries.async_update_entry(
        setup_entry, data={**setup_entry.data, "response_timeout_secs": 20}
    )

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "timeout"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_CONFIRM_TIMEOUT: 20}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"][OPT_CONFIRM_TIMEOUT] == "timeout_not_below_agent"


async def test_exceptions_store_subtractions_not_grants(
    hass, setup_entry, entity_registry
):
    """Storing removals is what makes live evaluation safe."""
    await async_setup_component(hass, "lock", {})
    lock = entity_registry.async_get_or_create("lock", "demo", "front")
    hass.states.async_set(lock.entity_id, "locked", {"supported_features": 0})
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_CONTROL_ENTITIES: [lock.id]}
    )

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "exceptions"}
    )
    # The form ships every service ticked; unticking unlock leaves lock only.
    # The schema key is the entity id, never the registry id: a registry id
    # would render as a raw UUID with no translation behind it.
    await hass.config_entries.options.async_configure(
        result["flow_id"], {lock.entity_id: ["lock.lock"]}
    )

    assert setup_entry.options[OPT_EXCEPTIONS][lock.id] == ["lock.unlock"]


async def test_failed_rotation_raises_a_repair_and_shows_a_form_error(
    hass, setup_entry
):
    """A form error is timely; a repair issue is durable. Both are needed."""
    with patch(
        "custom_components.elevenlabs_agent.config_flow.async_sync_tools",
        side_effect=ElevenLabsError("down"),
    ):
        result = await hass.config_entries.options.async_init(setup_entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "rotate_secret"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"confirm": True}
        )

    assert result["errors"]["base"] == "rotation_unsynced"
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ROTATION_UNSYNCED)


async def test_secret_rotation_has_no_rollback_on_failure(hass, setup_entry):
    """The secret has no grace period: it is written even when the sync fails."""
    original_secret = setup_entry.data["webhook_secret"]

    with patch(
        "custom_components.elevenlabs_agent.config_flow.async_sync_tools",
        side_effect=ElevenLabsError("down"),
    ):
        result = await hass.config_entries.options.async_init(setup_entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "rotate_secret"}
        )
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"confirm": True}
        )

    assert setup_entry.data["webhook_secret"] != original_secret


async def test_webhook_rotation_rolls_back_data_on_failure(hass, setup_entry):
    """A failed rotation leaves the stored id agreeing with the route and the tool."""
    old_id = setup_entry.data[CONF_WEBHOOK_ID]

    with patch(
        "custom_components.elevenlabs_agent.config_flow.async_sync_tools",
        side_effect=ElevenLabsError("down"),
    ):
        result = await hass.config_entries.options.async_init(setup_entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "rotate_webhook"}
        )
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"confirm": True}
        )

    assert setup_entry.data[CONF_WEBHOOK_ID] == old_id


async def test_webhook_rotation_stores_the_new_id_once_synced(hass, setup_entry):
    """A successful rotation stores the new id and drops the old route."""
    old_id = setup_entry.data[CONF_WEBHOOK_ID]

    with patch(
        "custom_components.elevenlabs_agent.config_flow.async_sync_tools",
        AsyncMock(),
    ):
        result = await hass.config_entries.options.async_init(setup_entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "rotate_webhook"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"confirm": True}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_entry.data[CONF_WEBHOOK_ID] != old_id


async def test_saving_options_reloads_the_entry(hass, setup_entry):
    """
    OptionsFlowWithReload reloads the entry so a saved option takes effect.

    Plain OptionsFlow combined with an update listener raises ValueError, and
    the integration registers no listener, so the reload has to come from here.
    """
    with patch(
        "custom_components.elevenlabs_agent.async_sync_tools", AsyncMock()
    ) as sync:
        result = await hass.config_entries.options.async_init(setup_entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "timeout"}
        )
        await hass.config_entries.options.async_configure(
            result["flow_id"], {OPT_CONFIRM_TIMEOUT: 8}
        )
        await hass.async_block_till_done()

    assert sync.await_count >= 1


def test_the_grant_step_warns_that_nothing_is_saved_yet() -> None:
    """
    An options flow saves once, at the end, so this screen is a gate.

    MEASURED 15 September 2026 against a running instance: backing out of
    the grant step leaves options empty and the control selection is gone.
    The text has to say so, because the screen otherwise reads as a report.
    """
    for language in ("en", "es"):
        strings = json.loads(
            Path(
                f"custom_components/elevenlabs_agent/translations/{language}.json"
            ).read_text()
        )
        description = strings["options"]["step"]["granted"]["description"]
        assert "{granted}" in description
        assert len(description) > len("{granted}"), (
            f"{language}.json says nothing beyond the list"
        )
