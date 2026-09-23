"""Diagnostics for the config entry, with every secret redacted."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data

from .const import (
    CONF_AGENT_ID,
    CONF_API_KEY,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    OPT_CONTROL_ENTITIES,
    OPT_EXCEPTIONS,
    OPT_READ_ENTITIES,
)
from .permissions import entity_id_for, missing_keys

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import ElevenLabsAgentConfigEntry

TO_REDACT = {CONF_API_KEY, CONF_AGENT_ID, CONF_WEBHOOK_ID, CONF_WEBHOOK_SECRET}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry
) -> dict[str, Any]:
    """
    Return the entry's configuration and a summary of its state.

    Redact the credentials and the agent id. Include no entity state.
    Home Assistant adds the repair issues of this domain by itself.
    """
    options = entry.options
    runtime = getattr(entry, "runtime_data", None)
    catalogue = None if runtime is None else runtime.catalogue
    missing = (entity_id_for(hass, key) or key for key in missing_keys(hass, options))

    return {
        "entry": {
            "state": entry.state.value,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(options),
        },
        "permissions": {
            "read": len(options.get(OPT_READ_ENTITIES, ())),
            "control": len(options.get(OPT_CONTROL_ENTITIES, ())),
            "restricted": len(options.get(OPT_EXCEPTIONS, {})),
            "missing": sorted(missing),
        },
        "sync": {
            "catalogue_sent": catalogue is not None,
            "entity_ids": None if catalogue is None else list(catalogue[0]),
        },
    }
