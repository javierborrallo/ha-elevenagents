"""The connection check: local state and the ElevenLabs workspace, in one report."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import aiohttp
import httpx
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError

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
    ElevenLabsScopeError,
    async_get_agent,
    async_get_client,
    async_get_tool,
)
from .permissions import entity_id_for, missing_keys
from .public_url import entry_webhook_url
from .repairs import is_protected
from .tool_sync import async_build_definitions, entity_enum, readback_divergence

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from elevenlabs import AsyncElevenLabs
    from homeassistant.core import HomeAssistant

    from . import ElevenAgentsConfigEntry, RuntimeData

OK = "ok"
PROBLEM = "problem"
SKIPPED = "skipped"

# Seconds the webhook probe waits for an answer.
WEBHOOK_PROBE_TIMEOUT = 10

CHECKS = (
    "loaded",
    "key",
    "protected",
    "tools_attached",
    "tools_current",
    "webhook",
    "timeouts",
    "missing_entities",
)


async def _webhook_check(
    hass: HomeAssistant, entry: ElevenAgentsConfigEntry
) -> dict[str, Any]:
    """
    Call the webhook's external URL without the secret.

    Skip the check when the entry is not loaded. The handler of this
    integration answers 401. The detail never carries the URL.
    """
    if entry.state is not ConfigEntryState.LOADED:
        return _item("webhook", SKIPPED)
    try:
        url = entry_webhook_url(hass, entry)
    except NoURLAvailableError:
        return _item("webhook", PROBLEM, "no external URL")
    return await async_probe_webhook(hass, url)


async def async_probe_webhook(hass: HomeAssistant, url: str) -> dict[str, Any]:
    """
    Call one webhook URL without the secret and return the report line.

    The handler of this integration answers 401. The detail never carries
    the URL.
    """
    try:
        async with async_get_clientsession(hass).post(
            url, json={}, timeout=aiohttp.ClientTimeout(total=WEBHOOK_PROBE_TIMEOUT)
        ) as response:
            status = response.status
    except (aiohttp.ClientError, TimeoutError) as err:
        return _item("webhook", PROBLEM, type(err).__name__)
    if status == HTTPStatus.UNAUTHORIZED:
        return _item("webhook", OK)
    return _item("webhook", PROBLEM, f"HTTP {status}")


def _item(check: str, status: str, detail: Any = None) -> dict[str, Any]:
    """Return one line of the report."""
    return {"check": check, "status": status, "detail": detail}


def _key_detail(err: Exception) -> str:
    """
    Map a failed agent read onto a fixed vocabulary, never the raw error text.

    The raw error can carry every response header and the request body.
    The recorder stores this detail, and every user with access can read it.
    """
    if isinstance(err, ElevenLabsAuthError):
        return "refused"
    if isinstance(err, ElevenLabsScopeError):
        return "scope"
    if isinstance(err, ElevenLabsNotFoundError):
        return "agent_not_found"
    status_code = getattr(err, "status_code", None)
    if status_code is None:
        return "unreachable"
    return f"HTTP {status_code}"


def _client(hass: HomeAssistant, entry: ElevenAgentsConfigEntry) -> AsyncElevenLabs:
    """Return the entry's client, or a new one when the entry is not loaded."""
    runtime: RuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is not None:
        return runtime.client
    return async_get_client(hass, entry.data[CONF_API_KEY])


async def _tools_current(
    hass: HomeAssistant, entry: ElevenAgentsConfigEntry, client: AsyncElevenLabs
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
            except ElevenLabsError, httpx.HTTPError:
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
            # The tool was read; it just carries no enum to compare. It
            # differs (hence "tools"), but the read itself did not fail.
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


def _detached_tools(entry: ElevenAgentsConfigEntry, agent: Any) -> list[str]:
    """Return the stored tools that are not on the agent's prompt."""
    agent_config = agent.conversation_config.agent
    prompt = None if agent_config is None else agent_config.prompt
    attached = set() if prompt is None else set(prompt.tool_ids or [])
    stored: dict[str, str] = entry.data.get(CONF_TOOL_IDS, {})
    return sorted(name for name, tool_id in stored.items() if tool_id not in attached)


def _local_checks(
    hass: HomeAssistant, entry: ElevenAgentsConfigEntry
) -> list[dict[str, Any]]:
    """Return the two checks that need no network."""
    confirm_timeout = entry.options.get(OPT_CONFIRM_TIMEOUT, DEFAULT_CONFIRM_TIMEOUT)
    agent_timeout = int(entry.data.get(CONF_RESPONSE_TIMEOUT, DEFAULT_RESPONSE_TIMEOUT))
    missing = sorted(
        entity_id_for(hass, key) or key for key in missing_keys(hass, entry.options)
    )
    return [
        _item("timeouts", OK)
        if confirm_timeout < agent_timeout
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
    hass: HomeAssistant, entry: ElevenAgentsConfigEntry
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
    except (ElevenLabsError, httpx.HTTPError) as err:
        agent = None
        checks.append(_item("key", PROBLEM, _key_detail(err)))
        _LOGGER.debug("The key check failed: %s", err)
    else:
        checks.append(_item("key", OK))

    if agent is None:
        checks += [
            _item(name, SKIPPED)
            for name in ("protected", "tools_attached", "tools_current")
        ]
    else:
        detached = _detached_tools(entry, agent)
        try:
            current = await _tools_current(hass, entry, client)
        except NoURLAvailableError:
            tools_item = _item("tools_current", PROBLEM, "no external URL")
        else:
            tools_item = (
                _item("tools_current", PROBLEM, current)
                if current["tools"]
                else _item("tools_current", OK)
            )
        checks += [
            _item("protected", OK if is_protected(agent) else PROBLEM),
            _item("tools_attached", PROBLEM, detached)
            if detached
            else _item("tools_attached", OK),
            tools_item,
        ]

    checks.append(await _webhook_check(hass, entry))
    checks += _local_checks(hass, entry)
    return {
        "ok": all(item["status"] != PROBLEM for item in checks),
        "checks": checks,
    }
