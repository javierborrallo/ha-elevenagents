"""The ElevenLabs Agent integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv

from .const import CONF_AGENT_ID, CONF_API_KEY, CONF_TOOL_IDS, DOMAIN
from .elevenlabs_client import (
    ElevenLabsAuthError,
    ElevenLabsError,
    async_delete_tool,
    async_get_agent,
    async_get_client,
    async_patch_agent,
)
from .expose import async_track_new_entities
from .panel import async_register_panel
from .services import async_setup_services
from .status import async_create_check_debouncer
from .tool_sync import async_sync_tools
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
    """Set up the config entry: register the webhook and run the initial sync."""
    client = async_get_client(hass, entry.data[CONF_API_KEY])
    debouncer = async_create_check_debouncer(hass, entry)
    entry.runtime_data = RuntimeData(
        bucket=TokenBucket(), client=client, check_debouncer=debouncer
    )
    entry.async_on_unload(debouncer.async_shutdown)

    async_register(hass, entry)
    entry.async_on_unload(lambda: async_unregister(hass, entry))
    entry.async_on_unload(async_track_new_entities(hass, entry))

    try:
        await async_sync_tools(hass, entry, client)
    except ElevenLabsAuthError as err:
        raise ConfigEntryAuthFailed from err

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
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
