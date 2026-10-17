"""Tests for the curated projection and the four write outcomes."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.elevenagents.const import (
    ERROR_EXECUTION_FAILED,
    ERROR_UNKNOWN_FIELD,
    OUTCOME_DONE,
    OUTCOME_NO_CHANGE,
    OUTCOME_UNCONFIRMED,
)
from custom_components.elevenagents.response import (
    error_fields,
    malformed,
    project,
    read_results,
    write_result,
)


async def test_projection_is_flat_and_drops_unlisted_attributes(hass):
    """Listed attributes sit beside state; everything else is dropped."""
    hass.states.async_set(
        "climate.salon",
        "heat",
        {
            "friendly_name": "Termostato salon",
            "current_temperature": 19.5,
            "temperature": 21.0,
            "hvac_action": "heating",
            "hvac_modes": ["off", "heat"],
            "min_temp": 7,
        },
    )

    assert project(hass, "climate.salon") == {
        "entity_id": "climate.salon",
        "name": "Termostato salon",
        "area": None,
        "state": "heat",
        "current_temperature": 19.5,
        "temperature": 21.0,
        "hvac_action": "heating",
    }


async def test_unlisted_domain_returns_the_four_base_fields(hass):
    """A domain absent from the table has an answer, not an error."""
    hass.states.async_set("lock.front", "locked", {"friendly_name": "Front"})

    assert project(hass, "lock.front") == {
        "entity_id": "lock.front",
        "name": "Front",
        "area": None,
        "state": "locked",
    }


async def test_area_is_null_not_missing(hass):
    """An entity with no area sends an explicit null."""
    hass.states.async_set("light.one", "on", {})

    projected = project(hass, "light.one")

    assert "area" in projected
    assert projected["area"] is None


async def test_assumed_state_travels_only_when_true(hass):
    """The hedge flag is present when Home Assistant sets it."""
    hass.states.async_set("switch.a", "on", {"assumed_state": True})
    hass.states.async_set("switch.b", "on", {"assumed_state": False})

    assert project(hass, "switch.a")["assumed_state"] is True
    assert "assumed_state" not in project(hass, "switch.b")


async def test_missing_entity_projects_to_nothing(hass):
    """A caller distinguishes an absent entity by an empty projection."""
    assert project(hass, "light.gone") == {}


def test_done_and_no_change_carry_the_projection():
    """Both outcomes that observed a state send it."""
    projection = {
        "entity_id": "light.salon",
        "name": "Salon",
        "area": None,
        "state": "on",
    }

    done = write_result(OUTCOME_DONE, "light.salon", "light.turn_on", projection)

    assert done["outcome"] == OUTCOME_DONE
    assert done["action"] == "light.turn_on"
    assert done["state"] == "on"
    assert "ok" not in done


def test_unconfirmed_carries_no_state_but_steers_to_a_read():
    """The call never returned, so there is nothing trustworthy to send."""
    result = write_result(OUTCOME_UNCONFIRMED, "light.salon", "light.turn_on")

    assert result["outcome"] == OUTCOME_UNCONFIRMED
    assert result["check_state_next"] is True
    assert "state" not in result


def test_unconfirmed_drops_a_projection_even_when_the_caller_passes_one():
    """A projection reaching write_result for an unconfirmed call is never trusted."""
    projection = {
        "entity_id": "light.salon",
        "name": "Salon",
        "area": None,
        "state": "on",
    }

    result = write_result(
        OUTCOME_UNCONFIRMED, "light.salon", "light.turn_on", projection
    )

    assert result["check_state_next"] is True
    assert "state" not in result
    assert "name" not in result
    assert "area" not in result


def test_no_change_is_not_a_failure():
    """no_change reports that nothing was written, not that nothing worked."""
    projection = {
        "entity_id": "light.salon",
        "name": "Salon",
        "area": None,
        "state": "off",
    }

    result = write_result(
        OUTCOME_NO_CHANGE, "light.salon", "light.turn_off", projection
    )

    assert result["outcome"] == OUTCOME_NO_CHANGE
    assert result["state"] == "off"
    assert "error" not in result


def test_service_validation_error_passes_through_verbatim():
    """The translation_key becomes the code and placeholders become fields."""
    exc = ServiceValidationError(
        translation_domain="climate",
        translation_key="temp_out_of_range",
        translation_placeholders={
            "check_temp": "45.0",
            "min_temp": "7.0",
            "max_temp": "35.0",
        },
    )

    code, extra = error_fields(exc)

    assert code == "temp_out_of_range"
    assert extra == {"check_temp": "45.0", "min_temp": "7.0", "max_temp": "35.0"}


def test_voluptuous_error_becomes_unknown_field_with_no_placeholders():
    """A schema failure has no translation_key to relay."""
    exc = vol.Invalid("extra keys not allowed", path=["brightness_pct"])

    code, extra = error_fields(exc)

    assert code == ERROR_UNKNOWN_FIELD
    assert extra == {"field": "brightness_pct"}


def test_home_assistant_error_becomes_execution_failed():
    """Something broke and there is nothing for the agent to correct."""
    code, extra = error_fields(HomeAssistantError("boom"))

    assert code == ERROR_EXECUTION_FAILED
    assert extra == {}


def test_read_results_has_no_top_level_ok():
    """A call that resolved some entities and not others is neither."""
    payload = read_results(
        [
            {"entity_id": "light.one", "state": "on"},
            {"entity_id": "lock.front", "error": "not_found"},
        ]
    )

    assert set(payload) == {"results"}
    assert len(payload["results"]) == 2


def test_malformed_is_two_fields():
    """The call-level shape carries no outcome and no results."""
    assert malformed("unknown tool discriminator") == {
        "error": "malformed_request",
        "detail": "unknown tool discriminator",
    }


async def test_a_light_projects_its_colour_mode_and_colour(hass):
    """The model sees the colour a light holds, not only its brightness."""
    hass.states.async_set(
        "light.office",
        "on",
        {
            "friendly_name": "Office",
            "brightness": 180,
            "color_mode": "rgbw",
            "rgb_color": [255, 128, 128],
            "rgbw_color": [255, 0, 0, 255],
            "supported_color_modes": ["rgbw"],
        },
    )

    assert project(hass, "light.office") == {
        "entity_id": "light.office",
        "name": "Office",
        "area": None,
        "state": "on",
        "brightness": 180,
        "color_mode": "rgbw",
        "rgb_color": [255, 128, 128],
    }
