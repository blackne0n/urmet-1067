"""Config flow for Urmet 1067."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import Urmet1067Api, UrmetBusy, UrmetError
from .const import (
    CONF_AWAY_ZONES,
    CONF_HOME_ZONES,
    CONF_IMPORT_SENSORS,
    CONF_MAC,
    CONF_NIGHT_ZONES,
    CONF_PIN,
    CONF_SENSORS,
    CONF_VACATION_ZONES,
    CONF_ZONE_PANELS,
    CONF_ZONES,
    DOMAIN,
)

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): PASSWORD,
        vol.Optional(CONF_PIN, default=""): PASSWORD,
        vol.Optional(CONF_IMPORT_SENSORS, default=True): bool,
    }
)


def zones_schema(zones: dict[str, str], current: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_AWAY_ZONES,
                default=[str(z) for z in current.get(CONF_AWAY_ZONES) or map(int, zones)],
            ): cv.multi_select(zones),
            vol.Optional(
                CONF_HOME_ZONES, default=[str(z) for z in current.get(CONF_HOME_ZONES, [])]
            ): cv.multi_select(zones),
            vol.Optional(
                CONF_NIGHT_ZONES, default=[str(z) for z in current.get(CONF_NIGHT_ZONES, [])]
            ): cv.multi_select(zones),
            vol.Optional(
                CONF_VACATION_ZONES,
                default=[str(z) for z in current.get(CONF_VACATION_ZONES, [])],
            ): cv.multi_select(zones),
            vol.Optional(CONF_ZONE_PANELS, default=current.get(CONF_ZONE_PANELS, True)): bool,
        }
    )


def preset_zones(shortcuts: list) -> dict:
    """Prefill home/night from the panel's named quick commands (ids 1-2 are total off/on)."""
    named = [s for s in shortcuts if s.get("name") and s.get("zone")]
    night = next((s for s in named if "nott" in s["name"].lower() or "night" in s["name"].lower()), None)
    home = next((s for s in named if s is not night), None)
    return {
        CONF_HOME_ZONES: home["zone"] if home else [],
        CONF_NIGHT_ZONES: night["zone"] if night else [],
    }


def _to_ids(user_input: dict) -> dict:
    return {
        CONF_AWAY_ZONES: [int(z) for z in user_input.get(CONF_AWAY_ZONES, [])],
        CONF_HOME_ZONES: [int(z) for z in user_input.get(CONF_HOME_ZONES, [])],
        CONF_NIGHT_ZONES: [int(z) for z in user_input.get(CONF_NIGHT_ZONES, [])],
        CONF_VACATION_ZONES: [int(z) for z in user_input.get(CONF_VACATION_ZONES, [])],
        CONF_ZONE_PANELS: user_input.get(CONF_ZONE_PANELS, True),
    }


class UrmetConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._presets: dict = {}

    async def _async_validate(self, user_input: dict) -> tuple[dict, dict[str, str]]:
        """Log in and read the panel structure; returns (setup info, errors)."""
        api = Urmet1067Api(
            async_get_clientsession(self.hass),
            user_input[CONF_HOST],
            user_input[CONF_USERNAME],
            user_input[CONF_PASSWORD],
        )
        try:
            return await api.async_get_setup_info(), {}
        except UrmetBusy:
            info = await api.async_sys_info()
            return {}, {"base": "busy" if info.get("sesstate") == 1 else "invalid_auth"}
        except (UrmetError, aiohttp.ClientError, asyncio.TimeoutError):
            return {}, {"base": "cannot_connect"}

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()
            info, errors = await self._async_validate(user_input)
            if not errors:
                structure = info["structure"]
                self._data = dict(user_input)
                self._data[CONF_MAC] = info["netinfo"].get("macadd") or ""
                self._presets = preset_zones(info["shortcuts"])
                self._data[CONF_ZONES] = {
                    str(z["NID"]): z["NAME"] or f"Zona {z['NID']}" for z in structure["cfgzone"]
                }
                self._data[CONF_SENSORS] = [
                    {"id": s["NID"], "name": s["NAME"] or f"Ingresso {s['NID']}", "zones": s["PID"]}
                    for s in structure["cfgsensor"]
                ]
                return await self.async_step_zones()
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_reconfigure(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Change host, credentials, PIN or input import after setup."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            if (other := self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, user_input[CONF_HOST]
            )) and other.entry_id != entry.entry_id:
                return self.async_abort(reason="already_configured")
            _, errors = await self._async_validate(user_input)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, unique_id=user_input[CONF_HOST], data_updates=user_input
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input or entry.data),
            errors=errors,
        )

    async def async_step_zones(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=f"Urmet 1067 ({self._data[CONF_HOST]})",
                data=self._data,
                options=_to_ids(user_input),
            )
        return self.async_show_form(
            step_id="zones", data_schema=zones_schema(self._data[CONF_ZONES], self._presets)
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return UrmetOptionsFlow()


class UrmetOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=_to_ids(user_input))
        return self.async_show_form(
            step_id="init",
            data_schema=zones_schema(self.config_entry.data[CONF_ZONES], self.config_entry.options),
        )
