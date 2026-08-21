"""Register the bundled, dependency-free sidebar panel."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    PANEL_COMPONENT,
    PANEL_ICON,
    PANEL_STATIC_URL,
    PANEL_TITLE,
    PANEL_URL_PATH,
    VERSION,
)


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve and register the panel for all authenticated users."""
    panel_file = Path(__file__).parent / "frontend" / "bluesky-passage-panel.js"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(PANEL_STATIC_URL, str(panel_file), cache_headers=True)]
    )
    await panel_custom.async_register_panel(
        hass=hass,
        frontend_url_path=PANEL_URL_PATH,
        config_panel_domain=DOMAIN,
        webcomponent_name=PANEL_COMPONENT,
        module_url=f"{PANEL_STATIC_URL}?v={VERSION}",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        embed_iframe=False,
        require_admin=False,
    )
