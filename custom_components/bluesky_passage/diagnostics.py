"""Redacted diagnostics that never disclose positions or messages."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_MOBILE_NOTIFY_SERVICE,
    CONF_PREDICTWIND_URL,
    CONF_XWEATHER_CLIENT_ID,
    CONF_XWEATHER_CLIENT_SECRET,
    DOMAIN,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    runtime = hass.data[DOMAIN]["entries"][entry.entry_id]
    state = await runtime.async_state()
    archive = state.get("archive", {})
    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "has_password": bool(entry.data.get("link_password")),
            "options": {
                key: value
                for key, value in entry.options.items()
                if key
                not in {
                    CONF_MOBILE_NOTIFY_SERVICE,
                    CONF_PREDICTWIND_URL,
                    CONF_XWEATHER_CLIENT_ID,
                    CONF_XWEATHER_CLIENT_SECRET,
                }
            },
            "has_mobile_notify_service": bool(
                entry.options.get(CONF_MOBILE_NOTIFY_SERVICE)
            ),
            "has_predictwind_url": bool(
                entry.options.get(
                    CONF_PREDICTWIND_URL,
                    entry.data.get(CONF_PREDICTWIND_URL),
                )
            ),
            "has_xweather_client_id": bool(
                entry.options.get(
                    CONF_XWEATHER_CLIENT_ID,
                    entry.data.get(CONF_XWEATHER_CLIENT_ID),
                )
            ),
            "has_xweather_client_secret": bool(
                entry.options.get(
                    CONF_XWEATHER_CLIENT_SECRET,
                    entry.data.get(CONF_XWEATHER_CLIENT_SECRET),
                )
            ),
        },
        "runtime": {
            "integration_version": state.get("runtime", {}).get(
                "integration_version"
            ),
            "status": state.get("runtime", {}).get("status"),
            "source_available": state.get("runtime", {}).get("source_available"),
            "consecutive_failures": state.get("runtime", {}).get(
                "consecutive_failures"
            ),
            "last_poll_attempt_utc": state.get("runtime", {}).get(
                "last_poll_attempt_utc"
            ),
            "last_poll_success_utc": state.get("runtime", {}).get(
                "last_poll_success_utc"
            ),
        },
        "archive": archive,
        "passage_count": len(state.get("passages") or []),
        "weather": {
            "configured": state.get("weather", {}).get("configured"),
            "stored_samples": state.get("weather", {}).get("stored_samples"),
            "last_error": state.get("weather", {}).get("last_error"),
        },
    }
