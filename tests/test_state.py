"""State mapping checks against JSON captured from a real 1067/052A."""

import importlib.util
from datetime import datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "api", Path(__file__).parents[1] / "custom_components/urmet_1067/api.py"
)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)

ALL, HOME, NIGHT = {1, 2, 3}, {1, 2}, {1}
BREAKINGS = [{"CAT": 2, "CODE": "Allarme intrusione", "DATE": {"DAY": 6, "HR": 12, "MIN": 21, "MT": 10, "SEC": 17, "YR": 26}, "DT": 1, "ID": 46, "NAME": "Input 46"}]


def test_password_hash():
    assert api.hash_password("password") == api.hash_password("password")
    assert api.hash_password("password") == "696d29e0940a4957748fe3fc9efd22a3"


def test_map_state():
    assert api.map_state({"activezone": []}, ALL, HOME, NIGHT) == "disarmed"
    assert api.map_state({}, ALL, HOME, NIGHT) == "disarmed"
    assert api.map_state({"activezone": [1, 2, 3]}, ALL, HOME, NIGHT) == "armed_away"
    assert api.map_state({"activezone": [2, 1]}, ALL, HOME, NIGHT) == "armed_home"
    assert api.map_state({"activezone": [1]}, ALL, HOME, NIGHT) == "armed_night"
    assert api.map_state({"activezone": [3]}, ALL, HOME, NIGHT) == "armed_custom_bypass"
    assert api.map_state({"activezone": [1]}, ALL, set(), set()) == "armed_custom_bypass"
    assert api.map_state({"activezone": [3]}, ALL, HOME, NIGHT, {3}) == "armed_vacation"


def test_breakings():
    assert api.latest_breaking(BREAKINGS) == datetime(2026, 10, 6, 12, 21, 17)
    assert api.latest_breaking([]) is None


def test_triggered_and_flags():
    from datetime import timedelta
    slack = timedelta(minutes=2)
    old = {"DATE": {"DAY": 6, "HR": 12, "MIN": 21, "MT": 10, "SEC": 17, "YR": 26}, "ID": 46}
    new = {"DATE": {"DAY": 12, "HR": 15, "MIN": 0, "MT": 10, "SEC": 0, "YR": 26}, "ID": 46}
    armed = {1: datetime(2026, 10, 12, 14, 0, 0), 3: datetime(2026, 10, 12, 14, 30, 0)}
    assert not api.is_triggered([old], armed, None, slack)  # memory from before arming
    assert api.is_triggered([new], armed, None, slack)
    assert api.is_triggered([new], armed, {46}, slack)
    assert not api.is_triggered([new], armed, {1, 2}, slack)  # other zone's sensors
    assert not api.is_triggered([new], {}, None, slack)  # disarmed
    # panel clock 1 min ahead of HA: still counts thanks to slack
    assert api.is_triggered([new], {1: datetime(2026, 10, 12, 15, 1, 0)}, None, slack)
    det = {"sensorstate": [{"A": 0, "ID": 13, "O": 1, "X": 0}]}
    assert api.sensor_flags(det, 13)["O"] == 1
    assert api.sensor_flags(det, 99) == {}


def test_is_sane():
    assert api.is_sane({"sensorstate": [{"A": 0, "ID": 13, "O": 1, "X": 1}]})
    assert api.is_sane({})
    assert not api.is_sane({"sensorstate": [{"A": 1193383220, "ID": 13, "O": 857396, "X": 1}]})
