# Urmet 1067 for Home Assistant

<img src="custom_components/urmet_1067/brand/icon.png" width="96" alt="logo" align="right">

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)

Custom integration for Urmet 1067 alarm panels (tested on a 1067/052A with the
`it500web` web server, firmware 2.0.0-27) using the panel's own HTTP/JSON web
interface. No cloud, no extra hardware beyond the panel's Ethernet/web module.

## What you get
* **Alarm panel entity**: *Arm away* / *Arm home* / *Arm night* each arm a
  zone grouping you pick at setup (away defaults to all zones; editable later
  in the integration options).
* **One alarm entity per zone**: arm / disarm a single zone. Any combination
  that matches no preset shows on the main entity as `armed_custom_bypass`.
* **Service `urmet_1067.arm_zones`** on the main entity: arm exactly the listed
  zone ids in one call (zones not listed are disarmed), for automations/scenes.
* **One binary sensor per input** (optional toggle at setup): open/closed, with
  *isolated* (bypassed) and *alarm memory* attributes, plus an **Isolate switch**
  per input (Urmet "Isola" / "Includi"; only created when the user code is
  stored at setup). Inputs are grouped under their zone's device.
* Device page shows the real model (e.g. 1067/052A), web server and firmware
  versions and the MAC address, all read from the panel.
* The groupings for *Arm home* / *Arm night* are prefilled from the panel's
  own named quick commands (e.g. a "Notte" command becomes *Arm night*).
* Attributes on the panel: active zones, open inputs, isolated inputs, alarm
  memory, last alarm, faults.
* English and Italian translations using the Urmet UI wording.

## Install
**HACS**: add this repository as a custom repository (category *Integration*),
install, restart Home Assistant.
**Manual**: copy `custom_components/urmet_1067` into `config/custom_components/`.

Then *Settings → Devices & services → Add integration → Urmet 1067*:
host, the web-server username/password (the same as the browser / My1067 app),
optionally the panel user code (PIN) and the inputs toggle. Next, pick the zones
for *Arm home* / *Arm night*.

## Behaviour and limits
* The panel accepts **one web session at a time**. The integration logs in,
  reads the state and logs out **once a minute**. While you are logged in from
  the app or browser, polls are skipped and the last state is kept; after 5
  consecutive busy polls the entities go unavailable. Conversely, each poll
  holds the session for a few seconds (the panel needs time to sync), so the
  app may refuse to log in if opened in that window: just retry.
* Commands (arm/disarm) need the panel user code: store it at setup, or leave
  it empty and the entity asks for it on every call.
* `triggered` = armed and the newest alarm event is newer than the moment HA
  saw that zone become armed (2 minutes of clock slack).
* Arming is sent as a delta against the live panel state, like the official
  UI: zones to activate and zones to deactivate.

## Logging
Commands (arm/disarm/isolate) and panel availability changes are logged at
INFO; a refused user code at WARNING. *Enable debug logging* on the
integration page adds one line per HTTP request (method, path, result) and the
retry/sync details. Passwords and codes are never logged.

## Protocol notes
Reverse engineered from `spweco_3/js/main-build.js`; see the docstring in
`api.py`. Verified on hardware unless stated otherwise.

* Password is sent as `md5(md5(password))` with any `TRACKID` cookie; if the
  login answer sets a new `TRACKID`, use it from then on.
* `sys/info.cgi` needs no login; its `sesstate` is the auth level of whoever
  holds the single session (-1 free, 1 web login, 4 master code, also from the
  phone app). `sys/netinfo.cgi` (MAC, IPs) and `getshortcuts.cgi` work at web
  login level.
* `detailedstates.cgi` answers NAK for a few seconds after login while the web
  server syncs with the panel, and right after a change it can return garbage
  flag values; the client retries until the data is sane.
* The web server occasionally stops answering for ~1 minute under a burst of
  requests, and the session stays taken until the 120 s idle expiry. The
  integration keeps the last state through this.
* `access.cgi {"code"}` elevates the session to the user's level (4 = master)
  and unlocks `exclude.cgi` / `include.cgi` (verified), `getctime`, `getenable`
  (after `section.cgi {"sid":1}`), `getoutput` (after `{"sid":2}`), `gethistory`.
  Do not call `gethistory` without the code: it hangs the web server for a while.
* `activate.cgi` / `deactivate.cgi {"zone":[ids]}` follow the same pattern but
  are **not yet tested on hardware**.
* The panel's history records who armed/disarmed (`USER`, keypad `NAME`,
  `ZONE` list) and isolation events; not used yet.

## Logo
HACS reads the icon from `custom_components/urmet_1067/brand/`. The Home
Assistant UI itself loads integration images from the central
[brands](https://github.com/home-assistant/brands) repository; the same files
can be submitted there as `custom_integrations/urmet_1067`.

## Development
```
# pure protocol/state tests
uv run --with aiohttp --with pytest pytest tests
# + entity tests against the current Home Assistant release
uv run --python 3.14 --with homeassistant --with pytest pytest tests
# live poll of a real panel (logs in, reads, logs out)
uv run --with aiohttp python custom_components/urmet_1067/api.py HOST USER PASSWORD
```
Minimum Home Assistant: 2026.2 (verified on 2026.2.3 and 2026.10.1).
