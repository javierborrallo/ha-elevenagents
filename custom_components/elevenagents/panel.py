"""Registration of the configuration panel."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.components import panel_custom
from homeassistant.components.http import StaticPathConfig

from .const import PANEL_URL_PATH

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

STATIC_PATH = "/elevenagents_panel"


def asset_version(directory: Path) -> str:
    """Return a path segment that changes when a top-level .js file changes."""
    newest = max(path.stat().st_mtime_ns for path in directory.glob("*.js"))
    return f"v{newest}"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Serve the panel files and register the panel itself."""
    directory = Path(__file__).parent / "panel"
    version = await hass.async_add_executor_job(asset_version, directory)
    served_at = f"{STATIC_PATH}/{version}"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(served_at, str(directory), cache_headers=True)]
    )
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name="elevenagents-panel",
        module_url=f"{served_at}/panel.js",
        require_admin=True,
        config_panel_domain="elevenagents",
    )
