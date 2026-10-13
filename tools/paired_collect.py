"""Paired Modbus + Direct Connect capture, read-only, for cross-site register mapping
(franklinwh-modbus#18).

Every ``INTERVAL`` s it records, under one timestamp:
* the Modbus bridge's latest poll (``GET /api/gateways/default/points``: every SunSpec point
  plus raw ``vreg_15000``–``15039``, ``15510``–``15513`` and ``16000``–``16002``). This adds no
  Modbus traffic: the aGate serves one client and the bridge already polls it;
* Direct Connect 1301 power flow (``GET /api/power``) via the Local Bridge.

Every ``SLOW`` s it adds the slower Direct Connect reads: 1405, 1707, 1901, 1903 (``opt:0``);
1703, 1705 and 1835 for each aPower (``id``); 1725 (answers only ``opt:1``, a read).

    MB_URL=... MB_USER=... MB_PASSWORD=... LB_URL=... \\
        python tools/paired_collect.py out.jsonl UNTIL_ISO [INTERVAL] [SLOW] [APOWERS]

The output holds serials and identifiers: run ``tools/redact_capture.py`` before sharing.
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

SLOW_CMDS = ((1405, 0), (1707, 0), (1901, 0), (1903, 0), (1725, 1))
PER_DEVICE = (1703, 1705, 1835)


def http(method, url, body=None, cookie=None, timeout=30):
    req = urllib.request.Request(url, method=method, headers={"Content-Type": "application/json"},
                                 data=None if body is None else json.dumps(body).encode())
    if cookie:
        req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.headers.get("Set-Cookie"), json.load(r)


def main():
    out, until = sys.argv[1], datetime.fromisoformat(sys.argv[2]).timestamp()
    interval = float(sys.argv[3]) if len(sys.argv) > 3 else 10
    slow = float(sys.argv[4]) if len(sys.argv) > 4 else 300
    apowers = int(sys.argv[5]) if len(sys.argv) > 5 else 1
    mb, lb = os.environ["MB_URL"].rstrip("/"), os.environ["LB_URL"].rstrip("/")
    cookie, cookie_at = None, 0.0
    last_slow, n = 0.0, 0
    while time.time() < until:
        t0 = time.time()
        rec = {"ts": t0}
        try:
            if cookie is None or t0 - cookie_at > 3000:
                sc, _ = http("POST", mb + "/api/auth/login",
                             {"username": os.environ["MB_USER"], "password": os.environ["MB_PASSWORD"]})
                cookie, cookie_at = sc.split(";")[0], t0
            _, p = http("GET", mb + "/api/gateways/default/points", cookie=cookie)
            rec["mb_ts"], rec["mb"] = p.get("ts"), p.get("points")
        except Exception as e:                       # noqa: BLE001 - recorded, not fatal
            rec["mb_error"], cookie = repr(e), None
        try:
            _, rec["dc_1301"] = http("GET", lb + "/api/power")
        except Exception as e:                       # noqa: BLE001
            rec["dc_error"] = repr(e)
        if t0 - last_slow >= slow:
            last_slow = t0
            slow_reads = {}
            for cmd, opt in SLOW_CMDS:
                try:
                    _, slow_reads[str(cmd)] = http("POST", f"{lb}/api/call/{cmd}", {"opt": opt, "data": {}})
                except Exception as e:               # noqa: BLE001
                    slow_reads[str(cmd)] = {"error": repr(e)}
            for cmd in PER_DEVICE:
                for dev in range(1, apowers + 1):
                    try:
                        _, slow_reads[f"{cmd}.{dev}"] = http("POST", f"{lb}/api/call/{cmd}",
                                                             {"opt": 0, "data": {"id": dev}})
                    except Exception as e:           # noqa: BLE001
                        slow_reads[f"{cmd}.{dev}"] = {"error": repr(e)}
            rec["dc_slow"] = slow_reads
        rec["dt_read"] = round(time.time() - t0, 2)
        with open(out, "a") as f:
            f.write(json.dumps(rec) + "\n")
        n += 1
        if n % 60 == 1:
            pf = rec.get("dc_1301") or {}
            print(f"{time.strftime('%H:%M')} samples {n} p_fhp {pf.get('p_fhp')} p_sun "
                  f"{pf.get('p_sun')} soc {pf.get('soc')}", flush=True)
        time.sleep(max(0.0, interval - (time.time() - t0)))
    print(f"done {n} samples", flush=True)


if __name__ == "__main__":
    main()
