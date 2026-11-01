"""Checking fleet and scenario files without running them; the gateway config description."""

import json
from dataclasses import fields
from pathlib import Path

import pytest

from franklinwh_emulator.gateway import Fleet, Gateway, GatewayConfig, load_yaml
from franklinwh_emulator.schema import schema
from franklinwh_emulator.site import SiteConfig
from franklinwh_emulator.validate import DOCS, config_schema, validate_fleet, validate_scenario

ROOT = Path(__file__).resolve().parent.parent
BUILTINS = sorted((ROOT / "src/franklinwh_emulator/scenarios").glob("*.yaml"))


def test_example_fleet_is_clean_and_starts():
    d = load_yaml(str(ROOT / "examples/fleet.yaml"))
    assert validate_fleet(d) == []
    Fleet.from_dict(d)        # what validation said is buildable is buildable


def test_example_gateways_have_a_service_rating():
    """Each example shows its service rating on the diagram's Meter box (#156)."""
    from franklinwh_emulator.gateway import expand_groups
    entries, _ = expand_groups(load_yaml(str(ROOT / "examples/fleet.yaml")))   # grouped: the group's (#241)
    assert [g["id"] for g in entries if not (g.get("service") or {}).get("main_breaker_a")] == []


@pytest.mark.parametrize("path", BUILTINS, ids=lambda p: p.stem)
def test_builtin_scenarios_are_clean(path):
    assert validate_scenario(load_yaml(str(path))) == []


def test_every_fleet_problem_reported_at_once():
    problems = validate_fleet({
        "extra": 1,
        "defaults": {"colour": "red"},
        "gateways": [
            {"id": "a", "port": 5020, "weather": {"model": "synthetic"}, "size": 3},
            {"id": "a", "port": 5020},
            {"id": "b", "profile": "nope"},
            {"id": "c", "modbus": {"port": 5030}, "directconnect": {"port": 5030}},
            "not a mapping",
        ]})
    got = {(p["path"], p["error"].split(";")[0].split(" '")[0].split(" (every")[0]) for p in problems}
    assert got == {
        ("extra", "unknown key"),
        ("defaults.colour", "unknown key"),
        ("gateways[0].size", "unknown key"),
        ("gateways[0]", "weather model"),
        ("gateways[1].id", "duplicate of gateways[0]"),
        ("gateways[1].modbus", "port 5020 already used by a (modbus)"),
        ("gateways[2]", "unknown profile"),
        ("gateways[3].directconnect", "port 5030 already used by c (modbus)"),
        ("gateways[4]", "must be a mapping"),
    }


@pytest.mark.parametrize("bad,path", [
    (None, "fleet"),
    ({"gateways": []}, "gateways"),
    ({"defaults": [1], "gateways": [{"id": "a"}]}, "defaults"),
])
def test_fleet_shape(bad, path):
    assert [p["path"] for p in validate_fleet(bad)] == [path]


def test_every_scenario_problem_reported_at_once():
    problems = validate_scenario({
        "fleet": {"gateways": [{"id": "home", "colour": 1}]},
        "steps": [
            {"at": 0, "do": "grid", "with": {"hzz": 51}},
            {"at": "1m", "do": "fly"},
            {"at": "2m", "gateway": "shed", "do": "release"},
            {"at": "3m", "do": "circuit", "with": {"n": 1}},
            {"at": "4m", "expect": {"grid_connected": True}},
        ]})
    assert [(p["path"], p["error"].split(";")[0].split(" (every")[0]) for p in problems] == [
        ("fleet.gateways[0].colour", "unknown key"),
        ("steps[0].with", "grid: unknown keys ['hzz']"),
        ("steps[1].do", "unknown action 'fly'"),
        ("steps[2].gateway", "no gateway 'shed'"),
        ("steps[3].with", "circuit: missing ['on']"),
    ]


def test_scenario_structure_errors():
    problems = validate_scenario({"fleet": {"gateways": [{"id": "h"}]}, "checks": ["nope"]})
    assert problems[0]["path"] == "scenario" and "unknown checks" in problems[0]["error"]
    assert validate_scenario({"steps": []})[0]["error"].startswith("scenario needs a `fleet:`")


def test_config_schema_covers_every_field_with_a_doc():
    cs = config_schema()
    expected = ({f.name for f in fields(GatewayConfig)} - {"site"}) | \
               ({f.name for f in fields(SiteConfig)} - {"seed"}) | {"modbus", "directconnect", "dnsp", "lines_company"}
    assert set(cs) == expected == set(DOCS)
    assert all(v["doc"] for v in cs.values())
    assert cs["tz_offset_hours"] == {"type": "number", "nullable": True, "default": None,
                                     "doc": DOCS["tz_offset_hours"]}
    assert cs["pv_inputs"]["default"] == {"proximal": 1.0}


def test_schema_includes_gateway_config_and_is_json():
    s = json.loads(json.dumps(schema()))
    assert set(s) == {"actions", "groups", "gateway_config", "tou"}
    assert "apower_units" in s["gateway_config"]


def test_configuration_page_lists_every_key_and_its_example_is_valid():
    """docs/configuration.md documents every gateway key, and its example fleet validates."""
    import re
    from pathlib import Path
    import yaml
    from franklinwh_emulator.gateway import fix_bool_keys
    page = (Path(__file__).parents[1] / "docs" / "configuration.md").read_text()
    missing = [k for k in config_schema() if f"| `{k}` |" not in page]
    assert not missing, f"add to docs/configuration.md: {missing}"
    example = re.search(r"```yaml\n(.*?)```", page, re.S).group(1)
    assert validate_fleet(fix_bool_keys(yaml.safe_load(example))) == []


def test_default_profile_is_v12():
    """#126: the default follows the reference aGate since 2026-10-20."""
    from franklinwh_emulator.gateway import Gateway, GatewayConfig
    gw = Gateway(GatewayConfig(seed=7))
    assert gw.profile.name == "agate-v12r02b60d00-candidate"
    assert gw.device.image.get(1, "Vr") == "V12R02B60D00"
    assert config_schema()["profile"]["default"] == "agate-v12r02b60d00-candidate"


def test_same_seed_or_serial_is_flagged():
    """#162: one seed gives one serial, so two gateways on it collide for serial-keyed clients."""
    same = validate_fleet({"gateways": [{"id": "a", "seed": 7}, {"id": "b", "seed": 7}]})
    assert [(p["path"], p["error"].split(",")[0]) for p in same] == \
        [("gateways[1].seed", "gives serial " + Gateway(GatewayConfig(id="x", seed=7)).serial)]
    by_serial = validate_fleet({"gateways": [{"id": "a", "serial": "S1"}, {"id": "b", "serial": "S1", "seed": 2}]})
    assert [p["path"] for p in by_serial] == ["gateways[1].serial"]
    assert validate_fleet({"gateways": [{"id": "a", "seed": 7}, {"id": "b", "seed": 8}]}) == []
    assert validate_fleet({"gateways": [{"id": "a"}, {"id": "b"}]}) == []      # unset: not refused


# -- validate command: recordings, sessions, file kinds (#200) ----------------------------------
from franklinwh_emulator.cli import main as cli_main
from franklinwh_emulator.validate import check_recording, validate_file, validate_session


def _csv(tmp_path, name="rec.csv", header="timestamp,solar_w,home_w,battery_w,soc", rows=None):
    rows = rows if rows is not None else [
        "2026-10-01T00:00:00Z,0,500,500,60", "2026-10-01T00:05:00Z,0,520,520,59",
        "2026-10-01T01:05:00Z,100,480,380,58"]
    f = tmp_path / name
    f.write_text("\n".join([header, *rows]) + "\n")
    return f


def test_recording_summary_and_use_columns(tmp_path):
    f = _csv(tmp_path)
    problems, info = check_recording(str(f))
    assert problems == []
    assert info == {"rows": 3, "first": "2026-10-01T00:00:00+00:00", "last": "2026-10-01T01:05:00+00:00",
                    "span_h": 1.1, "max_gap_s": 3600, "gaps_over_max_gap": 1,
                    "columns": ["solar_w", "home_w", "battery_w", "soc"]}
    problems, _ = check_recording({"source": str(f), "use": ["solar", "load", "ambient", "mode"]})
    assert [p["path"] for p in problems] == ["replay.use", "replay.use"]
    assert "'ambient' plays ['ambient_temp_c']" in problems[0]["error"]
    assert "'mode' plays ['mode_name']" in problems[1]["error"]


def test_recording_problems_name_the_fix(tmp_path):
    no_ts = _csv(tmp_path, "nots.csv", header="time,solar_w,home_w")
    one = _csv(tmp_path, "one.csv", rows=["2026-10-01T00:00:00Z,0,500,500,60"])
    for f, want in ((no_ts, "no timestamp column"), (one, "fewer than two rows")):
        problems, info = check_recording(str(f))
        assert len(problems) == 1 and problems[0]["path"] == "replay.source" and info == {}
        assert want in problems[0]["error"] and "`timestamp` or `ts` column" in problems[0]["error"]
    assert check_recording(str(tmp_path / "gone.csv"))[0][0]["error"].startswith("no such file")
    assert "unknown" in check_recording({"source": str(no_ts), "use": ["wind"]})[0][0]["error"]


def test_recording_from_sqlite(tmp_path):
    import sqlite3
    db = tmp_path / "bridge.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE metrics (ts REAL, solar_w REAL, home_w REAL)")
    con.executemany("INSERT INTO metrics VALUES (?, ?, ?)", [(1.7e9, 0, 400), (1.7e9 + 60, 10, 410)])
    con.commit()
    con.close()
    kind, problems, info = validate_file(db)
    assert (kind, problems, info["rows"], info["columns"]) == ("recording", [], 2, ["solar_w", "home_w"])


def test_fleet_reports_its_recording(tmp_path):
    f = _csv(tmp_path, "nots.csv", header="time,solar_w")
    problems = validate_fleet({"gateways": [{"id": "a", "replay": {"source": str(f)}},
                                            {"id": "b", "replay": {"source": str(tmp_path / "x.csv")}}]})
    assert [p["path"] for p in problems] == ["gateways[0].replay.source", "gateways[1].replay.source"]
    assert "no timestamp column" in problems[0]["error"] and "no such file" in problems[1]["error"]


def test_session_problems():
    good = {"gateways": ["site"], "actions": [{"gateway": "site", "do": "soc", "with": {"value": 50}}]}
    assert validate_session(good) == []
    bad = {"gateways": ["site"], "actions": [
        {"gateway": "other", "do": "soc", "with": {"value": 50}},
        {"gateway": "site", "do": "fly"},
        {"gateway": "site", "do": "soc", "with": {"valu": 5}},
        "x"]}
    got = [(p["path"], p["error"].split(";")[0]) for p in validate_session(bad)]
    assert got == [("actions[0].gateway", "'other' is not in this session's gateways ['site']"),
                   ("actions[1].do", "unknown action 'fly'"),
                   ("actions[2].with", "soc: unknown keys ['valu']"),
                   ("actions[3]", "must be a mapping {gateway, do, with}")]


def test_validate_command(tmp_path, capsys):
    fleet = tmp_path / "f.yaml"
    fleet.write_text("gateways:\n  - {id: a, seed: 1}\n")
    rec = _csv(tmp_path)
    broken = tmp_path / "broken.yaml"
    broken.write_text("gateways:\n  - {id: a\n")
    assert cli_main(["validate", str(fleet), str(rec)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"{fleet}: fleet ok (1 gateway)"
    assert out[1].startswith(f"{rec}: recording ok (3 rows, 2026-10-01T00:00:00+00:00 to ")
    assert cli_main(["validate", str(fleet), str(broken)]) == 2
    out = capsys.readouterr().out.splitlines()
    assert out[1] == f"{broken}: unknown: 1 problem"
    assert out[2].startswith(f"{broken}: line 3, column 1: not valid YAML")


def test_fleet_command_checks_the_file_first(tmp_path, capsys):
    """#200: `fleet` prints every problem and exits 2, as `ui` does (it raised on the first)."""
    f = tmp_path / "f.yaml"
    f.write_text("gateways:\n  - {id: a, seed: 1, colour: red}\n  - {id: a, seed: 1}\n")
    assert cli_main(["fleet", str(f)]) == 2
    err = capsys.readouterr().err.splitlines()
    assert [e.split(": ")[1] for e in err] == ["gateways[0].colour", "gateways[1].id", "gateways[1].seed"]


def test_unknown_key_suggests_the_closest():
    problems = validate_fleet({"gateways": [{"id": "a", "sead": 3, "colour": "red"}]})
    errs = {p["path"]: p["error"] for p in problems}
    assert errs["gateways[0].sead"].startswith("unknown key; did you mean 'seed'?")
    assert errs["gateways[0].colour"].startswith("unknown key (every key: docs/configuration.md")


def test_startup_fields_are_documented():
    """#200: every `key=` the banner prints is explained in docs/validate.md."""
    import re
    src = (Path(__file__).resolve().parent.parent / "src/franklinwh_emulator/cli.py").read_text()
    run = src[src.index("async def _run"):src.index("# -- ctl")]
    keys = set(re.findall(r"(\w+)=\{", run))
    doc = (Path(__file__).resolve().parent.parent / "docs/validate.md").read_text()
    assert keys >= {"serial", "profile", "grid", "legs", "modbus_max_connections", "dc_max_connections"}
    assert [k for k in sorted(keys) if f"`{k}=`" not in doc] == []


def test_single_phase_on_a_120v_code_is_refused():
    """#153: ac_type 0 with a North American code gave 60 V half-legs; split and three phase stay."""
    problems = validate_fleet({"gateways": [{"id": "a", "ac_type": 0, "grid_code": "ieee1547-cat2"}]})
    assert len(problems) == 1 and "use ac_type 1 (split phase, 120/240 V)" in problems[0]["error"]
    for ac in (1, 2):
        assert validate_fleet({"gateways": [{"id": "a", "ac_type": ac, "grid_code": "ieee1547-cat2"}]}) == []
    assert validate_fleet({"gateways": [{"id": "a", "ac_type": 0}]}) == []          # AU single phase
