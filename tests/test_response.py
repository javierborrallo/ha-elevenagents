"""Tests for the curated projection and the four write outcomes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.elevenagents.const import (
    ERROR_EXECUTION_FAILED,
    ERROR_INVALID_VALUE,
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
        "climate.living_room",
        "heat",
        {
            "friendly_name": "Living room thermostat",
            "current_temperature": 19.5,
            "temperature": 21.0,
            "hvac_action": "heating",
            "hvac_modes": ["off", "heat"],
            "target_temp_step": 0.5,
        },
    )

    assert project(hass, "climate.living_room") == {
        "entity_id": "climate.living_room",
        "name": "Living room thermostat",
        "area": None,
        "state": "heat",
        "current_temperature": 19.5,
        "temperature": 21.0,
        "hvac_action": "heating",
        "hvac_modes": ["off", "heat"],
        "temperature_unit": "°C",
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
        "entity_id": "light.living_room",
        "name": "Living Room",
        "area": None,
        "state": "on",
    }

    done = write_result(OUTCOME_DONE, "light.living_room", "light.turn_on", projection)

    assert done["outcome"] == OUTCOME_DONE
    assert done["action"] == "light.turn_on"
    assert done["state"] == "on"
    assert "ok" not in done


def test_unconfirmed_carries_no_state_but_steers_to_a_read():
    """The call never returned, so there is nothing trustworthy to send."""
    result = write_result(OUTCOME_UNCONFIRMED, "light.living_room", "light.turn_on")

    assert result["outcome"] == OUTCOME_UNCONFIRMED
    assert result["check_state_next"] is True
    assert "state" not in result


def test_unconfirmed_drops_a_projection_even_when_the_caller_passes_one():
    """A projection reaching write_result for an unconfirmed call is never trusted."""
    projection = {
        "entity_id": "light.living_room",
        "name": "Living Room",
        "area": None,
        "state": "on",
    }

    result = write_result(
        OUTCOME_UNCONFIRMED, "light.living_room", "light.turn_on", projection
    )

    assert result["check_state_next"] is True
    assert "state" not in result
    assert "name" not in result
    assert "area" not in result


def test_no_change_is_not_a_failure():
    """no_change reports that nothing was written, not that nothing worked."""
    projection = {
        "entity_id": "light.living_room",
        "name": "Living Room",
        "area": None,
        "state": "off",
    }

    result = write_result(
        OUTCOME_NO_CHANGE, "light.living_room", "light.turn_off", projection
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


def test_a_partial_inclusion_group_becomes_invalid_value_with_its_detail():
    """A setpoint sent without its pair is not an unknown field."""
    exc = vol.Invalid(
        "some but not all values in the same group of inclusion 'temperature'",
        path=["<temperature>"],
    )

    code, extra = error_fields(exc)

    assert code == ERROR_INVALID_VALUE
    assert extra == {
        "detail": "some but not all values in the same group of inclusion 'temperature'"
    }


def test_a_bad_value_becomes_invalid_value_with_its_field_and_detail():
    """A value the schema refuses names its field and the reason."""
    exc = vol.Invalid("expected float", path=["value"])

    code, extra = error_fields(exc)

    assert code == ERROR_INVALID_VALUE
    assert extra == {"field": "value", "detail": "expected float"}


def test_home_assistant_error_becomes_execution_failed():
    """Something broke and there is nothing for the agent to correct."""
    code, extra = error_fields(HomeAssistantError("boom"))

    assert code == ERROR_EXECUTION_FAILED
    assert extra == {}


def test_read_results_has_no_top_level_ok():
    """Partly resolved is neither ok nor failed; the payload carries the time."""
    payload = read_results(
        [
            {"entity_id": "light.one", "state": "on"},
            {"entity_id": "lock.front", "error": "not_found"},
        ]
    )

    assert set(payload) == {"results", "now"}
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
        "brightness_pct": 71,
        "color_mode": "rgbw",
        "rgb_color": [255, 128, 128],
    }


async def test_a_light_projects_its_brightness_as_a_percentage(hass):
    """The read uses the 0-100 scale of the brightness_pct write field."""
    for raw, percent in ((255, 100), (128, 50), (1, 1), (0, 0)):
        hass.states.async_set("light.office", "on", {"brightness": raw})
        projected = project(hass, "light.office")
        assert projected["brightness_pct"] == percent
        assert "brightness" not in projected


async def test_an_off_light_projects_no_brightness_percentage(hass):
    """An off light carries brightness None, and the projection keeps None."""
    hass.states.async_set("light.office", "off", {"brightness": None})

    assert project(hass, "light.office")["brightness_pct"] is None


async def test_a_climate_projects_both_setpoints_its_range_and_its_preset(hass):
    """A heat_cool climate holds two setpoints; the agent reads both."""
    hass.states.async_set(
        "climate.ecobee",
        "heat_cool",
        {
            "current_temperature": 23,
            "target_temp_high": 24,
            "target_temp_low": 21,
            "min_temp": 7,
            "max_temp": 35,
            "preset_mode": "home",
            "preset_modes": ["home", "eco", "away"],
        },
    )

    projected = project(hass, "climate.ecobee")

    assert projected["target_temp_high"] == 24
    assert projected["target_temp_low"] == 21
    assert projected["min_temp"] == 7
    assert projected["max_temp"] == 35
    assert projected["preset_mode"] == "home"
    assert projected["preset_modes"] == ["home", "eco", "away"]


async def test_a_climate_projects_the_temperature_unit_of_the_instance(hass):
    """Climate attributes use the unit system of Home Assistant."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    hass.states.async_set("climate.office", "heat", {"temperature": 70})

    assert project(hass, "climate.office")["temperature_unit"] == "°F"


async def test_a_timestamp_sensor_projects_its_state_in_local_time(hass):
    """Home Assistant stores a timestamp in UTC; the agent reads local time."""
    await hass.config.async_set_time_zone("Europe/Madrid")
    hass.states.async_set(
        "sensor.sun_next_rising",
        "2026-10-19T06:12:55+00:00",
        {"device_class": "timestamp"},
    )

    assert project(hass, "sensor.sun_next_rising")["state"] == (
        "2026-10-19T08:12:55+02:00"
    )


async def test_a_datetime_entity_projects_its_state_in_local_time(hass):
    """A datetime entity stores UTC like a timestamp sensor."""
    await hass.config.async_set_time_zone("Europe/Madrid")
    hass.states.async_set("datetime.alarm", "2026-01-15T06:30:00+00:00", {})

    assert project(hass, "datetime.alarm")["state"] == "2026-01-15T07:30:00+01:00"


async def test_a_timestamp_sensor_without_a_time_keeps_its_state(hass):
    """An unknown timestamp is not a time, and it stays as it is."""
    hass.states.async_set(
        "sensor.next_backup", "unknown", {"device_class": "timestamp"}
    )

    assert project(hass, "sensor.next_backup")["state"] == "unknown"


async def test_a_number_projects_its_range_step_and_unit(hass):
    """The agent knows which values set_value accepts and what they mean."""
    for entity_id in ("number.volume", "input_number.water"):
        hass.states.async_set(
            entity_id,
            "42.0",
            {
                "min": 0.0,
                "max": 100.0,
                "step": 1.0,
                "mode": "slider",
                "unit_of_measurement": "%",
            },
        )

        projected = project(hass, entity_id)

        assert projected["min"] == 0.0
        assert projected["max"] == 100.0
        assert projected["step"] == 1.0
        assert projected["unit_of_measurement"] == "%"
        assert "mode" not in projected


async def test_a_media_player_projects_its_volume_and_what_plays(hass):
    """The volume keeps the 0-1 scale of the volume_set write field."""
    hass.states.async_set(
        "media_player.kitchen",
        "playing",
        {
            "volume_level": 0.3,
            "is_volume_muted": False,
            "media_title": "I Wanna Be A Hippy",
            "media_artist": "Technohead",
            "source": "dvd",
            "source_list": ["dvd", "youtube"],
            "media_content_id": "bounzz-1",
        },
    )

    projected = project(hass, "media_player.kitchen")

    assert projected["volume_level"] == 0.3
    assert projected["is_volume_muted"] is False
    assert projected["media_title"] == "I Wanna Be A Hippy"
    assert projected["media_artist"] == "Technohead"
    assert projected["source"] == "dvd"
    assert projected["source_list"] == ["dvd", "youtube"]
    assert "media_content_id" not in projected


async def test_a_cover_projects_its_tilt(hass):
    """set_cover_tilt_position has a matching read."""
    hass.states.async_set(
        "cover.living_room_window",
        "open",
        {"current_position": 70, "current_tilt_position": 50},
    )

    assert project(hass, "cover.living_room_window")["current_tilt_position"] == 50


async def test_a_fan_projects_its_preset_oscillation_and_direction(hass):
    """Each fan write that has a state has a matching read."""
    hass.states.async_set(
        "fan.living_room",
        "on",
        {
            "percentage": 67,
            "preset_mode": "auto",
            "preset_modes": ["auto", "smart", "sleep"],
            "oscillating": True,
            "direction": "forward",
        },
    )

    projected = project(hass, "fan.living_room")

    assert projected["preset_mode"] == "auto"
    assert projected["preset_modes"] == ["auto", "smart", "sleep"]
    assert projected["oscillating"] is True
    assert projected["direction"] == "forward"


@pytest.mark.parametrize(
    ("entity_id", "attributes", "expected"),
    [
        (
            "climate.hvac",
            {
                "fan_mode": "on_high",
                "fan_modes": ["on_low", "on_high"],
                "swing_mode": "off",
                "swing_modes": ["off", "1"],
                "swing_horizontal_mode": "auto",
                "swing_horizontal_modes": ["auto", "off"],
                "humidity": 67,
                "current_humidity": 54,
            },
            None,
        ),
        (
            "humidifier.dehumidifier",
            {
                "humidity": 54,
                "current_humidity": 59,
                "min_humidity": 0,
                "max_humidity": 100,
                "mode": "normal",
                "available_modes": ["normal", "eco"],
            },
            None,
        ),
        (
            "water_heater.boiler",
            {
                "temperature": 59,
                "current_temperature": 55,
                "target_temp_high": None,
                "target_temp_low": None,
                "min_temp": 43,
                "max_temp": 60,
                "operation_mode": "eco",
                "operation_list": ["eco", "electric"],
                "away_mode": "off",
            },
            None,
        ),
        ("valve.kitchen", {"current_position": 30, "device_class": "water"}, None),
        ("vacuum.robot", {"fan_speed": "max", "fan_speed_list": ["min", "max"]}, None),
        ("select.speed", {"options": ["light", "ridiculous_speed"]}, None),
        ("input_select.who", {"options": ["Ana", "Luis"]}, None),
        (
            "light.office",
            {
                "effect": "rainbow",
                "effect_list": ["rainbow", "none"],
                "min_color_temp_kelvin": 2000,
                "max_color_temp_kelvin": 6500,
            },
            None,
        ),
        (
            "media_player.walkman",
            {
                "sound_mode": "Music",
                "sound_mode_list": ["Music", "Movie"],
                "shuffle": False,
                "repeat": "off",
            },
            None,
        ),
        ("text.note", {"min": 0, "max": 100, "pattern": None}, None),
        ("input_text.note", {"min": 0, "max": 100, "pattern": "[a-z]*"}, None),
        ("siren.siren", {"available_tones": ["fire", "alarm"]}, None),
        ("fan.ceiling", {"percentage_step": 33.33}, None),
        (
            "weather.home",
            {
                "temperature": 21.6,
                "apparent_temperature": 22.1,
                "humidity": 92,
                "pressure": 1099,
                "wind_speed": 0.5,
                "wind_bearing": 180,
                "visibility": 10,
                "cloud_coverage": 40,
                "uv_index": 3,
                "dew_point": 15,
                "temperature_unit": "°C",
                "pressure_unit": "hPa",
                "wind_speed_unit": "km/h",
                "visibility_unit": "km",
                "precipitation_unit": "mm",
            },
            None,
        ),
        (
            "calendar.work",
            {
                "message": "Dentist",
                "start_time": "2026-10-19 10:00:00",
                "end_time": "2026-10-19 11:00:00",
                "all_day": False,
                "location": "Calle Mayor 1",
                "description": "Check-up",
            },
            None,
        ),
        (
            "update.core",
            {
                "title": "Home Assistant Core",
                "installed_version": "2026.10.0",
                "latest_version": "2026.10.1",
                "in_progress": False,
            },
            None,
        ),
        ("sensor.wash_cycle", {"options": ["idle", "wash", "rinse"]}, None),
        (
            "alarm_control_panel.home",
            {"code_arm_required": True, "code_format": "number"},
            None,
        ),
        ("event.doorbell", {"event_type": "ring", "event_types": ["ring"]}, None),
        ("counter.cups", {"minimum": 0, "maximum": 10, "step": 1}, None),
        ("timer.pasta", {"duration": "0:10:00", "remaining": "0:10:00"}, None),
        (
            "remote.tv",
            {"activity_list": ["TV", "Music"], "current_activity": "TV"},
            None,
        ),
        ("lock.front", {"code_format": "^\\d{4}$", "changed_by": "Ana"}, None),
        (
            "air_quality.demo",
            {"particulate_matter_2_5": 14, "particulate_matter_10": 5},
            None,
        ),
    ],
)
async def test_each_domain_projects_what_its_actions_and_questions_need(
    hass, entity_id, attributes, expected
):
    """Every listed attribute reaches the agent unchanged."""
    hass.states.async_set(entity_id, "on", attributes)

    projected = project(hass, entity_id)

    for key, value in (expected or attributes).items():
        assert projected[key] == value, key


async def test_a_water_heater_projects_the_temperature_unit_of_the_instance(hass):
    """A water heater uses the unit system of Home Assistant, like a climate."""
    hass.states.async_set("water_heater.boiler", "eco", {"temperature": 59})

    assert project(hass, "water_heater.boiler")["temperature_unit"] == "°C"


@pytest.mark.parametrize("domain", ["event", "button", "scene", "input_button"])
async def test_a_state_that_is_a_utc_time_projects_in_local_time(hass, domain):
    """Several domains store the time of their last use in UTC."""
    await hass.config.async_set_time_zone("Europe/Madrid")
    hass.states.async_set(f"{domain}.one", "2026-10-19T06:12:55.123+00:00", {})

    assert project(hass, f"{domain}.one")["state"] == "2026-10-19T08:12:55+02:00"


async def test_an_attribute_that_is_a_utc_time_projects_in_local_time(hass):
    """The sun publishes its times as UTC attributes."""
    await hass.config.async_set_time_zone("Europe/Madrid")
    hass.states.async_set(
        "sun.sun",
        "below_horizon",
        {
            "next_rising": "2026-10-19T06:12:55.297+00:00",
            "next_setting": "2026-10-19T16:36:59+00:00",
            "rising": True,
        },
    )

    projected = project(hass, "sun.sun")

    assert projected["next_rising"] == "2026-10-19T08:12:55+02:00"
    assert projected["next_setting"] == "2026-10-19T18:36:59+02:00"
    assert projected["rising"] is True


@pytest.mark.parametrize(
    "state", ["42", "2026.10.1", "2026-10-19 10:00:00", "on", "unknown"]
)
async def test_a_state_without_a_time_zone_is_not_converted(hass, state):
    """Only an ISO time with a time zone is a UTC time to convert."""
    await hass.config.async_set_time_zone("Europe/Madrid")
    hass.states.async_set("input_text.note", state, {})

    assert project(hass, "input_text.note")["state"] == state


async def test_a_password_text_projects_no_value(hass):
    """A text in password mode keeps its value from the agent."""
    for entity_id in ("input_text.alarm_pin", "text.wifi"):
        hass.states.async_set(entity_id, "1234", {"mode": "password", "max": 10})

        projected = project(hass, entity_id)

        assert projected["state"] == "<hidden>"
        assert "1234" not in str(projected)


async def test_location_attributes_never_reach_the_agent(hass):
    """A person, a tracker and a zone keep their coordinates in the house."""
    for entity_id in ("person.ana", "device_tracker.phone", "zone.home"):
        hass.states.async_set(
            entity_id, "home", {"latitude": 40.4, "longitude": -3.7, "gps_accuracy": 5}
        )

        projected = project(hass, entity_id)

        assert "latitude" not in projected
        assert "longitude" not in projected


async def test_a_humidifier_projects_its_action_under_its_own_name(hass):
    """The attribute action would collide with the action of a write."""
    hass.states.async_set("humidifier.dry", "on", {"action": "drying"})

    projected = project(hass, "humidifier.dry")

    assert projected["humidifier_action"] == "drying"
    assert "action" not in projected


def test_a_projection_never_replaces_the_fields_of_a_write():
    """outcome, entity_id and action describe the write, whatever the state holds."""
    result = write_result(
        OUTCOME_DONE,
        "humidifier.dry",
        "humidifier.set_humidity",
        {"entity_id": "x", "outcome": "y", "action": "drying", "state": "on"},
    )

    assert result["outcome"] == OUTCOME_DONE
    assert result["entity_id"] == "humidifier.dry"
    assert result["action"] == "humidifier.set_humidity"
    assert result["state"] == "on"


@pytest.mark.parametrize(
    ("entity_id", "attribute"),
    [
        ("timer.pasta", "finishes_at"),
        ("schedule.heating", "next_event"),
        ("script.goodnight", "last_triggered"),
        ("automation.lights", "last_triggered"),
    ],
)
async def test_a_utc_time_attribute_of_a_helper_projects_in_local_time(
    hass, entity_id, attribute
):
    """A timer, a schedule, a script and an automation publish UTC times."""
    await hass.config.async_set_time_zone("Europe/Madrid")
    hass.states.async_set(entity_id, "on", {attribute: "2026-10-19T06:30:00+00:00"})

    assert project(hass, entity_id)[attribute] == "2026-10-19T08:30:00+02:00"


async def test_a_group_projects_no_members(hass):
    """The members of a group can name entities the user did not select."""
    hass.states.async_set(
        "group.downstairs", "on", {"entity_id": ["light.a", "lock.front_door"]}
    )

    assert "entity_id" not in str(
        {k: v for k, v in project(hass, "group.downstairs").items() if k != "entity_id"}
    )


async def test_an_active_timer_projects_the_time_left_now(hass, freezer):
    """Home Assistant updates remaining on pause only; finishes_at is the truth."""
    freezer.move_to("2026-10-19T08:00:00+00:00")
    hass.states.async_set(
        "timer.pasta",
        "active",
        {
            "duration": "0:10:00",
            "remaining": "0:10:00",
            "finishes_at": "2026-10-19T08:06:30+00:00",
        },
    )

    assert project(hass, "timer.pasta")["remaining"] == "0:06:30"


async def test_a_paused_timer_keeps_its_remaining(hass, freezer):
    """A paused timer has no finishes_at, and remaining is current."""
    freezer.move_to("2026-10-19T08:00:00+00:00")
    hass.states.async_set(
        "timer.pasta", "paused", {"duration": "0:10:00", "remaining": "0:04:12"}
    )

    assert project(hass, "timer.pasta")["remaining"] == "0:04:12"


async def test_a_datetime_attribute_projects_as_local_text(hass):
    """A script stores last_triggered as a datetime object, not as text."""
    await hass.config.async_set_time_zone("Europe/Madrid")
    moment = datetime(2026, 10, 19, 6, 30, tzinfo=UTC)
    hass.states.async_set("script.goodnight", "off", {"last_triggered": moment})

    assert project(hass, "script.goodnight")["last_triggered"] == (
        "2026-10-19T08:30:00+02:00"
    )


def test_a_value_error_becomes_invalid_value_with_its_message():
    """A counter refuses a value over its maximum with a ValueError."""
    exc = ValueError("Value 12 for counter.cups exceeding the maximum value of 10")

    code, extra = error_fields(exc)

    assert code == ERROR_INVALID_VALUE
    assert extra == {
        "detail": "Value 12 for counter.cups exceeding the maximum value of 10"
    }
