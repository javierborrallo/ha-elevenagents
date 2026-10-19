"""WebSocket commands the configuration panel calls."""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import webhook, websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.service import async_get_all_descriptions

from .const import (
    CONF_AGENT_ID,
    CONF_RESPONSE_TIMEOUT,
    CONF_TOOL_IDS,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DEFAULT_CONFIRM_TIMEOUT,
    DEFAULT_EVENT_CONTINUITY,
    DEFAULT_EVENT_COOLDOWN,
    DEFAULT_EVENT_HOURLY_LIMIT,
    DEFAULT_RESPONSE_TIMEOUT,
    DOMAIN,
    ISSUE_ROTATION_UNSYNCED,
    MAX_EVENT_HOURLY_LIMIT,
    MAX_EVENT_MINUTES,
    OPT_AGENT_SETTINGS,
    OPT_AUTO_CONTROL,
    OPT_AUTO_EXPOSE,
    OPT_AUTO_EXPOSE_AREAS,
    OPT_CONFIRM_TIMEOUT,
    OPT_CONTROL_ENTITIES,
    OPT_EVENT_CONTINUITY,
    OPT_EVENT_COOLDOWN,
    OPT_EVENT_HOURLY_LIMIT,
    OPT_EXCEPTIONS,
    OPT_PUBLIC_URL,
    OPT_READ_ENTITIES,
    OPT_SENSITIVE,
    TOOL_GET_STATE,
)
from .elevenlabs_client import (
    ElevenLabsError,
    async_get_agent,
    async_get_conversation_token,
    async_get_tool,
    async_patch_agent,
)
from .permissions import (
    Permissions,
    entity_id_for,
    granted_services,
    is_missing,
    resolve_target,
    stored_keys,
    without_keys,
)
from .public_url import normalize_public_url
from .repairs import async_check_missing
from .sensitive import Mark
from .status import async_record_check
from .tool_sync import AGENT_SETTING_DEFAULTS, ToolSyncError, async_sync_tools
from .webhook import WEBHOOK_NAME, async_handle_webhook

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from homeassistant.config_entries import ConfigEntry

    from . import ElevenAgentsConfigEntry

_LOGGER = logging.getLogger(__name__)


_silence_timeout = vol.Any(
    -1, vol.All(vol.Any(int, float), vol.Range(min=0, min_included=False))
)


def _entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry | None:
    """Return this integration's entry with that id, or None."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        return None
    return entry


def _sole_entry(hass: HomeAssistant) -> tuple[ConfigEntry | None, str]:
    """
    Return the only entry of this domain, for a caller that names none.

    The manifest declares single_config_entry, so Home Assistant allows
    exactly one. Any other count breaks that assumption, and this reports
    it instead of choosing.
    """
    entries = hass.config_entries.async_entries(DOMAIN)
    if len(entries) == 1:
        return entries[0], ""
    if not entries:
        return None, "No config entry"
    return None, "More than one config entry"


def _entity_ids(hass: HomeAssistant, keys: Iterable[str]) -> list[str]:
    """Return the entity_id of each key that still exists, sorted."""
    resolved = (entity_id_for(hass, key) for key in keys)
    return sorted(entity_id for entity_id in resolved if entity_id is not None)


def _exception_entity_ids(
    hass: HomeAssistant, exceptions: dict[str, Any]
) -> dict[str, list[str]]:
    """Key the exceptions by entity_id instead of by permission key."""
    result: dict[str, list[str]] = {}
    for key, services in exceptions.items():
        entity_id = entity_id_for(hass, key)
        if entity_id is not None:
            result[entity_id] = sorted(services)
    return result


async def _available_actions(
    hass: HomeAssistant, control: Iterable[str]
) -> dict[str, list[str]]:
    """
    Map each controlled entity_id to every service its domain grants it.

    Exceptions play no part here: this is the full list an entity's type
    accepts, not what remains after subtraction, so a removed action still
    appears and can be granted back.
    """
    descriptions = await async_get_all_descriptions(hass)
    result: dict[str, list[str]] = {}
    for key in control:
        entity_id = entity_id_for(hass, key)
        if entity_id is None:
            continue
        perms = Permissions(read=frozenset(), control=frozenset((key,)), exceptions={})
        result[entity_id] = sorted(granted_services(hass, perms, key, descriptions))
    return result


def _missing(hass: HomeAssistant, options: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Describe each stored key whose entity does not exist now."""
    read = set(options.get(OPT_READ_ENTITIES, ()))
    control = set(options.get(OPT_CONTROL_ENTITIES, ()))
    restricted = set(options.get(OPT_EXCEPTIONS, {}))
    sensitive = set(options.get(OPT_SENSITIVE, {}))
    return [
        {
            "key": key,
            "entity_id": key if "." in key else None,
            "read": key in read,
            "control": key in control,
            "restricted": key in restricted,
            "sensitive": key in sensitive,
        }
        for key in sorted(stored_keys(options))
        if is_missing(hass, key)
    ]


def _unregistered(hass: HomeAssistant) -> list[str]:
    """Return every entity_id that holds a state and has no registry entry."""
    registry = er.async_get(hass)
    return sorted(
        entity_id
        for entity_id in hass.states.async_entity_ids()
        if registry.async_get(entity_id) is None
    )


def _unsynced(entry: ElevenAgentsConfigEntry) -> list[str]:
    """Return the entity_ids the last check found missing on the agent."""
    runtime = getattr(entry, "runtime_data", None)
    result = None if runtime is None else runtime.last_check
    if result is None:
        return []
    for item in result["checks"]:
        if item["check"] == "tools_current" and isinstance(item["detail"], dict):
            return list(item["detail"]["missing"])
    return []


@websocket_api.websocket_command(
    {
        vol.Required("type"): "elevenagents/config/get",
        vol.Optional("entry_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def config_get(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """
    Return the stored configuration. This never reaches the network.

    A caller that names no entry gets the only one. That is how the panel
    opens from the link the config flow leaves behind, where the entry id
    does not exist yet.
    """
    if "entry_id" in msg:
        entry = _entry(hass, msg["entry_id"])
        detail = "Unknown config entry"
    else:
        entry, detail = _sole_entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_found", detail)
        return

    options = entry.options
    read = sorted(options.get(OPT_READ_ENTITIES, ()))
    control = sorted(options.get(OPT_CONTROL_ENTITIES, ()))
    exceptions = dict(options.get(OPT_EXCEPTIONS, {}))

    connection.send_result(
        msg["id"],
        {
            "entry_id": entry.entry_id,
            "read_entities": read,
            "control_entities": control,
            "read_entity_ids": _entity_ids(hass, read),
            "control_entity_ids": _entity_ids(hass, control),
            "exceptions": exceptions,
            "exception_entity_ids": _exception_entity_ids(hass, exceptions),
            "sensitive_entity_ids": _sensitive_entity_ids(
                hass, dict(options.get(OPT_SENSITIVE, {}))
            ),
            "available_actions": await _available_actions(hass, control),
            "missing": _missing(hass, options),
            "unregistered_entity_ids": _unregistered(hass),
            "unsynced_entity_ids": _unsynced(entry),
            "confirm_timeout": options.get(
                OPT_CONFIRM_TIMEOUT, DEFAULT_CONFIRM_TIMEOUT
            ),
            "events": {
                "cooldown_minutes": options.get(
                    OPT_EVENT_COOLDOWN, DEFAULT_EVENT_COOLDOWN
                ),
                "hourly_limit": options.get(
                    OPT_EVENT_HOURLY_LIMIT, DEFAULT_EVENT_HOURLY_LIMIT
                ),
                "continuity_minutes": options.get(
                    OPT_EVENT_CONTINUITY, DEFAULT_EVENT_CONTINUITY
                ),
            },
            "auto_expose": options.get(OPT_AUTO_EXPOSE, False),
            "auto_control": options.get(OPT_AUTO_CONTROL, False),
            "auto_expose_areas": sorted(options.get(OPT_AUTO_EXPOSE_AREAS, ())),
            "agent_settings": dict(options.get(OPT_AGENT_SETTINGS, {})),
            "public_url": options.get(OPT_PUBLIC_URL, ""),
            "agent_timeout": entry.data.get(
                CONF_RESPONSE_TIMEOUT, DEFAULT_RESPONSE_TIMEOUT
            ),
            "configured": bool(read or control),
        },
    )


BLOCKS = (
    "entities",
    "exceptions",
    "settings",
    "auto_expose",
    "forget",
    "events",
    "sensitive",
)
SYNCING_BLOCKS = ("entities", "exceptions", "settings", "forget", "sensitive")


def _entities_block(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Resolve the two lists of entity ids to permission keys."""
    return {
        OPT_READ_ENTITIES: sorted(
            resolve_target(hass, {"entity_id": list(data.get("read", ()))})
        ),
        OPT_CONTROL_ENTITIES: sorted(
            resolve_target(hass, {"entity_id": list(data.get("control", ()))})
        ),
    }


def _exceptions_block(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Key the removed services by permission key, not by entity id."""
    exceptions: dict[str, list[str]] = {}
    for entity_id, services in data.items():
        keys = resolve_target(hass, {"entity_id": [entity_id]})
        for key in keys:
            exceptions[key] = sorted(services)
    return {OPT_EXCEPTIONS: exceptions}


def _sensitive_block(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Key the sensitive marks by permission key, not by entity id."""
    marks: dict[str, dict[str, bool]] = {}
    for entity_id, flags in data.items():
        mark = Mark.from_raw(flags)
        for key in resolve_target(hass, {"entity_id": [entity_id]}):
            marks[key] = {
                "confirm": mark.confirm,
                "refuse_automatic": mark.refuse_automatic,
            }
    return {OPT_SENSITIVE: marks}


def _sensitive_entity_ids(
    hass: HomeAssistant, marks: dict[str, Any]
) -> dict[str, dict[str, bool]]:
    """Key the sensitive marks by entity_id instead of by permission key."""
    result: dict[str, dict[str, bool]] = {}
    for key, mark in marks.items():
        entity_id = entity_id_for(hass, key)
        if entity_id is not None:
            result[entity_id] = dict(mark)
    return result


def _events_block(data: dict[str, Any]) -> dict[str, Any] | None:
    """Return the three event options, or None when a value is bad or out of range."""
    try:
        cooldown = int(data.get("cooldown_minutes", DEFAULT_EVENT_COOLDOWN))
        limit = int(data.get("hourly_limit", DEFAULT_EVENT_HOURLY_LIMIT))
        continuity = int(data.get("continuity_minutes", DEFAULT_EVENT_CONTINUITY))
    except TypeError, ValueError:
        return None
    in_range = (
        0 <= cooldown <= MAX_EVENT_MINUTES
        and 1 <= limit <= MAX_EVENT_HOURLY_LIMIT
        and 0 <= continuity <= MAX_EVENT_MINUTES
    )
    if not in_range:
        return None
    return {
        OPT_EVENT_COOLDOWN: cooldown,
        OPT_EVENT_HOURLY_LIMIT: limit,
        OPT_EVENT_CONTINUITY: continuity,
    }


def _auto_expose_block(_hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Store both switches and the areas they watch."""
    return {
        OPT_AUTO_EXPOSE: bool(data.get("read", False)),
        OPT_AUTO_CONTROL: bool(data.get("control", False)),
        OPT_AUTO_EXPOSE_AREAS: sorted(data.get("areas", ())),
    }


def _public_url_value(hass: HomeAssistant, value: str) -> str | None:
    """
    Return the origin to store, None to clear the field, or the failure detail.

    An empty field is refused when Home Assistant has no external URL.
    """
    if not value.strip():
        try:
            get_url(hass, allow_internal=False)
        except NoURLAvailableError:
            return "no_public_url"
        return None
    public_url = normalize_public_url(value)
    return "invalid_public_url" if public_url is None else public_url


def _block_keys(
    hass: HomeAssistant,
    entry: ElevenAgentsConfigEntry,
    block: str,
    data: dict[str, Any],
) -> dict[str, Any] | str:
    """Return the options one block writes, or the failure detail as a string."""
    if block == "settings":
        agent_timeout = entry.data.get(CONF_RESPONSE_TIMEOUT, DEFAULT_RESPONSE_TIMEOUT)
        value = int(data.get("confirm_timeout", DEFAULT_CONFIRM_TIMEOUT))
        if value >= agent_timeout:
            return "timeout_not_below_agent"
        keys: dict[str, Any] = {OPT_CONFIRM_TIMEOUT: value}
        if "agent_settings" in data:
            keys[OPT_AGENT_SETTINGS] = dict(data["agent_settings"])
        if "public_url" in data:
            public_url = _public_url_value(hass, str(data["public_url"]))
            if public_url in ("invalid_public_url", "no_public_url"):
                return public_url
            keys[OPT_PUBLIC_URL] = public_url
        return keys
    if block == "events":
        events = _events_block(data)
        return "events_out_of_range" if events is None else events
    simple = {
        "entities": _entities_block,
        "exceptions": _exceptions_block,
        "auto_expose": _auto_expose_block,
        "sensitive": _sensitive_block,
    }
    if block in simple:
        return simple[block](hass, data)
    if block == "forget":
        missing = {key for key in stored_keys(entry.options) if is_missing(hass, key)}
        return without_keys(entry.options, missing & set(data.get("keys", ())))
    error = f"Unhandled block: {block}"
    raise ValueError(error)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "elevenagents/config/save",
        vol.Required("entry_id"): str,
        vol.Required("block"): vol.In(BLOCKS),
        vol.Required("data"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def config_save(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Write one block of options, then synchronise if the block needs it."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Unknown config entry")
        return

    block, data = msg["block"], msg["data"]
    keys = _block_keys(hass, entry, block, data)
    if isinstance(keys, str):
        connection.send_result(msg["id"], {"outcome": "failed", "detail": keys})
        return

    merged = {**entry.options, **keys}
    options = {key: value for key, value in merged.items() if value is not None}
    hass.config_entries.async_update_entry(entry, options=options)
    async_check_missing(hass, entry)

    if block not in SYNCING_BLOCKS:
        connection.send_result(msg["id"], {"outcome": "ok", "detail": None})
        return

    if entry.state is not ConfigEntryState.LOADED:
        connection.send_result(
            msg["id"],
            {"outcome": "saved_not_sent", "detail": "The config entry is not loaded."},
        )
        return

    try:
        await async_sync_tools(hass, entry, entry.runtime_data.client)
    except (ElevenLabsError, ToolSyncError) as err:
        connection.send_result(
            msg["id"], {"outcome": "saved_not_sent", "detail": str(err)}
        )
        return

    connection.send_result(msg["id"], {"outcome": "ok", "detail": None})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "elevenagents/agent/get",
        vol.Required("entry_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def agent_get(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the agent's current system prompt and its tool settings."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Unknown config entry")
        return
    if entry.state is not ConfigEntryState.LOADED:
        connection.send_error(
            msg["id"], "not_loaded", "The config entry is not loaded."
        )
        return

    tool_id = entry.data.get(CONF_TOOL_IDS, {}).get(TOOL_GET_STATE)
    try:
        agent = await async_get_agent(
            entry.runtime_data.client, entry.data[CONF_AGENT_ID]
        )
        tool = (
            None
            if tool_id is None
            else await async_get_tool(entry.runtime_data.client, tool_id)
        )
    except ElevenLabsError as err:
        connection.send_error(msg["id"], "unreachable", str(err))
        return

    config = agent.conversation_config.agent
    prompt = "" if config is None or config.prompt is None else config.prompt.prompt
    first_message = "" if config is None else config.first_message
    conversation = agent.conversation_config.conversation
    turn = agent.conversation_config.turn

    settings = dict(AGENT_SETTING_DEFAULTS)
    if tool is not None:
        settings = {
            key: getattr(tool.tool_config, key, None) or default
            for key, default in AGENT_SETTING_DEFAULTS.items()
        }

    connection.send_result(
        msg["id"],
        {
            "name": agent.name or "",
            "prompt": prompt or "",
            "first_message": first_message or "",
            "limits": {
                "max_duration_seconds": (
                    None if conversation is None else conversation.max_duration_seconds
                ),
                "silence_end_call_timeout": (
                    None if turn is None else turn.silence_end_call_timeout
                ),
            },
            "settings": settings,
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "elevenagents/agent/save",
        vol.Required("entry_id"): str,
        vol.Optional("prompt"): str,
        vol.Optional("first_message"): str,
        vol.Optional("max_duration_seconds"): vol.All(int, vol.Range(min=1)),
        vol.Optional("silence_end_call_timeout"): _silence_timeout,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def agent_save(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Write the fields the user submitted, and nothing else."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Unknown config entry")
        return
    if entry.state is not ConfigEntryState.LOADED:
        connection.send_result(
            msg["id"],
            {"outcome": "failed", "detail": "The config entry is not loaded."},
        )
        return

    agent_config: dict[str, Any] = {}
    if "prompt" in msg:
        agent_config["prompt"] = {"prompt": msg["prompt"]}
    if "first_message" in msg:
        agent_config["first_message"] = msg["first_message"]
    conversation_config: dict[str, Any] = {}
    if agent_config:
        conversation_config["agent"] = agent_config
    if "max_duration_seconds" in msg:
        conversation_config["conversation"] = {
            "max_duration_seconds": msg["max_duration_seconds"]
        }
    if "silence_end_call_timeout" in msg:
        conversation_config["turn"] = {
            "silence_end_call_timeout": msg["silence_end_call_timeout"]
        }
    if not conversation_config:
        connection.send_result(msg["id"], {"outcome": "ok", "detail": None})
        return

    try:
        await async_patch_agent(
            entry.runtime_data.client,
            entry.data[CONF_AGENT_ID],
            conversation_config=conversation_config,
        )
    except ElevenLabsError as err:
        connection.send_result(msg["id"], {"outcome": "failed", "detail": str(err)})
        return

    connection.send_result(msg["id"], {"outcome": "ok", "detail": None})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "elevenagents/rotate",
        vol.Required("entry_id"): str,
        vol.Required("what"): vol.In(("secret", "webhook")),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def rotate(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Replace the shared secret or the webhook id, rolling back on failure."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Unknown config entry")
        return
    if entry.state is not ConfigEntryState.LOADED:
        connection.send_result(
            msg["id"],
            {"outcome": "failed", "detail": "The config entry is not loaded."},
        )
        return

    rollback: Callable[[], None] | None
    if msg["what"] == "secret":
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_WEBHOOK_SECRET: secrets.token_urlsafe(32)},
        )
        rollback = None
    else:
        old_id = entry.data[CONF_WEBHOOK_ID]
        new_id = webhook.async_generate_id()
        webhook.async_register(
            hass,
            DOMAIN,
            WEBHOOK_NAME,
            new_id,
            async_handle_webhook,
            local_only=False,
            allowed_methods=["POST"],
        )
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_WEBHOOK_ID: new_id}
        )

        def rollback() -> None:
            """Put the old path back and retire the one that never worked."""
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_WEBHOOK_ID: old_id}
            )
            webhook.async_unregister(hass, new_id)

    try:
        await async_sync_tools(hass, entry, entry.runtime_data.client)
    except (ElevenLabsError, ToolSyncError) as err:
        if rollback is not None:
            rollback()
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_ROTATION_UNSYNCED,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_ROTATION_UNSYNCED,
        )
        connection.send_result(msg["id"], {"outcome": "failed", "detail": str(err)})
        return

    if msg["what"] == "webhook":
        webhook.async_unregister(hass, old_id)
    ir.async_delete_issue(hass, DOMAIN, ISSUE_ROTATION_UNSYNCED)
    connection.send_result(msg["id"], {"outcome": "ok", "detail": None})


@callback
@websocket_api.websocket_command(
    {
        vol.Required("type"): "elevenagents/check",
        vol.Required("entry_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def check(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Run the connection check and answer with its report."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Unknown config entry")
        return
    connection.send_result(msg["id"], await async_record_check(hass, entry))


def _agent_limits(agent: Any) -> dict[str, Any]:
    """Return the two conversation limits of an agent. A missing limit is None."""
    config = agent.conversation_config
    conversation = getattr(config, "conversation", None)
    turn = getattr(config, "turn", None)
    return {
        "max_duration_seconds": (
            None if conversation is None else conversation.max_duration_seconds
        ),
        "silence_end_call_timeout": (
            None if turn is None else turn.silence_end_call_timeout
        ),
    }


@websocket_api.websocket_command(
    {vol.Required("type"): "elevenagents/conversation_token"}
)
@websocket_api.async_response
async def conversation_token(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return a conversation token and the agent's limits. Any user may ask."""
    entry, _ = _sole_entry(hass)
    if entry is None or entry.state is not ConfigEntryState.LOADED:
        connection.send_error(msg["id"], "not_configured", "ElevenAgents is not set up")
        return
    client = entry.runtime_data.client
    agent_id = entry.data[CONF_AGENT_ID]
    token, agent = await asyncio.gather(
        async_get_conversation_token(client, agent_id),
        async_get_agent(client, agent_id),
        return_exceptions=True,
    )
    if isinstance(token, ElevenLabsError):
        connection.send_error(msg["id"], "unreachable", type(token).__name__)
        return
    if isinstance(token, BaseException):
        raise token
    if isinstance(agent, BaseException):
        _LOGGER.debug("The agent read for the limits failed: %s", type(agent).__name__)
        limits: dict[str, Any] = {
            "max_duration_seconds": None,
            "silence_end_call_timeout": None,
        }
    else:
        limits = _agent_limits(agent)
    connection.send_result(msg["id"], {"token": token, "limits": limits})


def async_register_commands(hass: HomeAssistant) -> None:
    """Register every command the panel uses."""
    websocket_api.async_register_command(hass, config_get)
    websocket_api.async_register_command(hass, config_save)
    websocket_api.async_register_command(hass, agent_get)
    websocket_api.async_register_command(hass, agent_save)
    websocket_api.async_register_command(hass, rotate)
    websocket_api.async_register_command(hass, check)
    websocket_api.async_register_command(hass, conversation_token)
