"""Level-2 (PIN) probe for a real Urmet 1067. Does NOT run from CI.

The PIN is read from the macOS keychain (item 'urmet-pin', account 'ha') and never printed.

  uv run --with aiohttp python scripts/arm_check.py HOST USER PASSWORD            # read-only probes
  uv run --with aiohttp python scripts/arm_check.py HOST USER PASSWORD --zone 1   # arm zone 1, read, disarm it

Read-only mode: access.cgi, filteredstructure, getctime, gethistory, getenable.
--zone N: additionally activate zone N, read detailedstates, deactivate N. Be at home.
A wrong PIN trips the panel's INHIBIT lockout, so check the keychain item first.
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components/urmet_1067"))
import aiohttp  # noqa: E402

from api import Urmet1067Api, _data  # noqa: E402


def keychain_pin() -> str:
    return subprocess.run(
        ["security", "find-generic-password", "-s", "urmet-pin", "-a", "ha", "-w"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def show(label: str, resp) -> None:
    print(f"--- {label}\n{json.dumps(resp, ensure_ascii=False)[:1500]}")


async def main(host: str, user: str, password: str, zone: int | None) -> None:
    pin = keychain_pin()
    async with aiohttp.ClientSession() as session:
        api = Urmet1067Api(session, host, user, password, pin)
        async with api._logged_in():
            show("access", {**(r := await api._post("/spweco/access.cgi", {"code": pin}))})
            if r.get("result") != "ACK":
                return
            show("user/info", await api._get("/user/info.cgi"))
            for ep in ("filteredstructure", "getctime", "getenable"):
                show(ep, await api._get(f"/spweco/{ep}.cgi"))
            show("gethistory", await api._post("/spweco/gethistory.cgi", {"event": 0, "period": 1}))
            show("getctime (POST)", await api._post("/spweco/getctime.cgi", {}))
            if zone is None:
                return
            try:
                show("activate", await api._post("/spweco/activate.cgi", {"zone": [zone]}))
                await asyncio.sleep(3)
                show("detailedstates", _data(await api._get("/spweco/detailedstates.cgi"), "detailedstates"))
                show("globalstates", await api._get("/spweco/globalstates.cgi"))
            finally:
                show("deactivate", await api._post("/spweco/deactivate.cgi", {"zone": [zone]}))
                await asyncio.sleep(2)
                show("detailedstates", _data(await api._get("/spweco/detailedstates.cgi"), "detailedstates"))


if __name__ == "__main__":
    args = sys.argv[1:]
    z = int(args[args.index("--zone") + 1]) if "--zone" in args else None
    asyncio.run(main(args[0], args[1], args[2], z))
