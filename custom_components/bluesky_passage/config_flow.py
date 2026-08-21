"""UI setup and options flow for BlueSky Passage."""

from __future__ import annotations

from urllib.parse import urlparse

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_LINK_NAME,
    CONF_LINK_PASSWORD,
    CONF_MOBILE_NOTIFY_SERVICE,
    CONF_NOTIFICATIONS_ENABLED,
    CONF_PREDICTWIND_URL,
    CONF_STALE_MINUTES,
    CONF_ZONE_NOTIFICATIONS_ENABLED,
    DEFAULT_NOTIFICATIONS_ENABLED,
    DEFAULT_STALE_MINUTES,
    DEFAULT_ZONE_NOTIFICATIONS_ENABLED,
    DOMAIN,
    ENTRY_TITLE,
)
from .feed import (
    FeedAuthenticationError,
    FeedConnectionError,
    FeedLinkError,
    GarminFeedClient,
    normalize_link_name,
)


def normalize_predictwind_url(value: str | None) -> str:
    """Validate an optional PredictWind link without publishing its value."""
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or not (hostname == "predictwind.com" or hostname.endswith(".predictwind.com"))
    ):
        raise ValueError("Use an HTTPS PredictWind URL")
    return cleaned


class BlueSkyPassageConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure one archived MapShare feed."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                link_name = normalize_link_name(user_input[CONF_LINK_NAME])
                password = user_input.get(CONF_LINK_PASSWORD) or None
                predictwind_url = normalize_predictwind_url(
                    user_input.get(CONF_PREDICTWIND_URL)
                )
                await GarminFeedClient(self.hass, link_name, password).async_fetch()
            except ValueError:
                errors[CONF_PREDICTWIND_URL] = "predictwind_url_format"
            except FeedAuthenticationError:
                errors["base"] = "invalid_auth"
            except FeedLinkError:
                errors["base"] = "invalid_link"
            except FeedConnectionError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(link_name.lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=ENTRY_TITLE,
                    data={
                        CONF_LINK_NAME: link_name,
                        CONF_LINK_PASSWORD: password,
                        CONF_PREDICTWIND_URL: predictwind_url,
                    },
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_LINK_NAME): str,
                vol.Optional(CONF_LINK_PASSWORD): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.PASSWORD,
                        autocomplete="current-password",
                    )
                ),
                vol.Optional(CONF_PREDICTWIND_URL): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            reauth_entry = self._get_reauth_entry()
            password = user_input.get(CONF_LINK_PASSWORD) or None
            link_name = reauth_entry.data[CONF_LINK_NAME]
            try:
                await GarminFeedClient(self.hass, link_name, password).async_fetch()
            except FeedAuthenticationError:
                errors["base"] = "invalid_auth"
            except (FeedLinkError, FeedConnectionError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(link_name.lower())
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_LINK_PASSWORD: password},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_LINK_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="current-password",
                        )
                    )
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return BlueSkyPassageOptionsFlow()


class BlueSkyPassageOptionsFlow(config_entries.OptionsFlow):
    """Change alerts without editing YAML."""

    async def async_step_init(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            mobile = user_input.get(CONF_MOBILE_NOTIFY_SERVICE, "").strip()
            if mobile and not mobile.startswith("notify."):
                errors[CONF_MOBILE_NOTIFY_SERVICE] = "notify_service_format"
            else:
                try:
                    predictwind_url = normalize_predictwind_url(
                        user_input.get(CONF_PREDICTWIND_URL)
                    )
                except ValueError:
                    errors[CONF_PREDICTWIND_URL] = "predictwind_url_format"
                else:
                    data = dict(user_input)
                    data[CONF_PREDICTWIND_URL] = predictwind_url
                    return self.async_create_entry(title="", data=data)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_STALE_MINUTES,
                        default=options.get(CONF_STALE_MINUTES, DEFAULT_STALE_MINUTES),
                    ): vol.All(vol.Coerce(int), vol.Range(min=15, max=1440)),
                    vol.Required(
                        CONF_NOTIFICATIONS_ENABLED,
                        default=options.get(
                            CONF_NOTIFICATIONS_ENABLED, DEFAULT_NOTIFICATIONS_ENABLED
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_MOBILE_NOTIFY_SERVICE,
                        default=options.get(CONF_MOBILE_NOTIFY_SERVICE, ""),
                    ): str,
                    vol.Optional(
                        CONF_PREDICTWIND_URL,
                        default=options.get(
                            CONF_PREDICTWIND_URL,
                            self.config_entry.data.get(CONF_PREDICTWIND_URL, ""),
                        ),
                    ): str,
                    vol.Required(
                        CONF_ZONE_NOTIFICATIONS_ENABLED,
                        default=options.get(
                            CONF_ZONE_NOTIFICATIONS_ENABLED,
                            DEFAULT_ZONE_NOTIFICATIONS_ENABLED,
                        ),
                    ): bool,
                }
            ),
            errors=errors,
        )
