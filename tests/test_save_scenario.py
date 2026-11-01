"""Save the run as a scenario (#90)."""

import yaml

from conftest import make_gateway
from franklinwh_emulator.control import ControlApi
from franklinwh_emulator.scenario import Scenario, run_scenario


def test_run_saved_as_a_scenario_replays(clock, tmp_path):
    gw = make_gateway(clock, id="home")
    api = ControlApi({"home": gw}, workspace=str(tmp_path))
    clock.advance(60)
    api.apply_logged(gw, "load", {"w": 900})
    clock.advance(240)
    api.apply_logged(gw, "vpp", {"watts": -2000})
    api.apply_logged(gw, "episode", {"down_s": 1, "offgrid_s": 1})   # transient: left out
    clock.advance(120)
    r = api.save_scenario("evening")
    assert r["steps"] == 2
    doc = yaml.safe_load(open(r["path"]))
    assert [(s["at"], s["do"]) for s in doc["steps"]] == [("60s", "load"), ("300s", "vpp")]
    assert doc["duration"] == "420s" and doc["fleet"]["gateways"][0]["id"] == "home"
    assert doc["start"] == api.began["home"].replace(tzinfo=None).isoformat(timespec="seconds")
    Scenario.load(r["path"])
    res = run_scenario(r["path"])
    assert not res.errors and res.rows


def test_saved_scenario_uses_the_fleet_file(clock, tmp_path):
    fleet = tmp_path / "f.yaml"
    fleet.write_text("gateways:\n  - {id: home, seed: 3, grid_code: ausgrid}\n")
    gw = make_gateway(clock, id="home")
    api = ControlApi({"home": gw}, workspace=str(tmp_path), fleet_file=str(fleet))
    doc = yaml.safe_load(open(api.save_scenario("x")["path"]))
    assert doc["fleet"] == {"gateways": [{"id": "home", "seed": 3, "grid_code": "ausgrid"}]}
