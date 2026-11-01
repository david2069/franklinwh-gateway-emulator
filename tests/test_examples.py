"""The published examples and the README's in-process snippet must keep working."""
import importlib.util
import pathlib
import re
import sys

import yaml

from franklinwh_emulator import Fleet

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
sys.path.insert(0, str(EXAMPLES))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_single_gateway_example():
    v = _load("single_gateway").main()
    assert 0 <= v["soc_pct"] <= 100
    # The registers it reads close the home-load balance (#2, #4).
    assert abs(v["solar_w"] + v["grid_w"] + v["battery_w"] - v["home_load_w"]) <= 3


def test_fleet_with_control_example():
    out = _load("fleet_with_control").main()
    assert "site" in out["gateways"] and len(out["gateways"]) == 10      # incl. the factory `fresh`; AU 3- and 2-phase homes are 3 + 2 aGates (#242)
    assert out["before"] is True and out["after"] is False


def test_conftest_for_clients_example(record_property):
    mod = _load("conftest_for_clients")
    gen = mod.agate.__wrapped__(record_property)
    gw = next(gen)
    try:
        mod.test_example_reads_soc(gw)
    finally:
        gen.close()


def test_getting_started_in_process_snippet(monkeypatch):
    text = (ROOT / "docs" / "getting-started.md").read_text()      # moved from the README (#85)
    code = re.search(r"In-process.*?```python\n(.*?)```", text, re.S).group(1)
    # Same code, but the fleet's fixed ports become ephemeral so the test never collides.

    def from_yaml(cls, path):
        d = yaml.safe_load((ROOT / path).read_text())
        for g in d["gateways"]:
            g["modbus"] = {**(g.get("modbus") or {}), "port": 0}
            if "directconnect" in g:
                g["directconnect"] = {**(g["directconnect"] or {}), "port": 0}
        return cls.from_dict(d)

    monkeypatch.setattr(Fleet, "from_yaml", classmethod(from_yaml))
    monkeypatch.chdir(ROOT)
    exec(compile(code, "README.md", "exec"), {})
