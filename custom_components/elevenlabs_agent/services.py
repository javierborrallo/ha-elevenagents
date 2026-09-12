"""The sync_tools service."""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .const import DOMAIN
from .tool_sync import async_sync_tools

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

SERVICE_SYNC_TOOLS = "sync_tools"

SYNC_TOOLS_SCHEMA = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string})


async def _async_handle_sync_tools(call: ServiceCall) -> None:
    """Synchronise the tools of the config entry named in the call."""
    entry = service.async_get_config_entry(
        call.hass, DOMAIN, call.data[ATTR_CONFIG_ENTRY_ID]
    )
    await async_sync_tools(call.hass, entry, entry.runtime_data.client)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the sync_tools service. It exists whether or not an entry is loaded."""
    hass.services.async_register(
        DOMAIN, SERVICE_SYNC_TOOLS, _async_handle_sync_tools, schema=SYNC_TOOLS_SCHEMA
    )
