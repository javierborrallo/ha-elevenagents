"""Constants for the ElevenAgents integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "elevenagents"

# entry.data holds identity and secrets. Reauth and rotation write it.
# Every write merges into the existing data.
CONF_API_KEY: Final = "api_key"
CONF_AGENT_ID: Final = "agent_id"
CONF_WEBHOOK_ID: Final = "webhook_id"
CONF_WEBHOOK_SECRET: Final = "webhook_secret"  # noqa: S105 - a key name, not a value
CONF_TOOL_IDS: Final = "tool_ids"
CONF_RESPONSE_TIMEOUT: Final = "response_timeout_secs"

# entry.options holds what the user edits. Each options step writes
# {**entry.options, **its own keys}.
OPT_READ_TARGET: Final = "read_target"
OPT_CONTROL_TARGET: Final = "control_target"
OPT_READ_ENTITIES: Final = "read_entities"
OPT_CONTROL_ENTITIES: Final = "control_entities"
OPT_EXCEPTIONS: Final = "exceptions"
OPT_CONFIRM_TIMEOUT: Final = "confirm_timeout"
OPT_AUTO_EXPOSE: Final = "auto_expose"
OPT_AUTO_CONTROL: Final = "auto_control"
OPT_AUTO_EXPOSE_AREAS: Final = "auto_expose_areas"
OPT_AGENT_SETTINGS: Final = "agent_settings"

DEFAULT_CONFIRM_TIMEOUT: Final = 5
DEFAULT_RESPONSE_TIMEOUT: Final = 20
AUTO_EXPOSE_DEBOUNCE: Final = 10

# Options the events block writes. Minutes, except the hourly limit.
OPT_EVENT_COOLDOWN: Final = "event_cooldown_minutes"
OPT_EVENT_HOURLY_LIMIT: Final = "event_hourly_limit"
OPT_EVENT_CONTINUITY: Final = "event_continuity_minutes"
DEFAULT_EVENT_COOLDOWN: Final = 5
DEFAULT_EVENT_HOURLY_LIMIT: Final = 20
DEFAULT_EVENT_CONTINUITY: Final = 30
MAX_EVENT_MESSAGE: Final = 1000
MAX_EVENT_DATA_BYTES: Final = 4096
MAX_EVENT_MINUTES: Final = 1440
MAX_EVENT_HOURLY_LIMIT: Final = 1000

# Seconds. A turn ends this long after the last message, once the agent answered.
TURN_IDLE_SECONDS: Final = 3.0
TURN_MAX_SECONDS: Final = 60.0

EVENT_REPLY: Final = "elevenagents_reply"
EVENT_ACTION: Final = "elevenagents_action"
EVENT_DROPPED: Final = "elevenagents_event_dropped"
EVENT_FAILED: Final = "elevenagents_event_failed"

# Seconds between the last sync of a burst and the connection check.
CHECK_DEBOUNCE: Final = 10
SIGNAL_CHECK: Final = "elevenagents_check_{}"

# Transport limits. The handler returns 401, 413, 415 and 429.
# Every other outcome is HTTP 200 with a structured body.
SECRET_HEADER: Final = "X-Elevenlabs-Agent-Secret"  # noqa: S105 - a header name
MAX_BODY_BYTES: Final = 256 * 1024
RATE_LIMIT_BURST: Final = 10
RATE_LIMIT_PER_SECOND: Final = 1.0

TOOL_GET_STATE: Final = "ha_get_state"
TOOL_CALL_SERVICE: Final = "ha_call_service"

# Discriminator values the tool definitions send.
DISCRIMINATOR_GET_STATE: Final = "get_state"
DISCRIMINATOR_CALL_SERVICE: Final = "call_service"

OUTCOME_DONE: Final = "done"
OUTCOME_NO_CHANGE: Final = "no_change"
OUTCOME_UNCONFIRMED: Final = "unconfirmed"
OUTCOME_FAILED: Final = "failed"
OUTCOME_CONFIRMATION_REQUIRED: Final = "confirmation_required"

# Error codes this integration sends. A Home Assistant error passes
# through under its own translation_key.
ERROR_NOT_FOUND: Final = "not_found"
ERROR_ACTION_NOT_ALLOWED: Final = "action_not_allowed"
ERROR_UNKNOWN_FIELD: Final = "unknown_field"
ERROR_INVALID_VALUE: Final = "invalid_value"
ERROR_EXECUTION_FAILED: Final = "execution_failed"
ERROR_MALFORMED_REQUEST: Final = "malformed_request"

# Services that write a file. The agent never gets them.
FILE_WRITING_SERVICES: Final = frozenset(
    {"camera.snapshot", "camera.record", "image.snapshot"}
)

# Options the sensitive block writes.
# permission key -> {"confirm": bool, "refuse_automatic": bool}.
OPT_SENSITIVE: Final = "sensitive"
REASON_NOT_ALLOWED_WITHOUT_A_PERSON: Final = "not_allowed_without_a_person"
REASON_CONFIRMATION_EXPIRED: Final = "confirmation_expired"
REASON_NO_CONFIRMATION_HEARD: Final = "no_confirmation_heard"
REASON_CONVERSATION_UNKNOWN: Final = "conversation_unknown"
# Domains whose devices write their state when they start to move.
MOVING_DEVICE_DOMAINS: Final = frozenset({"cover", "valve"})
# Seconds a write to a moving device waits for a late state write.
MOVING_DEVICE_WAIT_SECONDS: Final = 2.0
# Seconds a confirmation code stays valid.
CONFIRMATION_WINDOW_SECONDS: Final = 60.0
# Seconds an event conversation id stays recognised after its turn ends.
EVENT_CONVERSATION_GRACE_SECONDS: Final = 300.0

# Attributes added to the projection per domain. A domain absent from
# this mapping returns entity_id, name, area and state only.
PROJECTION_ATTRIBUTES: Final[dict[str, tuple[str, ...]]] = {
    "climate": (
        "current_temperature",
        "temperature",
        "target_temp_high",
        "target_temp_low",
        "min_temp",
        "max_temp",
        "hvac_action",
        "hvac_modes",
        "preset_mode",
        "preset_modes",
        "fan_mode",
        "fan_modes",
        "swing_mode",
        "swing_modes",
        "swing_horizontal_mode",
        "swing_horizontal_modes",
        "humidity",
        "current_humidity",
    ),
    "humidifier": (
        "humidity",
        "current_humidity",
        "min_humidity",
        "max_humidity",
        "mode",
        "available_modes",
        "action",
    ),
    "water_heater": (
        "temperature",
        "current_temperature",
        "target_temp_high",
        "target_temp_low",
        "min_temp",
        "max_temp",
        "operation_mode",
        "operation_list",
        "away_mode",
    ),
    "light": (
        "brightness",
        "color_mode",
        "rgb_color",
        "color_temp_kelvin",
        "min_color_temp_kelvin",
        "max_color_temp_kelvin",
        "effect",
        "effect_list",
    ),
    "cover": ("current_position", "current_tilt_position", "device_class"),
    "valve": ("current_position", "device_class"),
    "fan": (
        "percentage",
        "percentage_step",
        "preset_mode",
        "preset_modes",
        "oscillating",
        "direction",
    ),
    "vacuum": ("fan_speed", "fan_speed_list"),
    "sensor": ("unit_of_measurement", "device_class", "options"),
    "binary_sensor": ("device_class",),
    "number": ("min", "max", "step", "unit_of_measurement"),
    "input_number": ("min", "max", "step", "unit_of_measurement"),
    "select": ("options",),
    "input_select": ("options",),
    "text": ("min", "max", "pattern"),
    "input_text": ("min", "max", "pattern"),
    "media_player": (
        "volume_level",
        "is_volume_muted",
        "media_title",
        "media_artist",
        "source",
        "source_list",
        "sound_mode",
        "sound_mode_list",
        "shuffle",
        "repeat",
    ),
    "siren": ("available_tones",),
    "weather": (
        "temperature",
        "apparent_temperature",
        "dew_point",
        "humidity",
        "pressure",
        "wind_speed",
        "wind_bearing",
        "visibility",
        "cloud_coverage",
        "uv_index",
        "temperature_unit",
        "pressure_unit",
        "wind_speed_unit",
        "visibility_unit",
        "precipitation_unit",
    ),
    "calendar": (
        "message",
        "start_time",
        "end_time",
        "all_day",
        "location",
        "description",
    ),
    "update": ("title", "installed_version", "latest_version", "in_progress"),
    "alarm_control_panel": ("code_arm_required", "code_format"),
    "event": ("event_type", "event_types"),
    "sun": ("next_rising", "next_setting", "next_dawn", "next_dusk", "rising"),
    "counter": ("minimum", "maximum", "step"),
    "timer": ("duration", "remaining", "finishes_at"),
    "schedule": ("next_event",),
    "script": ("last_triggered",),
    "automation": ("last_triggered",),
    "remote": ("activity_list", "current_activity"),
    "lock": ("code_format", "changed_by"),
    "air_quality": (
        "particulate_matter_2_5",
        "particulate_matter_10",
        "air_quality_index",
        "carbon_dioxide",
        "nitrogen_oxide",
    ),
}
# Domains whose temperatures use the unit system of the instance.
INSTANCE_TEMPERATURE_DOMAINS: Final = frozenset({"climate", "water_heater"})

ISSUE_AGENT_UNPROTECTED: Final = "agent_unprotected"
ISSUE_TIMEOUT_CROSSED: Final = "timeout_crossed"
ISSUE_ROTATION_UNSYNCED: Final = "rotation_unsynced"
ISSUE_MISSING_ENTITIES: Final = "missing_entities"

PANEL_URL_PATH: Final = "elevenagents"
