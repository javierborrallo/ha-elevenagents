"""The ElevenAgents integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
from homeassistant.components import webhook as ha_webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.network import NoURLAvailableError

from .const import CONF_AGENT_ID, CONF_API_KEY, CONF_TOOL_IDS, CONF_WEBHOOK_ID, DOMAIN
from .elevenlabs_client import (
    ElevenLabsAuthError,
    ElevenLabsError,
    async_delete_tool,
    async_get_agent,
    async_get_client,
    async_patch_agent,
)
from .events import EventHistory, EventLimiter
from .expose import async_track_new_entities
from .panel import async_register_panel
from .sensitive import Gate, discard_gate, gate_for_entry
from .services import async_setup_services
from .status import async_create_check_debouncer
from .tool_sync import ToolSyncError, async_sync_tools
from .webhook import TokenBucket, async_register, async_unregister
from .websocket import async_register_commands

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from elevenlabs import AsyncElevenLabs
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.debounce import Debouncer
    from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR]


@dataclass
class RuntimeData:
    """Runtime state attached to the config entry."""

    bucket: TokenBucket
    client: AsyncElevenLabs
    check_debouncer: Debouncer[Coroutine[Any, Any, None]]
    sync_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    catalogue: tuple[list[str], str] | None = None
    last_check: dict[str, Any] | None = None
    event_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    event_limiter: EventLimiter = field(default_factory=EventLimiter)
    event_history: EventHistory = field(default_factory=EventHistory)
    gate: Gate = field(default_factory=Gate)


type ElevenLabsAgentConfigEntry = ConfigEntry[RuntimeData]


async def async_setup(
    hass: HomeAssistant,
    config: ConfigType,  # noqa: ARG001 - config-entry-only integration
) -> bool:
    """Register the sync_tools service and the panel's commands, once."""
    async_setup_services(hass)
    await async_register_panel(hass)
    async_register_commands(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry
) -> bool:
    """
    Set up the config entry: register the webhook, forward the platforms, then sync.

    Raise ConfigEntryNotReady before anything is set up when no external URL
    exists. Unload the platforms before an auth error or a lost external URL
    ends the setup. Log any other sync failure and keep the entry loaded.
    """
    try:
        ha_webhook.async_generate_url(
            hass, entry.data[CONF_WEBHOOK_ID], allow_internal=False
        )
    except NoURLAvailableError as err:
        raise ConfigEntryNotReady from err

    client = async_get_client(hass, entry.data[CONF_API_KEY])
    debouncer = async_create_check_debouncer(hass, entry)
    entry.runtime_data = RuntimeData(
        bucket=TokenBucket(),
        client=client,
        check_debouncer=debouncer,
        gate=gate_for_entry(hass, entry.entry_id),
    )
    entry.async_on_unload(debouncer.async_shutdown)

    async_register(hass, entry)
    entry.async_on_unload(lambda: async_unregister(hass, entry))
    entry.async_on_unload(async_track_new_entities(hass, entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    try:
        await async_sync_tools(hass, entry, client)
    except ElevenLabsAuthError as err:
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        raise ConfigEntryAuthFailed from err
    except NoURLAvailableError as err:
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        raise ConfigEntryNotReady from err
    except ElevenLabsError, ToolSyncError, httpx.HTTPError:
        _LOGGER.warning(
            "The initial sync with ElevenLabs failed; the entry stays "
            "loaded and the next sync retries"
        )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry
) -> bool:
    """Unload the sensors. Every other resource unregisters through async_on_unload."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry
) -> None:
    """
    Detach this integration's tools from the agent, then delete them.

    Both steps are best effort and logged. A failure in either one does
    not block the entry from being removed.
    """
    discard_gate(hass, entry.entry_id)
    stored: dict[str, str] = dict(entry.data.get(CONF_TOOL_IDS, {}))
    if not stored:
        return

    client = async_get_client(hass, entry.data[CONF_API_KEY])
    agent_id = entry.data[CONF_AGENT_ID]
    owned = set(stored.values())

    try:
        agent = await async_get_agent(client, agent_id)
        agent_config = agent.conversation_config.agent
        prompt = None if agent_config is None else agent_config.prompt
        attached = [] if prompt is None else list(prompt.tool_ids or [])
        remaining = [tool_id for tool_id in attached if tool_id not in owned]
        await async_patch_agent(
            client,
            agent_id,
            conversation_config={"agent": {"prompt": {"tool_ids": remaining}}},
        )
    except ElevenLabsError:
        _LOGGER.exception("Could not detach this entry's tools from agent %s", agent_id)

    for tool_id in owned:
        try:
            await async_delete_tool(client, tool_id)
        except ElevenLabsError:
            _LOGGER.exception("Could not delete tool %s", tool_id)
