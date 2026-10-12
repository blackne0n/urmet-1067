"""Minimal client for the Urmet 1067 web server (it500web backend).

Protocol (reverse engineered from spweco_3/js/main-build.js):
  * every request carries a TRACKID cookie; login.cgi answers with a NEW TRACKID
    that must be used for the rest of the session (some endpoints, e.g.
    detailedstates, NAK on the pre-login id)
  * POST /user/login.cgi  {"user": u, "pwd": md5(md5(password))}  -> authlevel 1
  * POST /spweco/access.cgi {"code": PIN}                         -> authlevel 2
  * GET  /spweco/globalstates.cgi / detailedstates.cgi / getfailures.cgi / getbreakings.cgi
  * POST /spweco/activate.cgi | deactivate.cgi {"zone": [ids]}   (needs authlevel 2)
  * POST /user/logout.cgi {}
Only one session may exist at a time, so every public call logs in and out.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import hashlib
import logging
import uuid

import aiohttp

_LOGGER = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=15)
READ_RETRIES = 6  # detailedstates syncs from the panel bus a few seconds after login
READ_RETRY_DELAY = 2


class UrmetError(Exception):
    """Base error."""


class UrmetBusy(UrmetError):
    """Login refused: wrong credentials or another user holds the session."""


class UrmetAuthError(UrmetError):
    """PIN missing or refused."""


def hash_password(password: str) -> str:
    """The web UI sends md5(md5(password)) as hex."""
    inner = hashlib.md5(password.encode()).hexdigest()
    return hashlib.md5(inner.encode()).hexdigest()


def _data(resp: dict, what: str) -> dict | list:
    if resp.get("result") != "ACK":
        raise UrmetError(f"{what} returned {resp}")
    return resp.get("data", {})


def parse_date(d: dict) -> datetime:
    """Panel DATE dict -> naive local datetime."""
    return datetime(2000 + d["YR"], d["MT"], d["DAY"], d["HR"], d["MIN"], d["SEC"])


def map_state(detailed: dict, away: set[int], home: set[int], night: set[int]) -> str:
    """Return the HA alarm state name derived from detailedstates."""
    active = set(detailed.get("activezone") or [])
    if not active:
        return "disarmed"
    if active == away:
        return "armed_away"
    if home and active == home:
        return "armed_home"
    if night and active == night:
        return "armed_night"
    return "armed_custom_bypass"


def latest_breaking(breakings: list, sensor_ids: set[int] | None = None) -> datetime | None:
    """Newest alarm event, optionally restricted to the given sensor ids."""
    dates = [
        parse_date(b["DATE"])
        for b in breakings
        if "DATE" in b and (sensor_ids is None or b.get("ID") in sensor_ids)
    ]
    return max(dates) if dates else None


def is_triggered(
    breakings: list,
    armed_since: dict[int, datetime],
    sensor_ids: set[int] | None,
    slack: timedelta,
) -> bool:
    """True when an alarm event is newer than the earliest arming time."""
    if not armed_since:
        return False
    last = latest_breaking(breakings, sensor_ids)
    return last is not None and last > min(armed_since.values()) - slack


def is_sane(detailed: dict) -> bool:
    """Right after a change the panel may report garbage flag values (e.g. O=857396)."""
    return all(
        s.get(f, 0) in (0, 1)
        for s in detailed.get("sensorstate") or []
        for f in ("A", "O", "X")
    )


def sensor_flags(detailed: dict, sensor_id: int) -> dict:
    """Flags for one input; the panel omits inputs whose flags are all zero."""
    return next((s for s in detailed.get("sensorstate") or [] if s.get("ID") == sensor_id), {})


class Urmet1067Api:
    """One instance per panel. All calls are serialized and self-contained."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
        pin: str | None = None,
    ) -> None:
        self._session = session
        self._base = f"http://{host}"
        self._user = username
        self._pwd = hash_password(password)
        self._pin = pin
        # Our own cookie: aiohttp's jar drops cookies set by bare-IP hosts anyway.
        self._headers = {"Cookie": f"TRACKID={uuid.uuid4().hex}"}
        self._lock = asyncio.Lock()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        async with self._session.request(
            method, self._base + path, headers=self._headers, timeout=TIMEOUT, **kwargs
        ) as resp:
            if resp.status == 404:  # logout without a session answers 404
                _LOGGER.debug("%s %s -> 404 (no session)", method, path)
                return {"result": "NAK"}
            if "TRACKID" in resp.cookies:  # login rotates the session id
                self._headers["Cookie"] = f"TRACKID={resp.cookies['TRACKID'].value}"
            body = await resp.json(content_type=None) or {}
            # never log request payloads: login/access carry the password hash / PIN
            _LOGGER.debug("%s %s -> %s %s", method, path, resp.status, body.get("result", body))
            return body

    async def _get(self, path: str, tries: int = 1) -> dict:
        """GET; some endpoints (detailedstates) NAK for a few seconds after login."""
        for i in range(tries):
            resp = await self._request("GET", path)
            if resp.get("result") == "ACK" or i == tries - 1:
                return resp
            await asyncio.sleep(READ_RETRY_DELAY)
        return resp

    async def _post(self, path: str, data: dict) -> dict:
        return await self._request("POST", path, json=data)

    @asynccontextmanager
    async def _logged_in(self):
        async with self._lock:
            resp = await self._post(
                "/user/login.cgi", {"user": self._user, "pwd": self._pwd}
            )
            if resp.get("result") != "ACK" or resp.get("data", {}).get("authlevel", -1) < 1:
                raise UrmetBusy("login refused (bad credentials or another user connected)")
            _LOGGER.debug("logged in, authlevel %s", resp["data"].get("authlevel"))
            try:
                yield
            finally:
                try:
                    await self._post("/user/logout.cgi", {})
                except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                    _LOGGER.debug("logout failed: %s", err)

    async def async_sys_info(self) -> dict:
        """Unauthenticated panel info (model, sesstate: 1 = session in use)."""
        return _data(await self._get("/sys/info.cgi"), "sysinfo")

    async def async_get_setup_info(self) -> dict:
        """Structure, network info (MAC) and quick-command presets, in one session."""
        async with self._logged_in():
            return {
                "structure": _data(await self._get("/spweco/plantstructure.cgi"), "plantstructure"),
                "netinfo": (await self._get("/sys/netinfo.cgi")).get("data", {}),
                "shortcuts": (await self._get("/spweco/getshortcuts.cgi")).get("data", []),
            }

    async def _detailed(self) -> dict:
        for _ in range(READ_RETRIES):
            detailed = _data(
                await self._get("/spweco/detailedstates.cgi", READ_RETRIES), "detailedstates"
            )
            if is_sane(detailed):
                return detailed
            _LOGGER.debug("detailedstates not synced yet: %s", detailed)
            await asyncio.sleep(READ_RETRY_DELAY)
        raise UrmetError("detailedstates kept returning garbage flags")

    async def _read_state(self) -> dict:
        return {
            "global": _data(await self._get("/spweco/globalstates.cgi"), "globalstates"),
            "detailed": await self._detailed(),
            "failures": _data(await self._get("/spweco/getfailures.cgi"), "getfailures"),
            "breakings": _data(await self._get("/spweco/getbreakings.cgi"), "getbreakings"),
        }

    async def async_get_state(self) -> dict:
        async with self._logged_in():
            return await self._read_state()

    @asynccontextmanager
    async def _as_user(self, code: str | None):
        """Logged-in session elevated with the panel user code (authlevel >= 2)."""
        pin = code or self._pin
        if not pin:
            raise UrmetAuthError("a PIN is required for this command")
        async with self._logged_in():
            resp = await self._post("/spweco/access.cgi", {"code": pin})
            if resp.get("result") != "ACK":
                _LOGGER.warning("panel refused the user code: %s", resp.get("info") or resp)
                raise UrmetAuthError(f"PIN refused: {resp.get('info')}")
            _LOGGER.debug("user code accepted, authlevel %s", resp.get("data", {}).get("authlevel"))
            yield

    async def _read_state_until(self, done) -> dict:
        """Wait until `done(detailed)` holds (the panel reports changes a moment later).

        ponytail: keep the request burst small; the web server hangs when hammered.
        """
        for i in range(READ_RETRIES):
            await asyncio.sleep(READ_RETRY_DELAY)
            if done(await self._detailed()):
                break
            _LOGGER.debug("panel has not reported the change yet (%s/%s)", i + 1, READ_RETRIES)
        else:
            _LOGGER.warning("panel accepted the command but did not report the new state")
        return await self._read_state()

    async def async_isolate(self, sensor_id: int, isolate: bool) -> dict:
        """Isolate (exclude) or include one input; returns fresh state."""
        async with self._as_user(None):
            ep = "exclude" if isolate else "include"
            _LOGGER.info("%s input %s", "isolating" if isolate else "including", sensor_id)
            _data(await self._post(f"/spweco/{ep}.cgi", {"sensor": [sensor_id]}), ep)
            return await self._read_state_until(
                lambda d: bool(sensor_flags(d, sensor_id).get("X")) == isolate
            )

    async def async_set_zones(self, target: set[int], code: str | None = None) -> dict:
        """Make exactly `target` zones armed (delta vs live state); returns fresh state."""
        async with self._as_user(code):
            active = set((await self._detailed()).get("activezone") or [])
            off = sorted(active - target)
            on = sorted(target - active)
            _LOGGER.info(
                "arm request: target zones %s, active %s -> activate %s, deactivate %s",
                sorted(target), sorted(active), on, off,
            )
            if off:
                _data(await self._post("/spweco/deactivate.cgi", {"zone": off}), "deactivate")
            if on:
                _data(await self._post("/spweco/activate.cgi", {"zone": on}), "activate")
            if not on and not off:
                return await self._read_state()
            return await self._read_state_until(
                lambda d: set(d.get("activezone") or []) == target
            )


if __name__ == "__main__":  # live check: python api.py HOST USER PASSWORD
    import json
    import sys

    async def _main() -> None:
        async with aiohttp.ClientSession() as session:
            api = Urmet1067Api(session, *sys.argv[1:4])
            print(json.dumps(await api.async_sys_info()))
            info = await api.async_get_setup_info()
            print("zones:", info["structure"]["cfgzone"])
            print("mac:", info["netinfo"].get("macadd"), "shortcuts:", info["shortcuts"])
            print(json.dumps(await api.async_get_state(), ensure_ascii=False))

    asyncio.run(_main())
