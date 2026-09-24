"""The connection check: local state and the ElevenLabs workspace, in one report."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState

from .const import (
    CONF_AGENT_ID,
    CONF_API_KEY,
    CONF_RESPONSE_TIMEOUT,
    CONF_TOOL_IDS,
    DEFAULT_CONFIRM_TIMEOUT,
    DEFAULT_RESPONSE_TIMEOUT,
    OPT_CONFIRM_TIMEOUT,
)
from .elevenlabs_client import (
    ElevenLabsAuthError,
    ElevenLabsError,
    ElevenLabsNotFoundError,
    async_get_agent,
    async_get_client,
    async_get_tool,
)
from .permissions import entity_id_for, missing_keys
from .repairs import is_protected
from .tool_sync import async_build_definitions, entity_enum, readback_divergence

if TYPE_CHECKING:
    from elevenlabs import AsyncElevenLabs
    from homeassistant.core import HomeAssistant

    from . import ElevenLabsAgentConfigEntry, RuntimeData

OK = "ok"
PROBLEM = "problem"
SKIPPED = "skipped"

CHECKS = (
    "loaded",
    "key",
    "protected",
    "tools_attached",
    "tools_current",
    "timeouts",
    "missing_entities",
)


def _item(check: str, status: str, detail: Any = None) -> dict[str, Any]:
    """Return one line of the report."""
    return {"check": check, "status": status, "detail": detail}


def _client(hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry) -> AsyncElevenLabs:
    """Return the entry's client, or a new one when the entry is not loaded."""
    runtime: RuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is not None:
        return runtime.client
    return async_get_client(hass, entry.data[CONF_API_KEY])


async def _tools_current(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry, client: AsyncElevenLabs
) -> dict[str, list[str]]:
    """
    Compare each tool's read-back with what a sync sends now.

    Return the tools that differ, the entities missing on the agent, the
    entities extra on the agent, and the tools that could not be read.
    """
    stored: dict[str, str] = entry.data.get(CONF_TOOL_IDS, {})
    tools: list[str] = []
    unreadable: list[str] = []
    missing: set[str] = set()
    extra: set[str] = set()
    for name, config in (await async_build_definitions(hass, entry)).items():
        tool_id = stored.get(name)
        readback: Any = None
        if tool_id is not None:
            try:
                readback = await async_get_tool(client, tool_id)
            except ElevenLabsNotFoundError:
                readback = None
        if readback is None:
            tools.append(name)
            unreadable.append(name)
            continue
        if not readback_divergence(config, readback.tool_config):
            continue
        tools.append(name)
        got = entity_enum(readback.tool_config)
        if got is None:
            unreadable.append(name)
            continue
        sent = set(entity_enum(config) or [])
        missing |= sent - set(got)
        extra |= set(got) - sent
    return {
        "tools": sorted(tools),
        "missing": sorted(missing),
        "extra": sorted(extra),
        "unreadable": sorted(unreadable),
    }


def _detached_tools(entry: ElevenLabsAgentConfigEntry, agent: Any) -> list[str]:
    """Return the stored tools that are not on the agent's prompt."""
    agent_config = agent.conversation_config.agent
    prompt = None if agent_config is None else agent_config.prompt
    attached = set() if prompt is None else set(prompt.tool_ids or [])
    stored: dict[str, str] = entry.data.get(CONF_TOOL_IDS, {})
    return sorted(name for name, tool_id in stored.items() if tool_id not in attached)


def _local_checks(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry
) -> list[dict[str, Any]]:
    """Return the two checks that need no network."""
    confirm_timeout = entry.options.get(OPT_CONFIRM_TIMEOUT, DEFAULT_CONFIRM_TIMEOUT)
    agent_timeout = int(entry.data.get(CONF_RESPONSE_TIMEOUT, DEFAULT_RESPONSE_TIMEOUT))
    missing = sorted(
        entity_id_for(hass, key) or key for key in missing_keys(hass, entry.options)
    )
    return [
        _item("timeouts", OK)
        if confirm_timeout <= agent_timeout
        else _item(
            "timeouts",
            PROBLEM,
            {"confirm_timeout": confirm_timeout, "agent_timeout": agent_timeout},
        ),
        _item("missing_entities", PROBLEM, missing)
        if missing
        else _item("missing_entities", OK),
    ]


async def async_run_check(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry
) -> dict[str, Any]:
    """
    Check the entry, the API key, the agent and both tools.

    A refused or unreachable key skips the checks that need ElevenLabs.
    """
    checks = [
        _item("loaded", OK)
        if entry.state is ConfigEntryState.LOADED
        else _item("loaded", PROBLEM, entry.state.value)
    ]

    client = _client(hass, entry)
    try:
        agent = await async_get_agent(client, entry.data[CONF_AGENT_ID])
    except ElevenLabsAuthError:
        agent = None
        checks.append(_item("key", PROBLEM, "refused"))
    except ElevenLabsError as err:
        agent = None
        checks.append(_item("key", PROBLEM, str(err)))
    else:
        checks.append(_item("key", OK))

    if agent is None:
        checks += [
            _item(name, SKIPPED)
            for name in ("protected", "tools_attached", "tools_current")
        ]
    else:
        detached = _detached_tools(entry, agent)
        current = await _tools_current(hass, entry, client)
        checks += [
            _item("protected", OK if is_protected(agent) else PROBLEM),
            _item("tools_attached", PROBLEM, detached)
            if detached
            else _item("tools_attached", OK),
            _item("tools_current", PROBLEM, current)
            if current["tools"]
            else _item("tools_current", OK),
        ]

    checks += _local_checks(hass, entry)
    return {
        "ok": all(item["status"] != PROBLEM for item in checks),
        "checks": checks,
    }
