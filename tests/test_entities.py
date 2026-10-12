"""Entity smoke test against real panel data; runs only when homeassistant is installed."""

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")
sys.path.insert(0, str(Path(__file__).parents[1]))

from custom_components.urmet_1067 import UrmetCoordinator  # noqa: E402
from custom_components.urmet_1067.alarm_control_panel import UrmetPanel, UrmetZone  # noqa: E402
from custom_components.urmet_1067.binary_sensor import UrmetInput  # noqa: E402
from custom_components.urmet_1067.config_flow import preset_zones  # noqa: E402
from custom_components.urmet_1067.switch import UrmetIsolate  # noqa: E402

DATA = {
    "global": {"A": 1, "F": 0, "I": 0, "O": 1, "X": 1},
    "detailed": {"activezone": [], "sensorstate": [
        {"A": 0, "ID": 1, "O": 0, "X": 1}, {"A": 0, "ID": 13, "O": 1, "X": 0},
        {"A": 1, "ID": 46, "O": 0, "X": 0}]},
    "failures": [],
    "breakings": [{"CAT": 2, "CODE": "Allarme intrusione", "DATE": {"DAY": 6, "HR": 12, "MIN": 21, "MT": 10, "SEC": 17, "YR": 26}, "DT": 1, "ID": 46, "NAME": "Input 46 - MAIN DOOR"}],
}


def make_entry(data, armed_since=None):
    coordinator = UrmetCoordinator.__new__(UrmetCoordinator)
    coordinator.data = data
    coordinator.armed_since = armed_since or {}
    coordinator.sys_info = {"cmodel": "URMET - 1067/052A", "swpver": "2.0.0-27", "cver": "01.04"}
    return SimpleNamespace(
        entry_id="e1",
        runtime_data=coordinator,
        data={
            "host": "1.2.3.4", "pin": "1234", "mac": "00:11:22:33:44:55",
            "zones": {"1": "Zone 1 - OUTSIDE", "2": "Zone 2 - PERIMETER", "3": "Zone 3 - INTERIOR"},
            "sensors": [
                {"id": 1, "name": "Input 1 - FRONT PIR", "zones": [2]},
                {"id": 13, "name": "Input 13 - WINDOW SHUTTER", "zones": [2]},
                {"id": 46, "name": "Input 46 - MAIN DOOR", "zones": [3]},
            ],
        },
        options={"away_zones": [1, 2, 3], "home_zones": [1, 2], "night_zones": [1]},
    )


def test_disarmed_with_alarm_memory():
    entry = make_entry(DATA)
    panel = UrmetPanel(entry)
    assert panel.alarm_state == "disarmed"  # A=1 memory must NOT read as triggered
    attrs = panel.extra_state_attributes
    assert attrs["open_sensors"] == ["Input 13 - WINDOW SHUTTER"]
    assert attrs["isolated_sensors"] == ["Input 1 - FRONT PIR"]
    assert attrs["alarm_memory_sensors"] == ["Input 46 - MAIN DOOR"]
    assert attrs["last_alarm"] == "2026-10-06T12:21:17"
    assert not panel.code_arm_required and panel.code_format is None


def test_armed_states_and_zone_entities():
    armed = {**DATA, "detailed": {**DATA["detailed"], "activezone": [1, 2]}}
    since = {1: datetime(2026, 10, 12, 14, 0), 2: datetime(2026, 10, 12, 14, 0)}
    entry = make_entry(armed, since)
    assert UrmetPanel(entry).alarm_state == "armed_home"
    assert UrmetZone(entry, 1).alarm_state == "armed_away"
    assert UrmetZone(entry, 3).alarm_state == "disarmed"
    # a fresh alarm on sensor 46 (zone 3) after arming zone 3 -> triggered on panel and zone 3 only
    fresh = {**armed, "detailed": {**armed["detailed"], "activezone": [1, 2, 3]},
             "breakings": [{"DATE": {"DAY": 12, "HR": 15, "MIN": 0, "MT": 10, "SEC": 0, "YR": 26}, "ID": 46}]}
    entry = make_entry(fresh, {**since, 3: datetime(2026, 10, 12, 14, 30)})
    assert UrmetPanel(entry).alarm_state == "triggered"
    assert UrmetZone(entry, 3).alarm_state == "triggered"
    assert UrmetZone(entry, 1).alarm_state == "armed_away"


def test_inputs():
    entry = make_entry(DATA)
    sensors = {s["id"]: UrmetInput(entry, s) for s in entry.data["sensors"]}
    assert sensors[13].is_on and not sensors[1].is_on
    assert sensors[1].extra_state_attributes == {"isolated": True, "alarm_memory": False, "zones": ["Zone 2 - PERIMETER"]}
    assert sensors[46].extra_state_attributes["alarm_memory"] is True
    assert sensors[46].device_info["name"] == "Zone 3 - INTERIOR"
    panel = UrmetPanel(entry).device_info
    assert panel["name"] == "Urmet 1067/052A" and panel["model"] == "1067/052A"
    assert panel["sw_version"] == "2.0.0-27" and panel["hw_version"] == "01.04"
    assert ("mac", "00:11:22:33:44:55") in panel["connections"]
    isolate = {s["id"]: UrmetIsolate(entry, s) for s in entry.data["sensors"]}
    assert isolate[1].is_on and not isolate[13].is_on
    assert isolate[1].translation_placeholders == {"name": "Input 1 - FRONT PIR"}


def test_presets_from_shortcuts():
    shortcuts = [{"id": 1, "name": "", "zone": []}, {"id": 2, "name": "", "zone": []},
                 {"id": 3, "name": "Notte", "zone": [1, 2]}, {"id": 4, "name": "PERIMETRALE+INTERNO", "zone": [2, 3]}]
    assert preset_zones(shortcuts) == {"home_zones": [2, 3], "night_zones": [1, 2]}
    assert preset_zones([]) == {"home_zones": [], "night_zones": []}


def test_custom_away_set():
    armed = {**DATA, "detailed": {**DATA["detailed"], "activezone": [1, 2]}}
    entry = make_entry(armed, {1: datetime(2026, 10, 12, 14, 0), 2: datetime(2026, 10, 12, 14, 0)})
    entry.options = {"away_zones": [1, 2], "home_zones": [], "night_zones": []}
    assert UrmetPanel(entry).alarm_state == "armed_away"
    entry.options = {}  # away defaults to all zones
    assert UrmetPanel(entry).alarm_state == "armed_custom_bypass"
