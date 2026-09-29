"""Constants for the ElevenAgents integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "elevenlabs_agent"

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

EVENT_REPLY: Final = "elevenlabs_agent_reply"
EVENT_DROPPED: Final = "elevenlabs_agent_event_dropped"
EVENT_FAILED: Final = "elevenlabs_agent_event_failed"

# Seconds between the last sync of a burst and the connection check.
CHECK_DEBOUNCE: Final = 10
SIGNAL_CHECK: Final = "elevenlabs_agent_check_{}"

# Transport limits. The handler returns 401, 413, 415 and 429.
# Every other outcome is HTTP 200 with a structured body.
SECRET_HEADER: Final = "X-Elevenlabs-Agent-Secret"  # noqa: S105 - a header name
MAX_BODY_BYTES: Final = 64 * 1024
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

# Error codes this integration sends. A Home Assistant error passes
# through under its own translation_key.
ERROR_NOT_FOUND: Final = "not_found"
ERROR_ACTION_NOT_ALLOWED: Final = "action_not_allowed"
ERROR_UNKNOWN_FIELD: Final = "unknown_field"
ERROR_EXECUTION_FAILED: Final = "execution_failed"
ERROR_MALFORMED_REQUEST: Final = "malformed_request"

# Attributes added to the projection per domain. A domain absent from
# this mapping returns entity_id, name, area and state only.
PROJECTION_ATTRIBUTES: Final[dict[str, tuple[str, ...]]] = {
    "climate": ("current_temperature", "temperature", "hvac_action"),
    "light": ("brightness", "color_mode", "rgb_color", "color_temp_kelvin"),
    "cover": ("current_position", "device_class"),
    "fan": ("percentage",),
    "sensor": ("unit_of_measurement", "device_class"),
    "binary_sensor": ("device_class",),
}

ISSUE_AGENT_UNPROTECTED: Final = "agent_unprotected"
ISSUE_TIMEOUT_CROSSED: Final = "timeout_crossed"
ISSUE_ROTATION_UNSYNCED: Final = "rotation_unsynced"
ISSUE_MISSING_ENTITIES: Final = "missing_entities"

PANEL_URL_PATH: Final = "elevenlabs-agent"
