"""``franklinwh-emulator`` command line.

    franklinwh-emulator serve --port 5020 --seed 7
    franklinwh-emulator serve --profile spec --time-scale 60
    franklinwh-emulator fleet fleet.yaml
    franklinwh-emulator ui fleet.yaml                 # the fleet + web UI on http://127.0.0.1:8502/
    franklinwh-emulator scenario list
    franklinwh-emulator scenario run outage-during-vpp --out results/
    franklinwh-emulator scenario run my.yaml --live --time-scale 60   # a bridge can connect

    # steer a running emulator (control API, default http://127.0.0.1:8502)
    franklinwh-emulator ctl agate status
    franklinwh-emulator ctl agate outage            # black-out (or: outage L2 = lose one leg)
    franklinwh-emulator ctl agate voltage 195       # brown-out (volts L-N, or 0.8pu; add L3 for one leg)
    franklinwh-emulator ctl agate hz 51.3
    franklinwh-emulator ctl agate restore
    franklinwh-emulator ctl agate offgrid on
    franklinwh-emulator ctl agate write-reject 704.WSetPct abs_gt=100 code=3   # or: clear
    franklinwh-emulator ctl agate addressing alias=40000 write=0   # V12 mirror; writes at 0 only
    franklinwh-emulator ctl agate range-fault 15500 15509 silent for=30   # or: exception code=2, short; clear
    franklinwh-emulator ctl agate episode 60 120    # drop 60 s, back islanded, grid back after 120 s
    franklinwh-emulator ctl agate pv remote1 off
    franklinwh-emulator ctl agate solar 6000        # or: solar auto
    franklinwh-emulator ctl agate alarm OVER_TEMP
    franklinwh-emulator ctl agate reset-alarms
    franklinwh-emulator ctl agate fault latency_s=0.5 session_held=true
    franklinwh-emulator ctl agate modbus off         # 1205 sunsMdEn=0 (applies after ~3 s)
    franklinwh-emulator ctl agate export-limit enforce   # or: track (default)

    # CSIP-AUS: enable the aGate's 2030.5 client, then play the utility server
    franklinwh-emulator ctl agate csip on https://csip.example/dcap
    franklinwh-emulator ctl agate csip export 1500 1800   # opModExpLimW 1.5 kW for 30 min
    franklinwh-emulator ctl agate csip default export=5000
    franklinwh-emulator ctl agate csip server down        # comms loss → default after 300 s
    franklinwh-emulator ctl agate csip connect off
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import urllib.error
import urllib.request

from .control import ControlApi
from .gateway import Fleet, Gateway, GatewayConfig
from .gridcodes import GRID_CODES
from .profiles import DEFAULT_PROFILE, PROFILES
from .site import ALARMS, SiteConfig

DEFAULT_API = "http://127.0.0.1:8502"


async def _run(gateways: list[Gateway], control_port: int | None, ui: bool = False,
               workspace: str = "emulator-workspace", fleet_file: str | None = None,
               restore: str | None = None) -> None:
    for gw in gateways:
        host, port = await gw.start_async()
        p = gw.profile
        modbus = (f"Modbus TCP on {host}:{port}" if gw.modbus_enabled else
                  f"Modbus TCP off ({gw.config.setup}; enable with Direct Connect 1205 sunsMdEn)")
        print(f"{gw.config.id}: {modbus}  serial={gw.serial}  "
              f"profile={p.name}  grid={gw.site.grid.name}  legs={','.join(gw.site.phases)}  "
              f"modbus_max_connections={p.max_connections}",
              flush=True)
        if gw.dc is not None:
            dh, dp = gw.dc_address
            print(f"{gw.config.id}: Direct Connect on {dh}:{dp}  "
                  f"dc_max_connections={p.dc_max_connections}", flush=True)
    api = None
    if control_port is not None:
        cls = ControlApi
        if ui:
            from .ui.server import UiServer
            cls = UiServer
        api = cls({gw.config.id: gw for gw in gateways}, port=control_port,
                  workspace=workspace, fleet_file=fleet_file)
        host, port = await api.start()
        print(f"control API on http://{host}:{port}/gateways", flush=True)
        if ui:
            print(f"UI on http://{host}:{port}/", flush=True)
        if restore:
            from .control import ApiError
            try:
                r = await asyncio.to_thread(api.restore_session, restore)   # may send DC frames
                print(f"restored session {restore}: {r['applied']} actions applied, "
                      f"{r['failed']} failed", flush=True)
                for x in r["results"]:
                    if not x["ok"]:
                        print(f"  {x['gateway']} {x['do']} {x.get('with')}: {x['error']}", flush=True)
            except ApiError as e:
                print(f"restore {restore}: {e}", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    if api is not None:
        await api.stop()
    for gw in gateways:
        await gw.stop_async()


# -- ctl --------------------------------------------------------------------------
def _parse_value(v: str):
    low = v.lower()
    if low in ("true", "on", "yes"):
        return True
    if low in ("false", "off", "no"):
        return False
    if low in ("none", "null", "auto"):
        return None
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


_CSIP_ALIASES = {"export": "opModExpLimW", "import": "opModImpLimW", "gen": "opModGenLimW",
                 "load": "opModLoadLimW", "connect": "opModConnect", "energize": "opModEnergize"}


def _ctl_request(verb: str, args: list[str]) -> tuple[str, str, dict]:
    """Map a ctl verb to (method, action, body)."""
    def need(n):
        if len(args) < n:
            raise SystemExit(f"ctl {verb}: expected {n} argument(s)")

    if verb == "status":
        return "GET", "", {}
    def leg(body, rest):
        if rest:
            body["phase"] = rest[0].upper()
        return body

    if verb == "outage":
        return "POST", "grid", leg({"available": False}, args)
    if verb == "restore":
        return "POST", "grid", {"restore": True}
    if verb == "voltage":
        need(1)
        v = args[0].lower()
        body = {"v_pu": float(v[:-2])} if v.endswith("pu") else {"voltage": float(v)}
        return "POST", "grid", leg(body, args[1:])
    if verb == "hz":
        need(1)
        return "POST", "grid", {"hz": float(args[0])}
    if verb == "offgrid":
        need(1)
        return "POST", "offgrid", {"on": bool(_parse_value(args[0]))}
    if verb == "write-reject":
        need(1)
        if args[0] == "clear":
            return "POST", "write-reject", {"clear": True}
        model, _, point = args[0].partition(".")
        body = {"model": int(model), "point": point}
        for a in args[1:]:
            k, _, v = a.partition("=")
            body[k] = int(v)
        return "POST", "write-reject", body
    if verb == "addressing":
        need(1)
        body: dict = {}
        for a in args:
            k, _, v = a.partition("=")
            bases = None if v in ("all", "none", "") else [int(b) for b in v.split(",")]
            if k == "alias":
                body["base_aliases"] = bases or []
            elif k == "write":
                body["write_bases"] = bases
            else:
                raise SystemExit("addressing: alias=40000|none write=0|40000|all")
        return "POST", "addressing", body
    if verb == "range-fault":
        need(1)
        if args[0] == "clear":
            return "POST", "range-fault", {"clear": True}
        need(2)
        body = {"start": int(args[0]), "end": int(args[1])}
        for a in args[2:]:
            if "=" in a:
                k, v = a.split("=", 1)
                body[{"for": "for_s"}.get(k, k)] = _parse_value(v)
            else:
                body["mode"] = a
        return "POST", "range-fault", body
    if verb == "episode":
        need(2)
        return "POST", "episode", {"down_s": float(args[0]), "offgrid_s": float(args[1])}
    if verb == "pv":
        need(2)
        return "POST", "pv", {"input": args[0], "relay": bool(_parse_value(args[1]))}
    if verb == "solar":
        need(1)
        return "POST", "pv", {"solar_w": _parse_value(args[0])}
    if verb == "load":
        need(1)
        return "POST", "load", {"w": _parse_value(args[0])}
    if verb == "ambient":
        need(1)
        return "POST", "ambient", {"c": _parse_value(args[0])}
    if verb == "alarm":
        need(1)
        persistent = "--persistent" in args
        names = [a.upper() for a in args if a != "--persistent"]
        return "POST", "alarms", {"set": names, "latched": not persistent}
    if verb == "clear-alarm":
        return "POST", "alarms", {"clear": [a.upper() for a in args] or "all"}
    if verb == "reset-alarms":
        return "POST", "alarms", {"reset": True}
    if verb == "vpp":
        need(1)
        return "POST", "vpp", {"watts": float(args[0])}
    if verb == "release":
        return "POST", "release", {}
    if verb == "mode":
        need(1)
        return "POST", "mode", {"mode": int(args[0])}
    if verb == "soc":
        need(1)
        return "POST", "soc", {"value": float(args[0])}
    if verb == "circuit":
        need(2)
        return "POST", "circuit", {"n": int(args[0]), "on": bool(_parse_value(args[1]))}
    if verb == "appliance":
        need(2)
        v = _parse_value(args[1])
        return "POST", "appliance", {"name": args[0], "on": None if v is None else bool(v)}
    if verb == "generator":
        need(1)
        sub = args[0]
        if sub in ("start", "stop", "clear"):
            return "POST", "generator", {sub: True}
        if sub == "fault":
            need(2)
            return "POST", "generator", {"fault": None if args[1] in ("none", "off") else args[1]}
        if sub == "refuel":
            need(2)
            return "POST", "generator", {"refuel": None if args[1] == "unlimited" else float(args[1])}
        if sub == "set":
            need(2)
            kv = dict(a.split("=", 1) for a in args[1:])
            return "POST", "generator", {"set": {k: _parse_value(v) for k, v in kv.items()}}
        raise SystemExit("generator: start | stop | clear | fault fail-start|trip|none | "
                         "refuel <litres>|unlimited | set key=value ...")
    if verb == "sky":
        need(1)
        return "POST", "weather", {"sky": args[0]}
    if verb == "modbus":
        need(1)
        return "POST", "der-comms", {"modbus": bool(_parse_value(args[0]))}
    if verb == "export-limit":
        need(1)
        if args[0] not in ("enforce", "track"):
            raise SystemExit("ctl export-limit: enforce | track")
        return "POST", "export", {"enforced": args[0] == "enforce"}
    if verb == "csip":
        need(1)
        sub, rest = args[0], args[1:]
        if sub in ("on", "off"):
            body = {"enable": sub == "on"}
            if rest:
                body["uri"] = rest[0]
            return "POST", "csip", body
        if sub == "server":
            if not rest or rest[0] not in ("up", "down"):
                raise SystemExit("ctl csip server up|down")
            return "POST", "csip", {"server_up": rest[0] == "up"}
        if sub == "clear":
            return "POST", "csip", {"clear": True}
        if sub == "default":
            controls = {}
            for a in rest:
                k, _, v = a.partition("=")
                controls[_CSIP_ALIASES.get(k, k)] = _parse_value(v)
            return "POST", "csip", {"default": controls}
        if sub in _CSIP_ALIASES:
            if not rest:
                raise SystemExit(f"ctl csip {sub}: needs a value")
            body = {"control": {_CSIP_ALIASES[sub]: _parse_value(rest[0])}}
            if len(rest) > 1:
                body["duration_s"] = float(rest[1])
            return "POST", "csip", body
        raise SystemExit(f"unknown csip sub-command {sub!r}")
    if verb in ("fault", "dc-fault"):
        need(1)
        body = {}
        for a in args:
            k, _, v = a.partition("=")
            body[k] = _parse_value(v)
        return "POST", "faults" if verb == "fault" else "dc-faults", body
    raise SystemExit(f"unknown ctl verb {verb!r}")


def _summary(state: dict) -> str:
    u = state["utility"]
    trip = state["trip_reason"] + (f" {state['trip_stage']}" if state["trip_stage"] else "") \
        if state["trip_reason"] else ""
    grid = "connected" if state["grid_connected"] else f"ISLANDED ({trip})"
    legs = "  ".join(
        f"{ph}:{'up' if u['available'][ph] else 'DOWN'} {u['v_pu'][ph]:.3f}pu"
        f" {st['v_ln']:.1f}V {st['grid_w']:+.0f}W"
        for ph, st in state["phases"].items())
    def watts(v):
        return "none" if v is None else f"{v:.0f} W"
    export_limit = (f"declared {watts(state['declared_export_limit_w'])}, enforcing "
                    f"{watts(state['export_limit_w'])}, over by {state['export_over_limit_w']:.0f} W"
                    f" (breach total {state['export_breach_s']:.0f} s / "
                    f"{state['export_breach_wh']:.0f} Wh)")
    c = state["csip"]
    csip = (f"{c['status']}  source={c['source']}  controls={c['controls'] or '{}'}"
            + (f"  sfdi={c['sfdi']}" if c["sfdi"] else "")
            + ("" if c["server_up"] else "  SERVER DOWN"))
    lines = [
        f"{state['id']}  {state['serial']}  {state['address'][0]}:{state['address'][1]}"
        f"  {state['grid_code']}  ac_type={state['ac_type']}",
        f"  grid      {grid}   utility {u['hz']:.2f} Hz",
        f"  legs      {legs}",
        f"  measured  {state['v_ln']:.1f} V  {state['hz']:.2f} Hz   inverter_state={state['inverter_state']}"
        f"  run_status={state['run_status']}  throttle={state['throttle_pct']}%",
        f"  power     solar {state['solar_w']:.0f} W (clipped {state['solar_clipped_w']:.0f})  "
        f"load {state['load_w']:.0f} W (shed {state['load_shed_w']:.0f})  "
        f"battery {state['battery_w']:+.0f} W  grid {state['grid_w']:+.0f} W  var {state['var']:+.0f}",
        f"  export    {export_limit}",
        f"  csip-aus  {csip}",
        f"  modbus    {'on' if state['modbus_enabled'] else 'OFF (sunsMdEn=0)'}"
        + (f"   direct connect {state['dc_address'][0]}:{state['dc_address'][1]}"
           if state.get("dc_address") else "   direct connect off"),
        f"  battery   SoC {state['soc']:.1f}%  mode {state['native_mode']} {state.get('native_mode_name', '')}  "
        f"VPP {'on' if state['remote'] else 'off'}",
        f"  pv relays {state['pv_relays']}",
        f"  circuits  " + "  ".join(f"{n}:{c['name']} {'on' if c['on'] else 'OFF'} {c['power_w']:.0f}W"
                                    for n, c in state["circuits"].items()),
        f"  loads     " + "  ".join(f"{k} {v:.0f}" for k, v in state["loads"].items() if v >= 1),
        f"  alarms    {', '.join(state['alarm_names']) or 'none'}",
    ]
    return "\n".join(lines)


def ctl(api: str, gateway: str, verb: str, args: list[str], as_json: bool) -> int:
    method, action, body = _ctl_request(verb, args)
    url = f"{api.rstrip('/')}/gateways/{gateway}" + (f"/{action}" if action else "")
    data = json.dumps(body).encode() if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            state = json.load(resp)
    except urllib.error.HTTPError as e:
        print(json.load(e).get("error", e), file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"cannot reach control API at {api}: {e.reason}", file=sys.stderr)
        return 1
    print(json.dumps(state, indent=1) if as_json else _summary(state))
    return 0


def _scenario_cmd(args) -> int:
    from pathlib import Path

    from .scenario import Runner, Scenario, builtin_names, builtin_path
    if args.scmd == "list":
        import yaml
        for name in builtin_names():
            with open(builtin_path(name), encoding="utf-8") as f:
                desc = " ".join((yaml.safe_load(f).get("description") or "").split())
            print(f"{name:34s} {desc[:100]}{'…' if len(desc) > 100 else ''}")
        return 0
    if args.scmd == "show":
        print(builtin_path(args.name).read_text())
        return 0
    failed = 0
    for src in args.scenarios:
        sc = Scenario.load(src)
        runner = Runner(sc)
        if args.live:
            def show(fleet):
                for gid, gw in fleet.gateways.items():
                    dc = f"  dc {gw.dc_address[0]}:{gw.dc_address[1]}" if gw.dc else ""
                    print(f"{sc.name}/{gid}: modbus {gw.address[0]}:{gw.address[1]}{dc}",
                          flush=True)
                if fleet.control is not None:
                    print(f"control API on http://127.0.0.1:{fleet.control.port}", flush=True)
            res = runner.run_live(time_scale=args.time_scale, control_port=args.control_port,
                                  on_start=show)
        else:
            res = runner.run()
        print(res.text())
        if args.out:
            out = res.write(Path(args.out) / sc.name)
            if args.bridge_csv:
                from .replay import to_bridge_csv
                for gid in res.summary:
                    to_bridge_csv(res.rows, out / f"metrics_{gid}.csv", gateway=gid)
            print(f"  → {out}")
        failed += not res.passed
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="franklinwh-emulator",
                                 description="FranklinWH aGate Modbus TCP emulator")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run one emulated aGate")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=5020)
    s.add_argument("--dc-port", type=int,
                   help="Direct Connect (TCP/9000 protocol) port; aGate profiles always run one "
                        "(default: a free port)")
    s.add_argument("--setup", choices=("factory", "in-service"), default="factory",
                   help="factory: Modbus TCP off until a Direct Connect 1205 write enables it "
                        "(default); in-service: Modbus TCP on")
    s.add_argument("--id", default="agate")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--serial")
    s.add_argument("--profile", default=DEFAULT_PROFILE, choices=sorted(PROFILES))
    s.add_argument("--apower-units", type=int, default=1)
    s.add_argument("--ac-type", type=int, default=0, choices=(0, 1, 2),
                   help="0 single, 1 split (US), 2 three phase")
    s.add_argument("--phases", help="legs wired, e.g. L1,L2 (default from --ac-type)")
    s.add_argument("--grid-code", choices=sorted(GRID_CODES),
                   help="default: ieee1547-cat2 for split phase, else as4777-au-a")
    s.add_argument("--timezone", help="IANA timezone, e.g. Australia/Sydney (DST-aware)")
    s.add_argument("--lat", type=float, help="site latitude (weather, Direct Connect 1202)")
    s.add_argument("--lon", type=float, help="site longitude")
    s.add_argument("--postcode", default="")
    s.add_argument("--soc", type=float, help="initial state of charge %%")
    s.add_argument("--time-scale", type=float, default=1.0)
    s.add_argument("--multi-client", action="store_true",
                   help="allow more than one Modbus client at a time")
    s.add_argument("--alias-40000", action="store_true",
                   help="also serve the SunSpec chain at base 40000")

    f = sub.add_parser("fleet", help="run several gateways from a YAML file")
    f.add_argument("config")

    u = sub.add_parser("ui", help="run a fleet from a YAML file with the web UI")
    u.add_argument("config")
    u.add_argument("--port", type=int, default=8502,
                   help="UI and control API port on 127.0.0.1 (default 8502)")
    u.add_argument("--workspace", default="emulator-workspace",
                   help="where saved sessions go (<workspace>/sessions; default ./emulator-workspace)")
    u.add_argument("--restore", metavar="NAME",
                   help="replay a saved session's actions once the fleet is up")

    for p in (s, f):
        p.add_argument("--control-port", type=int, default=8502,
                       help="HTTP control API port on 127.0.0.1 (default 8502)")
        p.add_argument("--no-control", action="store_true", help="disable the control API")

    sc = sub.add_parser("scenario", help="run timed, repeatable scenarios with checks")
    scs = sc.add_subparsers(dest="scmd", required=True)
    scs.add_parser("list", help="list built-in scenarios")
    sh = scs.add_parser("show", help="print a built-in scenario's YAML")
    sh.add_argument("name")
    rn = scs.add_parser("run", help="run a scenario (file path or built-in name)")
    rn.add_argument("scenarios", nargs="+")
    rn.add_argument("--out", help="write timeseries.csv + report.json/.txt per scenario here")
    rn.add_argument("--live", action="store_true",
                    help="start the servers so an external controller can take part")
    rn.add_argument("--time-scale", type=float, default=1.0,
                    help="live only: site seconds per real second")
    rn.add_argument("--control-port", type=int, help="live only: also start the control API")
    rn.add_argument("--bridge-csv", action="store_true",
                    help="with --out: also write metrics_<gateway>.csv in the Modbus bridge's "
                         "export format (replayable, importable into the bridge)")

    c = sub.add_parser("ctl", help="steer a running emulator via its control API",
                       description="verbs: status | outage [L1|L2|L3] | restore | voltage <V|0.8pu> [leg] | "
                                   "hz <Hz> | offgrid on|off | episode <down_s> <offgrid_s> | write-reject <model.point> [abs_gt|gt|lt=N] [code=N] | clear | addressing alias=..|none write=..|all | range-fault <start> <end> [mode] [code=N] [for=S] | clear | pv <proximal|remote1|remote2> on|off"
                                   " | solar <W|auto> | load <W|auto> | ambient <C|auto> | "
                                   "alarm <NAME...> [--persistent] | clear-alarm [NAME...] | "
                                   "reset-alarms | fault|dc-fault key=value... | vpp <W, +charge> | release | "
                                   "mode <1|2|3> (Modbus 15507: 1 Emergency Backup, 2 Self-Consumption, 3 TOU) | soc <pct> | sky clear|broken|overcast|storm | "
                                   "circuit <1-3> on|off | appliance <name> on|off|auto | modbus on|off | "
                                   "generator start|stop|clear|fault X|refuel L|set k=v... | "
                                   "export-limit enforce|track | csip on|off [uri] | "
                                   "csip server up|down | csip export|import|gen|load <W> [secs] | "
                                   "csip connect|energize on|off [secs] | csip default k=v... | "
                                   "csip clear.  Alarms: "
                                   + ", ".join(ALARMS))
    c.add_argument("--api", default=DEFAULT_API)
    c.add_argument("--json", action="store_true", help="print the full state as JSON")
    c.add_argument("gateway")
    c.add_argument("verb")
    c.add_argument("args", nargs=argparse.REMAINDER)

    args = ap.parse_args(argv)
    if args.cmd == "ctl":
        return ctl(args.api, args.gateway, args.verb, args.args, args.json)
    if args.cmd == "scenario":
        return _scenario_cmd(args)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "serve":
        quirks: dict = {}
        if args.multi_client:
            quirks["max_connections"] = None
        if args.alias_40000:
            quirks["base_aliases"] = (40000,)
        cfg = GatewayConfig(
            id=args.id, seed=args.seed, serial=args.serial, host=args.host, port=args.port,
            profile=args.profile, quirks=quirks,
            time_scale=args.time_scale, dc_port=args.dc_port, setup=args.setup,
            site=SiteConfig(seed=args.seed, apower_units=args.apower_units,
                            ac_type=args.ac_type, initial_soc=args.soc,
                            phases=args.phases.upper().split(",") if args.phases else None,
                            location={"latitude": args.lat, "longitude": args.lon,
                                      "timezone": args.timezone, "postcode": args.postcode},
                            grid_code=args.grid_code),
        )
        gateways = [Gateway(cfg)]
    elif args.cmd == "ui":
        from .gateway import load_yaml
        from .validate import validate_fleet
        d = load_yaml(args.config)
        problems = validate_fleet(d)
        if problems:
            for p in problems:
                print(f"{args.config}: {p['path']}: {p['error']}", file=sys.stderr)
            return 2
        gateways = list(Fleet.from_dict(d).gateways.values())
        try:
            asyncio.run(_run(gateways, args.port, ui=True, workspace=args.workspace,
                             fleet_file=args.config, restore=args.restore))
        except KeyboardInterrupt:
            pass
        return 0
    else:
        gateways = list(Fleet.from_yaml(args.config).gateways.values())

    try:
        asyncio.run(_run(gateways, None if args.no_control else args.control_port))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
