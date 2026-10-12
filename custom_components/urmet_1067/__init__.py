"""Urmet 1067 alarm panel integration."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import Urmet1067Api, UrmetBusy, UrmetError
from .const import CONF_MAC, CONF_PIN, CONF_ZONES, DOMAIN, MAX_BUSY_POLLS, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.ALARM_CONTROL_PANEL, Platform.BINARY_SENSOR, Platform.SWITCH]

type UrmetConfigEntry = ConfigEntry[UrmetCoordinator]


class UrmetCoordinator(DataUpdateCoordinator[dict]):
    """Polls the panel once a minute (single-session device, see const.py)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: Urmet1067Api) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=UPDATE_INTERVAL
        )
        self.api = api
        self.sys_info: dict = {}  # unauthenticated /sys/info.cgi, read once at setup
        self.panel_device_id: str | None = None  # registry id of the panel device
        self.armed_since: dict[int, datetime] = {}  # zone id -> naive local arm time
        self._busy = 0

    async def _async_update_data(self) -> dict:
        try:
            data = await self.api.async_get_state()
        except (UrmetBusy, aiohttp.ClientError, asyncio.TimeoutError) as err:
            # Someone else holds the single session, or the web server is hanging
            # (it does, briefly, under load). Keep the last state for a few polls.
            self._busy += 1
            if self.data and self._busy < MAX_BUSY_POLLS:
                _LOGGER.debug("panel unavailable, keeping last state: %s", err)
                return self.data
            raise UpdateFailed(err) from err
        except UrmetError as err:
            raise UpdateFailed(err) from err
        self._busy = 0
        return self.track(data)

    def track(self, data: dict) -> dict:
        """Remember when each zone became armed, for 'triggered' detection."""
        now = dt_util.now().replace(tzinfo=None)
        active = data["detailed"].get("activezone") or []
        self.armed_since = {z: self.armed_since.get(z, now) for z in active}
        return data


def _model(entry: ConfigEntry) -> str:
    """'URMET - 1067/052A' -> '1067/052A'; falls back to '1067'."""
    cmodel = getattr(entry.runtime_data, "sys_info", {}).get("cmodel", "")
    return cmodel.split("-")[-1].strip() or "1067"


def panel_device(entry: ConfigEntry) -> DeviceInfo:
    info = getattr(entry.runtime_data, "sys_info", {})
    mac = entry.data.get(CONF_MAC)
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        connections={(CONNECTION_NETWORK_MAC, mac)} if mac else set(),
        manufacturer="Urmet",
        model=_model(entry),
        name=f"Urmet {_model(entry)}",
        sw_version=info.get("swpver") or None,
        hw_version=info.get("cver") or None,
        configuration_url=f"http://{entry.data[CONF_HOST]}/",
    )


def zone_device(entry: ConfigEntry, zone_id: int) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_zone_{zone_id}")},
        manufacturer="Urmet",
        model=f"{_model(entry)} zone",
        name=entry.data[CONF_ZONES][str(zone_id)],
        via_device_id=getattr(entry.runtime_data, "panel_device_id", None),
    )


async def async_setup_entry(hass: HomeAssistant, entry: UrmetConfigEntry) -> bool:
    api = Urmet1067Api(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_PIN) or None,
    )
    coordinator = UrmetCoordinator(hass, entry, api)
    try:
        coordinator.sys_info = await api.async_sys_info()
    except (UrmetError, aiohttp.ClientError, asyncio.TimeoutError) as err:
        _LOGGER.debug("sys/info unavailable, using generic device info: %s", err)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    coordinator.panel_device_id = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **panel_device(entry)
    ).id
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: UrmetConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
