"""BlueSky Passage custom integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .archive import AsyncArchive
from .const import (
    ARCHIVE_DIRECTORY,
    ARCHIVE_FILENAME,
    CONF_LINK_NAME,
    CONF_LINK_PASSWORD,
    DOMAIN,
)
from .coordinator import BlueSkyCoordinator, BlueSkyRuntime
from .feed import GarminFeedClient
from .frontend import async_register_frontend
from .migration import migrated_entry_title
from .notifications import NotificationManager
from .websocket import async_register_websocket_commands

_LOGGER = logging.getLogger(__name__)
PLATFORMS = (Platform.SENSOR, Platform.BINARY_SENSOR, Platform.DEVICE_TRACKER)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Register the panel and API once; no YAML configuration is required."""
    hass.data.setdefault(DOMAIN, {"entries": {}})
    try:
        await async_register_frontend(hass)
    except ValueError as err:
        # A duplicate path indicates a prior copy or stale registration. Keep
        # setup alive so the integration page can expose the problem in logs.
        _LOGGER.error("Could not register BlueSky Passage panel: %s", err)
    async_register_websocket_commands(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Open the archive, poll once, then create entities and alerts."""
    # v2.0.0 generated a local entry title containing the private MapShare
    # share name. Change only that exact generated title; preserve a title the
    # user deliberately customized.
    migrated_title = migrated_entry_title(
        entry.title, entry.data.get(CONF_LINK_NAME)
    )
    if migrated_title != entry.title:
        hass.config_entries.async_update_entry(entry, title=migrated_title)

    archive = AsyncArchive(
        hass, hass.config.path(ARCHIVE_DIRECTORY, ARCHIVE_FILENAME)
    )
    try:
        await archive.async_initialize()
    except Exception as err:
        raise ConfigEntryNotReady(f"Could not initialize the passage archive: {err}") from err

    client = GarminFeedClient(
        hass,
        entry.data[CONF_LINK_NAME],
        entry.data.get(CONF_LINK_PASSWORD),
    )
    coordinator = BlueSkyCoordinator(hass, entry, archive, client)
    await coordinator.async_config_entry_first_refresh()

    runtime = BlueSkyRuntime(hass, entry, archive, coordinator)
    runtime.notifications = NotificationManager(runtime)
    entry.runtime_data = runtime
    hass.data[DOMAIN]["entries"][entry.entry_id] = runtime
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await runtime.async_start()
    await runtime.notifications.async_process()
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime = hass.data[DOMAIN]["entries"].get(entry.entry_id)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        if runtime:
            await runtime.async_stop()
        hass.data[DOMAIN]["entries"].pop(entry.entry_id, None)
    return unloaded
