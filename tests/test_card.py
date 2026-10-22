"""Tests for the registration of the Lovelace card."""

from __future__ import annotations

import pytest
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL

from custom_components.elevenagents.card import CARD_MODULE, CARD_STATIC_PATH

# homeassistant 2026.9.1's http server sets self.app["hass"] as a plain key.
# aiohttp 3.14.3 raises NotAppKeyWarning for that pattern. pyproject.toml
# turns every warning into an error, so this module ignores this one category.
pytestmark = pytest.mark.filterwarnings(
    "ignore::aiohttp.web_exceptions.NotAppKeyWarning"
)


def _card_url(hass) -> str:
    urls = [
        url
        for url in hass.data[DATA_EXTRA_MODULE_URL].urls
        if url.startswith(f"{CARD_STATIC_PATH}/v")
    ]
    assert len(urls) == 1
    return str(urls[0])


async def test_setup_loads_the_card_in_every_dashboard(hass, setup_entry):
    """The card module is an extra frontend module, under a versioned path."""
    assert _card_url(hass).endswith(f"/{CARD_MODULE}")


async def test_the_card_module_is_served(hass, setup_entry, hass_client):
    """The module answers 200 and defines the custom element."""
    client = await hass_client()
    response = await client.get(_card_url(hass))

    assert response.status == 200
    assert 'customElements.define("elevenagents-card"' in await response.text()


async def test_the_sdk_is_served_beside_the_card(hass, setup_entry, hass_client):
    """The pinned SDK is served from the same versioned path."""
    base = _card_url(hass).rsplit("/", 1)[0]
    client = await hass_client()
    response = await client.get(f"{base}/vendor/elevenlabs-client-1.30.0.iife.js")

    assert response.status == 200
    assert (await response.text()).startswith("var ElevenLabsClient")
