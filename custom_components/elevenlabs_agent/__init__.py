"""The ElevenLabs Agent integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

from .webhook import TokenBucket, async_register, async_unregister

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@dataclass
class RuntimeData:
    """Runtime state attached to the config entry."""

    bucket: TokenBucket


type ElevenLabsAgentConfigEntry = ConfigEntry[RuntimeData]


# PROVISIONAL — Task 6 needs a working async_setup_entry to test the webhook
# over HTTP. Task 16 replaces this with the full setup/unload/reload.
async def async_setup_entry(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry
) -> bool:
    """Set up the config entry: register the webhook and its rate limiter."""
    entry.runtime_data = RuntimeData(bucket=TokenBucket())
    async_register(hass, entry)
    return True


# PROVISIONAL — see async_setup_entry above.
async def async_unload_entry(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry
) -> bool:
    """Unload the config entry: remove the webhook."""
    async_unregister(hass, entry)
    return True
