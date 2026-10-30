"""Scenario engine: parsing, determinism, expectations, invariants, recording, live mode."""

import csv
import json

import pytest

from franklinwh_emulator.scenario import (INVARIANTS, Runner, Scenario, builtin_names,
                                          check_expectation, parse_duration, run_scenario)

BASIC = {
    "name": "basic", "start": "2026-01-15T12:00:00", "duration": "10m", "record_every": "30s",
    "fleet": {"gateways": [{"id": "site", "seed": 7, "grid_code": "ausgrid", "setup": "in-service", "cap_forced_solar": False,
                            "tz_offset_hours": 10, "initial_soc": 60}]},
    "steps": [
        {"at": 0, "do": "pv", "with": {"solar_w": 0}},
        {"at": 0, "do": "load", "with": {"w": 1000}},
        {"at": "2m", "expect": {"battery_w": "~1000", "grid_connected": True}},
    ],
}


def test_parse_duration():
    assert parse_duration(90) == 90
    assert parse_duration("10m") == 600
    assert parse_duration("1h30m") == 5400
    assert parse_duration("2m9s") == 129
    assert parse_duration("1.5h") == 5400
    with pytest.raises(ValueError):
        parse_duration("soon")


def test_expectation_operators():
    assert check_expectation(5, ">0") and not check_expectation(0, ">0")
    assert check_expectation(-3000, "~-3000") and check_expectation(-3020, "~-3000")
    assert not check_expectation(-3100, "~-3000")
    assert check_expectation("V<", "==V<") and check_expectation(None, None)
    assert check_expectation(True, True) and not check_expectation(1, False)


@pytest.mark.parametrize("name", builtin_names())
def test_builtin_scenarios_pass(name):
    res = run_scenario(name)
    assert res.passed, res.text()


def test_offline_runs_are_deterministic():
    a, b = run_scenario("outage-during-vpp"), run_scenario("outage-during-vpp")
    assert a.rows == b.rows and len(a.rows) > 100


def test_failed_expectation_fails_the_run():
    d = json.loads(json.dumps(BASIC))
    d["steps"].append({"at": "3m", "expect": {"grid_w": ">5000"}})
    res = run_scenario(d)
    assert not res.passed and [e for e in res.expectations if not e["ok"]]


def test_bad_action_is_reported_not_raised():
    d = json.loads(json.dumps(BASIC))
    d["steps"].append({"at": "1m", "do": "alarms", "with": {"set": ["NOT_AN_ALARM"]}})
    res = run_scenario(d)
    assert not res.passed and res.errors[0]["step"] == "step 4"


def test_scenario_validation():
    with pytest.raises(ValueError):
        Scenario.from_dict({**BASIC, "checks": ["nope"]})
    with pytest.raises(ValueError):
        Scenario.from_dict({**BASIC, "steps": [{"at": 0, "bogus": 1}]})
    with pytest.raises(ValueError):
        Scenario.from_dict({k: v for k, v in BASIC.items() if k != "fleet"})


def test_invariants_detect_violations():
    from franklinwh_emulator.gridcodes import get_grid_code

    class _Gw:
        class site:
            grid = get_grid_code("as4777-au-a")
            export_breach_since = 1000.0
            _t = 1100.0                               # 100 s into a breach: past the envelope

            @staticmethod
            def active_reserve():
                return 20.0
    base = {"load_w": 1000, "solar_w": 0, "grid_w": 0, "battery_w": 1000, "soc": 50,
            "grid_connected": True, "export_limit_w": None}
    assert INVARIANTS["energy_balance"](_Gw, {**base, "grid_w": 300}, None)
    assert INVARIANTS["soc_bounds"](_Gw, {**base, "soc": 101}, None)
    assert INVARIANTS["export_limit"](_Gw, {**base, "grid_w": -1500, "battery_w": 2500,
                                            "export_limit_w": 1000}, None)
    _Gw.site._t = 1020.0                              # 20 s in: inside the 31 s envelope
    assert not INVARIANTS["export_limit"](_Gw, {**base, "grid_w": -1500, "battery_w": 2500,
                                                "export_limit_w": 1000}, None)
    _Gw.site._t = 1100.0
    assert INVARIANTS["islanded_no_flow"](_Gw, {**base, "grid_connected": False, "grid_w": 5}, None)
    assert INVARIANTS["reserve_floor"](_Gw, {**base, "soc": 10}, {**base, "soc": 10.5})
    assert not INVARIANTS["reserve_floor"](_Gw, {**base, "soc": 50}, {**base, "soc": 50.1})


def test_summary_and_files(tmp_path):
    res = run_scenario("latent-export-breach")
    s = res.summary["home"]
    assert s["export_breach_s"] > 500 and s["export_breach_wh"] > 100
    out = res.write(tmp_path / "x")
    rows = list(csv.DictReader(open(out / "timeseries.csv")))
    assert rows and "export_over_limit_w" in rows[0] and "L1_grid_w" in rows[0]
    report = json.loads((out / "report.json").read_text())
    assert report["passed"] and (out / "report.txt").read_text().startswith("scenario")


def test_relay_events_and_reconnect_check():
    res = run_scenario("outage-during-vpp")
    ev = res.summary["home"]["relay_events"]
    assert [e["event"] for e in ev] == ["open", "close"]
    assert ev[1]["normal_for"] >= 60 - 1e-6


def test_live_mode_serves_while_running():
    import socket
    d = json.loads(json.dumps(BASIC))
    d["duration"] = "2m"
    d["steps"] = [{"at": 0, "do": "load", "with": {"w": 1000}},
                  {"at": "1m", "expect": {"grid_connected": True}}]
    seen = {}

    def probe(fleet):
        gw = fleet["site"]
        socket.create_connection(gw.address, timeout=1).close()
        seen["ok"] = True
    res = Runner(Scenario.from_dict(d)).run_live(time_scale=240, on_start=probe)
    assert seen.get("ok") and res.passed and res.mode == "live x240"


def test_cli_scenario_run(tmp_path, capsys):
    from franklinwh_emulator.cli import main
    assert main(["scenario", "run", "csip-zero-export", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "csip-zero-export" / "timeseries.csv").exists()
    assert "PASS" in capsys.readouterr().out
    assert main(["scenario", "list"]) == 0
