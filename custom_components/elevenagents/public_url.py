"""Build the webhook URL that ElevenLabs calls."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from homeassistant.components import webhook

from .const import CONF_WEBHOOK_ID, OPT_PUBLIC_URL

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


def normalize_public_url(value: str) -> str | None:
    """
    Return the value as a bare https origin, or None when it is not one.

    A bare origin has a scheme, a host and an optional port. It has no
    path, no query, no fragment and no credentials.
    """
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not parts.hostname:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    host = parts.hostname.lower()
    return f"https://{host}" if port is None else f"https://{host}:{port}"


def webhook_url(hass: HomeAssistant, webhook_id: str, public_url: str | None) -> str:
    """
    Return the webhook URL that ElevenLabs calls.

    Use the public URL when it is set. Use the external URL of Home Assistant
    when it is not. Raise NoURLAvailableError when neither exists.
    """
    if public_url:
        return f"{public_url}{webhook.async_generate_path(webhook_id)}"
    return webhook.async_generate_url(hass, webhook_id, allow_internal=False)


def entry_webhook_url(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return the webhook URL of the entry."""
    return webhook_url(
        hass, entry.data[CONF_WEBHOOK_ID], entry.options.get(OPT_PUBLIC_URL)
    )
