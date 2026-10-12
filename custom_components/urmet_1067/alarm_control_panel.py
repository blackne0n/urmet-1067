"""Alarm control panel entities for Urmet 1067: one for the panel, one per zone."""

from __future__ import annotations

import asyncio

import aiohttp
import voluptuous as vol

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback, async_get_current_platform
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import UrmetConfigEntry, UrmetCoordinator, panel_device, zone_device
from .api import UrmetError, is_triggered, latest_breaking, map_state
from .const import (
    CLOCK_SLACK,
    CONF_AWAY_ZONES,
    CONF_HOME_ZONES,
    CONF_NIGHT_ZONES,
    CONF_PIN,
    CONF_SENSORS,
    CONF_ZONES,
    DOMAIN,
)

SERVICE_ARM_ZONES = "arm_zones"


async def async_setup_entry(
    hass: HomeAssistant, entry: UrmetConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    entities: list[UrmetAlarmBase] = [UrmetPanel(entry)]
    entities += [UrmetZone(entry, int(z)) for z in entry.data[CONF_ZONES]]
    async_add_entities(entities)
    async_get_current_platform().async_register_entity_service(
        SERVICE_ARM_ZONES,
        {
            vol.Required("zones"): vol.All(cv.ensure_list, [cv.positive_int]),
            vol.Optional("code"): cv.string,
        },
        "async_arm_zones",
    )


class UrmetAlarmBase(CoordinatorEntity[UrmetCoordinator], AlarmControlPanelEntity):
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: UrmetConfigEntry, zones: set[int]) -> None:
        super().__init__(entry.runtime_data)
        self._zone_names: dict[int, str] = {int(k): v for k, v in entry.data[CONF_ZONES].items()}
        self._zones = zones  # zones this entity controls
        self._sensors = [s for s in entry.data[CONF_SENSORS] if set(s["zones"]) & zones]
        self._sensor_ids = {s["id"] for s in self._sensors}
        has_pin = bool(entry.data.get(CONF_PIN))
        self._attr_code_arm_required = not has_pin
        self._attr_code_format = None if has_pin else CodeFormat.NUMBER

    @property
    def _active(self) -> set[int]:
        return set(self.coordinator.data["detailed"].get("activezone") or [])

    def _triggered(self) -> bool:
        since = {z: t for z, t in self.coordinator.armed_since.items() if z in self._zones}
        return is_triggered(self.coordinator.data["breakings"], since, self._sensor_ids, CLOCK_SLACK)

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        flags = {s["ID"]: s for s in data["detailed"].get("sensorstate") or []}
        names = {s["id"]: s["name"] for s in self._sensors}

        def named(flag: str) -> list[str]:
            return [names[i] for i in names if flags.get(i, {}).get(flag)]

        last = latest_breaking(data["breakings"], self._sensor_ids)
        return {
            "active_zones": [self._zone_names[z] for z in sorted(self._active & self._zones)],
            "open_sensors": named("O"),
            "isolated_sensors": named("X"),
            "alarm_memory_sensors": named("A"),
            "last_alarm": last.isoformat() if last else None,
        }

    async def _set(self, target: set[int], code: str | None) -> None:
        try:
            data = await self.coordinator.api.async_set_zones(target, code)
        except (UrmetError, aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HomeAssistantError(f"Urmet 1067 command failed: {err}") from err
        self.coordinator.async_set_updated_data(self.coordinator.track(data))

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._set(self._active - self._zones, code)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._set(self._active | self._zones, code)


class UrmetPanel(UrmetAlarmBase):
    """Whole system: away = all zones, home/night = configured groupings."""

    _attr_translation_key = "panel"

    def __init__(self, entry: UrmetConfigEntry) -> None:
        super().__init__(entry, {int(z) for z in entry.data[CONF_ZONES]})
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = panel_device(entry)
        self._away = set(entry.options.get(CONF_AWAY_ZONES) or self._zones)
        self._home = set(entry.options.get(CONF_HOME_ZONES, []))
        self._night = set(entry.options.get(CONF_NIGHT_ZONES, []))
        features = AlarmControlPanelEntityFeature.ARM_AWAY
        if self._home:
            features |= AlarmControlPanelEntityFeature.ARM_HOME
        if self._night:
            features |= AlarmControlPanelEntityFeature.ARM_NIGHT
        self._attr_supported_features = features

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        state = map_state(self.coordinator.data["detailed"], self._away, self._home, self._night)
        if state != "disarmed" and self._triggered():
            return AlarmControlPanelState.TRIGGERED
        return AlarmControlPanelState(state)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            **super().extra_state_attributes,
            "failures": self.coordinator.data["failures"],
            "global_flags": self.coordinator.data["global"],
        }

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._set(self._away, code)

    async def async_arm_zones(self, zones: list[int], code: str | None = None) -> None:
        """Service: make exactly these zones armed (empty list = disarm all)."""
        unknown = set(zones) - self._zones
        if unknown:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_zone",
                translation_placeholders={"zones": ", ".join(map(str, sorted(unknown)))},
            )
        await self._set(set(zones), code)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._set(self._home, code)

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._set(self._night, code)


class UrmetZone(UrmetAlarmBase):
    """One panel zone: arm away / disarm just that zone."""

    _attr_translation_key = "zone"
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY

    def __init__(self, entry: UrmetConfigEntry, zone_id: int) -> None:
        super().__init__(entry, {zone_id})
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone_id}"
        self._attr_device_info = zone_device(entry, zone_id)

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        if not self._zones & self._active:
            return AlarmControlPanelState.DISARMED
        if self._triggered():
            return AlarmControlPanelState.TRIGGERED
        return AlarmControlPanelState.ARMED_AWAY
