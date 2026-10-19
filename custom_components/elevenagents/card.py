"""Registration of the Lovelace card."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig

from .panel import asset_version

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

CARD_STATIC_PATH = "/elevenagents_card"
CARD_MODULE = "elevenagents-card.js"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card files and load the card module in every dashboard."""
    directory = Path(__file__).parent / "card"
    version = await hass.async_add_executor_job(asset_version, directory)
    served_at = f"{CARD_STATIC_PATH}/{version}"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(served_at, str(directory), cache_headers=True)]
    )
    frontend.add_extra_js_url(hass, f"{served_at}/{CARD_MODULE}")
