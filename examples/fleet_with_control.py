"""Several emulated aGates from a YAML file, plus the HTTP control API to steer one of them.

    python examples/fleet_with_control.py

Loads examples/fleet.yaml with every port made ephemeral (so it never collides with a running
emulator), cuts the grid on one gateway through the control API, and shows the change.
"""
import json
import pathlib
import urllib.request

import yaml

from franklinwh_emulator import Fleet

FLEET_YAML = pathlib.Path(__file__).with_name("fleet.yaml")


def ephemeral(d: dict) -> dict:
    for g in d["gateways"]:
        g["modbus"] = {**(g.get("modbus") or {}), "port": 0}
        if "directconnect" in g:
            g["directconnect"] = {**(g["directconnect"] or {}), "port": 0}
    return d


def call(port: int, method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def main() -> dict:
    fleet = Fleet.from_dict(ephemeral(yaml.safe_load(FLEET_YAML.read_text())))
    addresses = fleet.start(control_port=0)
    try:
        for gid, (host, port) in addresses.items():
            print(f"{gid:14} Modbus {host}:{port}")
        api = fleet.control.port
        before = call(api, "GET", "/gateways/site")["grid_connected"]
        call(api, "POST", "/gateways/site/grid", {"available": False})   # black-out
        after = call(api, "GET", "/gateways/site")["grid_connected"]
        print(f"site grid_connected: {before} -> {after}")
        return {"gateways": sorted(addresses), "before": before, "after": after}
    finally:
        fleet.stop()


if __name__ == "__main__":
    main()
