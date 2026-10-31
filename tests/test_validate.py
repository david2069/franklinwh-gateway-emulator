"""Checking fleet and scenario files without running them; the gateway config description."""

import json
from dataclasses import fields
from pathlib import Path

import pytest

from franklinwh_emulator.gateway import Fleet, GatewayConfig, load_yaml
from franklinwh_emulator.schema import schema
from franklinwh_emulator.site import SiteConfig
from franklinwh_emulator.validate import DOCS, config_schema, validate_fleet, validate_scenario

ROOT = Path(__file__).resolve().parent.parent
BUILTINS = sorted((ROOT / "src/franklinwh_emulator/scenarios").glob("*.yaml"))


def test_example_fleet_is_clean_and_starts():
    d = load_yaml(str(ROOT / "examples/fleet.yaml"))
    assert validate_fleet(d) == []
    Fleet.from_dict(d)        # what validation said is buildable is buildable


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
    got = {(p["path"], p["error"].split(";")[0].split(" '")[0]) for p in problems}
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
    assert [(p["path"], p["error"].split(";")[0]) for p in problems] == [
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
               ({f.name for f in fields(SiteConfig)} - {"seed"}) | {"modbus", "directconnect"}
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
