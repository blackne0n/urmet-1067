"""One binary sensor per panel input (Urmet 'Ingresso'): on = open."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import UrmetConfigEntry, UrmetCoordinator, panel_device, zone_device
from .api import sensor_flags
from .const import CONF_IMPORT_SENSORS, CONF_SENSORS, CONF_ZONES


async def async_setup_entry(
    hass: HomeAssistant, entry: UrmetConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if not entry.data.get(CONF_IMPORT_SENSORS, True):
        return
    async_add_entities(UrmetInput(entry, s) for s in entry.data[CONF_SENSORS])


class UrmetInput(CoordinatorEntity[UrmetCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.OPENING
    _attr_translation_key = "input"

    def __init__(self, entry: UrmetConfigEntry, sensor: dict) -> None:
        super().__init__(entry.runtime_data)
        self._id = sensor["id"]
        self._attr_name = sensor["name"]
        self._attr_unique_id = f"{entry.entry_id}_input_{self._id}"
        zones = [z for z in sensor["zones"] if str(z) in entry.data[CONF_ZONES]]
        self._zone_names = [entry.data[CONF_ZONES][str(z)] for z in zones]
        self._attr_device_info = zone_device(entry, zones[0]) if zones else panel_device(entry)

    @property
    def _flags(self) -> dict:
        return sensor_flags(self.coordinator.data["detailed"], self._id)

    @property
    def is_on(self) -> bool:
        return bool(self._flags.get("O"))

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "isolated": bool(self._flags.get("X")),
            "alarm_memory": bool(self._flags.get("A")),
            "zones": self._zone_names,
        }
