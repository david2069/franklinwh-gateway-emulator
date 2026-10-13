"""Scheduled force-charge test via the Modbus bridge's command API, with guards.

Plan: list of (local HH:MM, watts or 0=release, label). Before every force step:
grid connected, SoC < max_soc, no unexpected dispatch, previous request OK.
Every force step sets the bridge's watchdog (battery_command_duration) so the bridge
releases by itself if this script dies. Always releases at the end / on error.

    MB_URL=http://localhost:8100 LB_URL=http://localhost:8101 MB_USER=... MB_PASSWORD=... \
        python tools/charge_test.py 2026-10-08 85 \
        '[["05:30",1000,"stair 1 kW"],["05:40",2500,"stair 2.5 kW"],["06:00",0,"end"]]'

The bridge sends SunSpec Model 704 WSetEna/WSetPct, which a write-locked aGate obeys
(docs/CALIBRATION.md, 2026-10-08 staircase). Times are in ``SITE_TZ`` (default
Australia/Sydney). A slow or stalled Local Bridge read no longer delays a step: reads time out
after 10 s and the step fires on time.
"""
import json, os, sys, time, urllib.error, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("SITE_TZ", "Australia/Sydney"))
MB = os.environ["MB_URL"].rstrip("/")            # Modbus bridge
LB = os.environ["LB_URL"].rstrip("/")            # Local Bridge (read-only telemetry)
USER, PASSWORD = os.environ["MB_USER"], os.environ["MB_PASSWORD"]
DAY = sys.argv[1]                                # YYYY-MM-DD
MAX_SOC = float(sys.argv[2])                     # skip/abort a block at or above this SoC
PLAN = json.loads(sys.argv[3])                   # [["05:30", 1000, "stair 1 kW"], ...]
WATCHDOG_S = 2400
cookie = None


def log(*a):
    print(datetime.now(TZ).strftime("%H:%M:%S"), *a, flush=True)


def http(method, url, body=None, auth=False, timeout=20):
    req = urllib.request.Request(url, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    if auth and cookie:
        req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.headers.get("Set-Cookie"), json.load(r)


def login():
    global cookie
    set_cookie, _ = http("POST", MB + "/api/auth/login", {"username": USER, "password": PASSWORD})
    cookie = set_cookie.split(";")[0]


def cmd(slug, value):
    for attempt in range(2):
        try:
            _, r = http("POST", f"{MB}/api/gateways/default/command",
                        {"slug": slug, "value": str(value)}, auth=True)
            log("cmd", slug, value, "->", r.get("ok"), r.get("result"))
            return bool(r.get("ok"))
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 0:
                login(); continue
            log("cmd error", slug, value, e); return False
        except Exception as e:
            log("cmd error", slug, value, e); return False


def power():
    """Latest 1301 power flow, or {} if the Local Bridge is slow (never blocks a step)."""
    try:
        _, p = http("GET", LB + "/api/power", timeout=10)
        return p
    except Exception as e:                               # noqa: BLE001
        log("power read failed:", e)
        return {}


def release(why):
    log("RELEASE:", why)
    ok = cmd("battery_command", "Release")
    if not ok:
        time.sleep(10); cmd("battery_command", "Release")


def at(hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime.fromisoformat(f"{DAY}T{h:02d}:{m:02d}:00").replace(tzinfo=TZ).timestamp()


def main():
    login()
    active = False
    log("plan", PLAN, "max_soc", MAX_SOC)
    try:
        for hhmm, watts, label in PLAN:
            t = at(hhmm)
            while time.time() < t:
                time.sleep(max(0, min(30, t - time.time())))
                if time.time() >= t:
                    break
                if active:                               # guard while forcing
                    p = power()
                    if not p:
                        pass                             # stalled read: keep forcing (bridge watchdog)
                    elif p.get("elecnet_state") != 0:
                        release("grid not connected"); active = False
                    elif (p.get("soc") or 0) >= MAX_SOC:
                        release(f"SoC {p.get('soc'):.1f} >= {MAX_SOC}"); active = False
            p = power()
            if not p and watts:
                log("skip: no telemetry to check the guards"); continue
            log("step", hhmm, label, "| soc", round(p.get("soc", 0), 1), "p_fhp", p.get("p_fhp"),
                "p_uti", p.get("p_uti"), "t_amb", p.get("t_amb"))
            if watts == 0:
                if active:
                    release(label); active = False
                continue
            if p.get("elecnet_state") != 0:
                log("skip: grid not connected"); continue
            if (p.get("soc") or 0) >= MAX_SOC:
                log(f"skip: SoC {p.get('soc'):.1f} >= {MAX_SOC}"); continue
            if not active and abs(p.get("p_fhp") or 0) > 1500:
                log("skip: battery already dispatching", p.get("p_fhp")); continue
            ok = (cmd("battery_command_duration", WATCHDOG_S)
                  and cmd("battery_command_target_soc", 0)
                  and cmd("battery_command_power", watts)
                  and (active or cmd("battery_command", "Force Charge")))
            if not ok:
                release("command failed"); active = False; continue
            active = True
            time.sleep(40)
            p = power()
            log("  verify: p_fhp", p.get("p_fhp"), "(expect about", -watts, ")")
    finally:
        if active:
            release("end of plan")
    log("done")


if __name__ == "__main__":
    main()
