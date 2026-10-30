"""Action log, Save and Restore: get a fleet back to where it was after a restart."""

import json
import urllib.error
import urllib.request

import pytest
import yaml

from franklinwh_emulator.cli import main
from franklinwh_emulator.control import ApiError, gateway_state
from franklinwh_emulator.gateway import Fleet

FLEET = {"defaults": {"setup": "in-service", "tz_offset_hours": 10},
         "gateways": [{"id": "site", "seed": 7, "loads": "sydney-family"}, {"id": "b", "seed": 3}]}


def start(tmp_path):
    f = Fleet.from_dict(FLEET)
    f.start(control_port=0, ui=True, workspace=str(tmp_path))
    return f


def post(f, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{f.control.port}{path}", method="POST",
                                 data=json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def get(f, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{f.control.port}{path}", timeout=5) as r:
        return json.load(r)


SETUP = [("site", "load", {"w": 4321}), ("site", "circuit", {"n": 2, "on": False}),
         ("site", "appliance", {"name": "hot_water", "on": True}), ("site", "mode", {"mode": 3}),
         ("b", "alarms", {"set": ["OVER_TEMP"]}), ("b", "pv", {"solar_w": 2500}),
         ("site", "episode", {"down_s": 0.05, "offgrid_s": 0.05})]


def test_log_save_restart_restore(tmp_path):
    f = start(tmp_path)
    try:
        for gid, action, body in SETUP:
            post(f, f"/gateways/{gid}/{action}", body)
        with pytest.raises(urllib.error.HTTPError):
            post(f, "/gateways/site/circuit", {"n": 2})                  # refused, still logged
        log = get(f, "/log")
        assert [e["action"] for e in log] == [a for _, a, _ in SETUP] + ["circuit"]
        assert log[-1]["ok"] is False and "missing ['on']" in log[-1]["error"]
        assert all(e["site_time"] and e["at"] for e in log)
        soc = {g: gateway_state(f[g])["soc"] for g in ("site", "b")}
        saved = post(f, "/session/save", {"name": "evening"})
        assert saved["name"] == "evening" and saved["actions"] == 6 + 2   # 6 settings + 2 SoC
    finally:
        f.stop()

    doc = yaml.safe_load((tmp_path / "sessions" / "evening.yaml").read_text())
    assert [a["do"] for a in doc["actions"]] == ["load", "circuit", "appliance", "mode", "alarms",
                                                "pv", "soc", "soc"]       # no episode, no failure
    assert doc["emulator"]["version"] and doc["gateways"] == ["b", "site"]

    f = start(tmp_path)                                                   # a fresh run
    try:
        assert get(f, "/sessions")[0]["name"] == "evening"
        assert gateway_state(f["site"])["native_mode"] != 3
        r = post(f, "/session/restore", {"name": "evening"})
        assert (r["applied"], r["failed"]) == (8, 0)
        s, b = gateway_state(f["site"]), gateway_state(f["b"])
        assert s["overrides"]["load_w"] == 4321 and s["native_mode"] == 3
        assert s["circuits"]["2"]["on"] is False and s["appliances"]["hot_water"]["forced"] is True
        assert "OVER_TEMP" in b["alarm_names"] and b["overrides"]["solar_w"] == 2500
        assert s["soc"] == pytest.approx(soc["site"], abs=0.1)
        assert get(f, "/log")[0]["source"] == "restore:evening"
    finally:
        f.stop()


def test_restore_reports_what_does_not_fit(tmp_path):
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "other.yaml").write_text(yaml.safe_dump({"actions": [
        {"gateway": "gone", "do": "load", "with": {"w": 1}},
        {"gateway": "b", "do": "appliance", "with": {"name": "ev", "on": True}},   # b has none
        {"gateway": "b", "do": "load", "with": {"w": 900}}]}))
    f = start(tmp_path)
    try:
        r = post(f, "/session/restore", {"name": "other"})
        assert (r["applied"], r["failed"]) == (1, 2)
        assert "no gateway 'gone'" in r["results"][0]["error"]
        assert "no appliances" in r["results"][1]["error"]
    finally:
        f.stop()


@pytest.mark.parametrize("name", ["../x", "a/b", "", "x" * 65, "sp ace"])
def test_session_names_are_plain(tmp_path, name):
    f = Fleet.from_dict(FLEET)
    from franklinwh_emulator.control import ControlApi
    api = ControlApi(f.gateways, workspace=str(tmp_path))
    with pytest.raises(ApiError) as e:
        api.save_session(name) if name else api.restore_session(name)
    assert e.value.status == 400


def test_restore_unknown_session_is_404(tmp_path):
    from franklinwh_emulator.control import ControlApi
    api = ControlApi(Fleet.from_dict(FLEET).gateways, workspace=str(tmp_path))
    with pytest.raises(ApiError) as e:
        api.restore_session("nope")
    assert e.value.status == 404
