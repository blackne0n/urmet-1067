"""One switch per input: on = isolated (Urmet 'Isola' / exclude.cgi), off = included."""

from __future__ import annotations

import asyncio

import aiohttp

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import UrmetConfigEntry, UrmetCoordinator, panel_device, zone_device
from .api import UrmetError, sensor_flags
from .const import CONF_IMPORT_SENSORS, CONF_PIN, CONF_SENSORS, CONF_ZONES


async def async_setup_entry(
    hass: HomeAssistant, entry: UrmetConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if not entry.data.get(CONF_IMPORT_SENSORS, True) or not entry.data.get(CONF_PIN):
        return  # isolating needs the user code; no prompt path for switches
    async_add_entities(UrmetIsolate(entry, s) for s in entry.data[CONF_SENSORS])


class UrmetIsolate(CoordinatorEntity[UrmetCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "isolate"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:shield-off-outline"

    def __init__(self, entry: UrmetConfigEntry, sensor: dict) -> None:
        super().__init__(entry.runtime_data)
        self._id = sensor["id"]
        self._attr_unique_id = f"{entry.entry_id}_isolate_{self._id}"
        self._attr_translation_placeholders = {"name": sensor["name"]}
        zones = [z for z in sensor["zones"] if str(z) in entry.data[CONF_ZONES]]
        self._attr_device_info = zone_device(entry, zones[0]) if zones else panel_device(entry)

    @property
    def is_on(self) -> bool:
        return bool(sensor_flags(self.coordinator.data["detailed"], self._id).get("X"))

    async def _set(self, isolate: bool) -> None:
        try:
            data = await self.coordinator.api.async_isolate(self._id, isolate)
        except (UrmetError, aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HomeAssistantError(f"Urmet 1067 command failed: {err}") from err
        self.coordinator.async_set_updated_data(self.coordinator.track(data))

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)
