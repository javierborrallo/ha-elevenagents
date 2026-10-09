"""Generate the two tool schemas and synchronise them with the workspace."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final, cast

from homeassistant.components import webhook
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_get_all_descriptions
from homeassistant.helpers.translation import async_get_translations

from .const import (
    CONF_AGENT_ID,
    CONF_RESPONSE_TIMEOUT,
    CONF_TOOL_IDS,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DEFAULT_RESPONSE_TIMEOUT,
    DISCRIMINATOR_CALL_SERVICE,
    DISCRIMINATOR_GET_STATE,
    OPT_AGENT_SETTINGS,
    SECRET_HEADER,
    TOOL_CALL_SERVICE,
    TOOL_GET_STATE,
)
from .elevenlabs_client import (
    ElevenLabsNotFoundError,
    async_create_tool,
    async_get_agent,
    async_get_tool,
    async_patch_agent,
    async_update_tool,
)
from .permissions import entity_id_for, from_options, granted_services
from .repairs import async_check_agent, async_check_timeouts
from .sensitive import any_confirm

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from elevenlabs import AsyncElevenLabs
    from homeassistant.core import HomeAssistant

    from . import ElevenLabsAgentConfigEntry
    from .permissions import Permissions

_LOGGER = logging.getLogger(__name__)

# Client events that report a tool call on the WebSocket.
TOOL_CLIENT_EVENTS: Final = ("agent_tool_request", "agent_tool_response")

GET_STATE_DESCRIPTION = (
    "Read the current state of one or more Home Assistant entities. "
    "Call this before answering any question about state. "
    "Never invent an entity id."
)

CALL_SERVICE_DESCRIPTION = (
    "Perform one action on the one Home Assistant entity the user named. "
    "Call it only when an entity in the catalogue matches the user's words and "
    "can do what they asked. Otherwise do not call it: tell the user. "
    "If a call returns an outcome of unconfirmed, call ha_get_state to check "
    "the entity. Never repeat an action that returned unconfirmed."
    " If the outcome is confirmation_required, nothing ran: ask the user to confirm "
    "this action on this device. If the user confirms, call again with the same "
    "entity_id, action and service_data, and the confirmation_code. If not, do nothing."
)

# Any selector kind absent from this table is not a scalar. Its key is omitted.
_SELECTOR_TYPES: dict[str, str] = {
    "number": "number",
    "text": "string",
    "select": "string",
    "state": "string",
    "boolean": "boolean",
}

# Fields this integration checks in the tool's read-back after a write.
COMPARED_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "api_schema",
    "pre_tool_speech",
)


class ToolSyncError(Exception):
    """Raised when a tool's read-back diverges from what was sent."""


def _flatten_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """
    Return the scalar fields of a service, descending into sections.

    A field entry that has its own fields and no selector is a section.
    """
    flat: dict[str, Any] = {}
    for key, field in fields.items():
        if "fields" in field and "selector" not in field:
            flat.update(_flatten_fields(field["fields"]))
        else:
            flat[key] = field
    return flat


def entity_catalogue(hass: HomeAssistant, keys: Iterable[str]) -> tuple[list[str], str]:
    """
    Build the entity_id enum and its readable catalogue text.

    Each line maps one entity_id to its spoken name and area. An entity
    with no area is written without a trailing empty bracket. Only a
    registry entry carries an area.
    """
    registry = er.async_get(hass)
    resolved: list[tuple[str, er.RegistryEntry | None]] = []
    for key in keys:
        entity_id = entity_id_for(hass, key)
        if entity_id is not None:
            resolved.append((entity_id, registry.entities.get_entry(key)))
    resolved.sort(key=lambda pair: pair[0])

    lines: list[str] = []
    for entity_id, entry in resolved:
        state = hass.states.get(entity_id)
        name = entity_id
        if state is not None:
            name = state.attributes.get("friendly_name", entity_id)

        area = None
        if entry is not None:
            area_id = er.async_get_effective_area_id(hass, entry)
            if area_id is not None:
                area_entry = ar.async_get(hass).async_get_area(area_id)
                area = None if area_entry is None else area_entry.name

        line = f"{entity_id} = {name}"
        if area is not None:
            line = f"{line} ({area})"
        lines.append(line)

    return [entity_id for entity_id, _ in resolved], "\n".join(lines)


def service_data_keys(
    descriptions: Mapping[str, Mapping[str, Any]], action: str
) -> frozenset[str]:
    """Return the scalar service_data keys one action declares."""
    domain, _, service = action.partition(".")
    fields = _flatten_fields(
        descriptions.get(domain, {}).get(service, {}).get("fields", {})
    )
    return frozenset(
        key
        for key, field in fields.items()
        if next(iter(field.get("selector") or {}), None) in _SELECTOR_TYPES
    )


def service_data_properties(
    descriptions: Mapping[str, Mapping[str, Any]],
    actions: Iterable[str],
    translations: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    """
    Build the service_data properties shared by every permitted action.

    No minimum, maximum or enum travels with a property. Two permitted
    services can publish the same key with different bounds, and Home
    Assistant validates against the entity's own limits regardless. A key
    two actions declare with a different type is omitted.
    """
    properties: dict[str, dict[str, Any]] = {}
    types_seen: dict[str, str] = {}
    omitted: set[str] = set()

    for action in actions:
        domain, _, service = action.partition(".")
        service_description = descriptions.get(domain, {}).get(service, {})
        fields = _flatten_fields(service_description.get("fields", {}))

        for key, field in fields.items():
            selector = field.get("selector") or {}
            selector_kind = next(iter(selector), None)
            if selector_kind is None:
                continue
            json_type = _SELECTOR_TYPES.get(selector_kind)
            if json_type is None:
                continue

            previous_type = types_seen.get(key)
            if previous_type is not None and previous_type != json_type:
                omitted.add(key)
                _LOGGER.warning(
                    "Omitting service_data key '%s': %s and %s disagree on its type",
                    key,
                    previous_type,
                    json_type,
                )
                continue
            types_seen[key] = json_type

            properties[key] = {
                "type": json_type,
                "description": translations.get(
                    f"component.{domain}.services.{service}.fields.{key}.description",
                    key,
                ),
            }

    for key in omitted:
        properties.pop(key, None)

    return properties


AGENT_SETTING_DEFAULTS: Final = {
    "pre_tool_speech": "auto",
}


def agent_settings(options: Mapping[str, Any]) -> dict[str, Any]:
    """Return the tool behaviour block, with the platform defaults filled in."""
    stored = options.get(OPT_AGENT_SETTINGS, {})
    return {
        key: stored.get(key, value) for key, value in AGENT_SETTING_DEFAULTS.items()
    }


def build_get_state(  # noqa: PLR0913 - a keyword-only settings argument joins the rest
    hass: HomeAssistant,
    perms: Permissions,
    url: str,
    secret: str,
    timeout: int,
    *,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Build the ha_get_state tool definition."""
    enum, catalogue = entity_catalogue(hass, perms.read | perms.control)
    entity_description = catalogue or "No entity is exposed to this tool."

    return {
        "name": TOOL_GET_STATE,
        "type": "webhook",
        "description": GET_STATE_DESCRIPTION,
        "response_timeout_secs": timeout,
        **settings,
        "api_schema": {
            "url": url,
            "method": "POST",
            "request_headers": {SECRET_HEADER: secret},
            "request_body_schema": {
                "type": "object",
                "required": ["entity_id"],
                "properties": {
                    "tool": {
                        "type": "string",
                        "constant_value": DISCRIMINATOR_GET_STATE,
                    },
                    "entity_id": {
                        "type": "array",
                        "description": entity_description,
                        "items": {
                            "type": "string",
                            "enum": enum,
                            "description": "One entity id from the catalogue above.",
                        },
                    },
                },
            },
        },
    }


def build_call_service(  # noqa: PLR0913, PLR0917 - each argument is part of the tool
    hass: HomeAssistant,
    perms: Permissions,
    descriptions: Mapping[str, Mapping[str, Any]],
    translations: Mapping[str, str],
    url: str,
    secret: str,
    timeout: int,
    *,
    settings: dict[str, Any],
    with_history: bool = False,
) -> dict[str, Any]:
    """Build the ha_call_service tool definition."""
    enum, catalogue = entity_catalogue(hass, perms.control)
    entity_description = (
        f"The entity the user named, from this catalogue:\n{catalogue}"
        if catalogue
        else "No entity is exposed to this tool."
    )

    actions: set[str] = set()
    for key in perms.control:
        actions |= granted_services(hass, perms, key, descriptions)
    properties = service_data_properties(descriptions, actions, translations)

    config: dict[str, Any] = {
        "name": TOOL_CALL_SERVICE,
        "type": "webhook",
        "description": CALL_SERVICE_DESCRIPTION,
        "response_timeout_secs": timeout,
        **settings,
        "api_schema": {
            "url": url,
            "method": "POST",
            "request_headers": {SECRET_HEADER: secret},
            "request_body_schema": {
                "type": "object",
                "required": ["entity_id", "action"],
                "properties": {
                    "tool": {
                        "type": "string",
                        "constant_value": DISCRIMINATOR_CALL_SERVICE,
                    },
                    "entity_id": {
                        "type": "string",
                        "description": entity_description,
                        "enum": enum,
                    },
                    "action": {
                        "type": "string",
                        "description": (
                            "The domain.service action to run on entity_id. The list "
                            "covers every controlled entity together, so an action "
                            "here may not apply to this one; a failed outcome says why."
                        ),
                        "enum": sorted(actions),
                    },
                    "service_data": {
                        "type": "object",
                        "description": (
                            "Extra parameters the action accepts. An entity may not "
                            "support every parameter, and Home Assistant can ignore "
                            "one it does not support without an error: check the state "
                            "in the response before saying it worked."
                        ),
                        "properties": properties,
                    },
                    # The platform fills this property. It carries no description.
                    "conversation_id": {
                        "type": "string",
                        "dynamic_variable": "system__conversation_id",
                    },
                    "confirmation_code": {
                        "type": "string",
                        "description": (
                            "The code from a confirmation_required answer. Send it "
                            "only when repeating an action the user confirmed."
                        ),
                    },
                },
            },
        },
    }

    if with_history:
        schema = config["api_schema"]["request_body_schema"]
        # The platform fills this property. It carries no description.
        schema["properties"]["conversation_history"] = {
            "type": "string",
            "dynamic_variable": "system__conversation_history",
        }

    return config


def _as_dict(value: Any) -> dict[str, Any]:
    """Return a plain dict view of a mapping, a pydantic model or a namespace."""
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return cast("dict[str, Any]", value.model_dump())
    return cast("dict[str, Any]", vars(value))


def entity_enum(config: Any) -> list[str] | None:
    """
    Return the entity_id enum of a tool definition or read-back.

    Return None when the definition has no such enum.
    """
    try:
        api_schema = _as_dict(_as_dict(config)["api_schema"])
        schema = _as_dict(api_schema["request_body_schema"])
        entity = _as_dict(_as_dict(schema["properties"])["entity_id"])
    except KeyError, TypeError:
        return None
    items = entity.get("items")
    holder = entity if items is None else _as_dict(items)
    enum = holder.get("enum")
    return None if enum is None else [str(value) for value in enum]


def _controlled_domains(hass: HomeAssistant, perms: Permissions) -> set[str]:
    """Return the domains of every entity the user may control."""
    domains: set[str] = set()
    for key in perms.control:
        entity_id = entity_id_for(hass, key)
        if entity_id is not None:
            domains.add(entity_id.partition(".")[0])
    return domains


def _survives(sent: Any, got: Any, path: str = "") -> list[str]:
    """
    Return the paths where a sent value did not come back unchanged.

    The comparison is one-directional. Keys the API adds are ignored; only
    keys this integration sent are checked, at every depth.
    """
    if isinstance(sent, dict):
        if not isinstance(got, dict):
            return [path or "."]
        divergent: list[str] = []
        for key, value in sent.items():
            if key not in got:
                divergent.append(f"{path}.{key}" if path else key)
                continue
            divergent += _survives(value, got[key], f"{path}.{key}" if path else key)
        return divergent
    return [] if sent == got else [path or "."]


def readback_divergence(sent: dict[str, Any], got: Any) -> list[str]:
    """Return the compared fields where a read-back differs from what was sent."""
    sent_view = _as_dict(sent)
    got_view = _as_dict(got)
    divergent: list[str] = []
    for field in COMPARED_FIELDS:
        divergent += _survives(sent_view.get(field), got_view.get(field), field)
    return divergent


def _check_readback(name: str, sent: dict[str, Any], got: Any) -> None:
    """Raise when a value this integration sent did not come back unchanged."""
    divergent = readback_divergence(sent, got)
    if divergent:
        message = f"{name} read-back diverged from what was sent: {divergent}"
        raise ToolSyncError(message)


async def _attach(
    hass: HomeAssistant,
    entry: ElevenLabsAgentConfigEntry,
    client: AsyncElevenLabs,
    stored: dict[str, str],
) -> None:
    """
    Attach every synced tool id to the agent's prompt.

    Check the agent's protection from the same read. The write is a union.
    Sending only the ids this integration owns would detach every tool the
    user attached to the agent by hand.
    """
    agent_id = entry.data[CONF_AGENT_ID]
    agent = await async_get_agent(client, agent_id)
    async_check_agent(hass, entry, agent)
    agent_config = agent.conversation_config.agent
    prompt = None if agent_config is None else agent_config.prompt
    attached = [] if prompt is None else list(prompt.tool_ids or [])
    for tool_id in stored.values():
        if tool_id not in attached:
            attached.append(tool_id)

    conversation_config: dict[str, Any] = {"agent": {"prompt": {"tool_ids": attached}}}
    events = _client_events(agent.conversation_config)
    if events is not None:
        conversation_config["conversation"] = {"client_events": events}
    await async_patch_agent(client, agent_id, conversation_config=conversation_config)


def _client_events(conversation_config: Any) -> list[str] | None:
    """
    Return the agent's client events plus the tool events, or None.

    None means the agent has no client event list. The platform default stays.
    """
    conversation = getattr(conversation_config, "conversation", None)
    events = getattr(conversation, "client_events", None)
    if events is None:
        return None
    merged = list(events)
    for event in TOOL_CLIENT_EVENTS:
        if event not in merged:
            merged.append(event)
    return merged


async def async_build_definitions(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry
) -> dict[str, dict[str, Any]]:
    """Build the two tool definitions a sync sends now, keyed by tool name."""
    perms = from_options(entry.options)
    descriptions = await async_get_all_descriptions(hass)
    translations = await async_get_translations(
        hass,
        hass.config.language,
        "services",
        integrations=_controlled_domains(hass, perms),
    )
    secret = entry.data[CONF_WEBHOOK_SECRET]
    timeout = int(entry.data.get(CONF_RESPONSE_TIMEOUT, DEFAULT_RESPONSE_TIMEOUT))
    url = webhook.async_generate_url(
        hass, entry.data[CONF_WEBHOOK_ID], allow_internal=False
    )
    settings = agent_settings(entry.options)

    return {
        TOOL_GET_STATE: build_get_state(
            hass, perms, url, secret, timeout, settings=settings
        ),
        TOOL_CALL_SERVICE: build_call_service(
            hass,
            perms,
            descriptions,
            translations,
            url,
            secret,
            timeout,
            settings=settings,
            with_history=any_confirm(entry.options),
        ),
    }


async def _sync(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry, client: AsyncElevenLabs
) -> None:
    """
    Create or update each tool, store its id, then attach both to the agent.

    Record the catalogue only when the runtime data of the entry is the
    runtime data this sync started with.
    """
    runtime = entry.runtime_data
    definitions = await async_build_definitions(hass, entry)
    perms = from_options(entry.options)
    catalogue = entity_catalogue(hass, perms.read | perms.control)

    stored: dict[str, str] = dict(entry.data.get(CONF_TOOL_IDS, {}))

    for name, config in definitions.items():
        tool_id = stored.get(name)
        if tool_id is None:
            created = await async_create_tool(client, config)
            tool_id = created.id
        else:
            try:
                await async_update_tool(client, tool_id, config)
            except ElevenLabsNotFoundError:
                created = await async_create_tool(client, config)
                tool_id = created.id

        # Store the id before verifying the write.
        stored[name] = tool_id
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_TOOL_IDS: dict(stored)}
        )

        readback = await async_get_tool(client, tool_id)
        _check_readback(name, config, readback.tool_config)

    await _attach(hass, entry, client, stored)
    if getattr(entry, "runtime_data", None) is runtime:
        runtime.catalogue = catalogue
    async_check_timeouts(hass, entry, definitions[TOOL_CALL_SERVICE])


async def async_sync_tools(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry, client: AsyncElevenLabs
) -> None:
    """
    Synchronise the two tool definitions and attach them to the agent.

    The lock serialises a burst of syncs within one load of the entry: a
    registry change and the panel can each start one close together. It
    lives in RuntimeData, so it does not serialise across a reload, which
    replaces RuntimeData with a new lock. The connection check is
    scheduled when the sync ends, on success and on failure.
    """
    async with entry.runtime_data.sync_lock:
        try:
            await _sync(hass, entry, client)
        finally:
            runtime = getattr(entry, "runtime_data", None)
            if runtime is not None:
                runtime.check_debouncer.async_schedule_call()
