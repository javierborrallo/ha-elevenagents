"""The sync_tools and check services."""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import SupportsResponse, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .const import DOMAIN
from .status import async_record_check
from .tool_sync import async_sync_tools

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse

SERVICE_SYNC_TOOLS = "sync_tools"
SERVICE_CHECK = "check"

ENTRY_SCHEMA = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string})


async def _async_handle_sync_tools(call: ServiceCall) -> None:
    """Synchronise the tools of the config entry named in the call."""
    entry = service.async_get_config_entry(
        call.hass, DOMAIN, call.data[ATTR_CONFIG_ENTRY_ID]
    )
    await async_sync_tools(call.hass, entry, entry.runtime_data.client)


async def _async_handle_check(call: ServiceCall) -> ServiceResponse:
    """Run the connection check for the config entry named in the call."""
    entry = service.async_get_config_entry(
        call.hass, DOMAIN, call.data[ATTR_CONFIG_ENTRY_ID]
    )
    return await async_record_check(call.hass, entry)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register both services. They exist whether or not an entry is loaded."""
    hass.services.async_register(
        DOMAIN, SERVICE_SYNC_TOOLS, _async_handle_sync_tools, schema=ENTRY_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CHECK,
        _async_handle_check,
        schema=ENTRY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
