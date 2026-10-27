"""Diagnostics for the config entry, with every secret redacted."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data

from .const import (
    CONF_AGENT_ID,
    CONF_API_KEY,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    OPT_AUTO_EXPOSE_AREAS,
    OPT_CONTROL_ENTITIES,
    OPT_EXCEPTIONS,
    OPT_PUBLIC_URL,
    OPT_READ_ENTITIES,
    OPT_SENSITIVE,
)
from .permissions import missing_keys

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import ElevenAgentsConfigEntry

TO_REDACT = {
    CONF_API_KEY,
    CONF_AGENT_ID,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    OPT_PUBLIC_URL,
    OPT_READ_ENTITIES,
    OPT_CONTROL_ENTITIES,
    OPT_EXCEPTIONS,
    OPT_SENSITIVE,
    OPT_AUTO_EXPOSE_AREAS,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ElevenAgentsConfigEntry
) -> dict[str, Any]:
    """
    Return the entry's configuration and a summary of its state.

    Redact the credentials, the agent id, the public URL and every entity and area name.
    Include no entity state.
    Home Assistant adds the repair issues of this domain by itself.
    """
    options = entry.options
    runtime = getattr(entry, "runtime_data", None)
    catalogue = None if runtime is None else runtime.catalogue

    return {
        "entry": {
            "state": entry.state.value,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(options), TO_REDACT),
        },
        "permissions": {
            "read": len(options.get(OPT_READ_ENTITIES, ())),
            "control": len(options.get(OPT_CONTROL_ENTITIES, ())),
            "restricted": len(options.get(OPT_EXCEPTIONS, {})),
            "missing": len(missing_keys(hass, options)),
        },
        "sync": {
            "catalogue_sent": catalogue is not None,
            "entities": None if catalogue is None else len(catalogue[0]),
        },
    }
