"""The UI server: pages and static files beside the control API, on one loopback port."""

import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from franklinwh_emulator.cli import main
from franklinwh_emulator.control import gateway_state
from franklinwh_emulator.gateway import Fleet
from franklinwh_emulator.ui.server import STATIC

ALPINE_SHA256 = "358d9afbb1ab5befa2f48061a30776e5bcd7707f410a606ba985f98bc3b1c034"  # upstream 3.14.1

FLEET = {"gateways": [
    {"id": "site", "seed": 7, "loads": "sydney-family", "dc_port": 0,
     "location": {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney"},
     "weather": {"model": "synthetic", "kwp": 6.6, "sky": "broken"}},
    {"id": "us-split", "seed": 42, "ac_type": 1}]}


@pytest.fixture(scope="module")
def ui():
    fleet = Fleet.from_dict(FLEET)
    fleet.start(control_port=0, ui=True)
    yield fleet, f"http://127.0.0.1:{fleet.control.port}"
    fleet.stop()


def get(url):
    with urllib.request.urlopen(url, timeout=3) as r:
        return r.status, r.headers["Content-Type"], r.read()


def test_page_and_its_files(ui):
    _, base = ui
    status, ctype, body = get(base + "/")
    assert status == 200 and ctype.startswith("text/html")
    page = body.decode()
    refs = re.findall(r'(?:src|href)="/static/([^"]+)"', page)
    assert set(refs) == {"style.css", "app.js", "diagram.js", "actions.js", "tou.js",
                         "alpinejs-3.14.1.min.js"}
    for name in refs:
        status, ctype, _ = get(f"{base}/static/{name}")
        assert status == 200
        assert ctype.split(";")[0] == {"css": "text/css", "js": "text/javascript"}[name.rsplit(".", 1)[1]]


@pytest.mark.parametrize("path", ["/static/../server.py", "/static/server.py", "/static/%2e%2e/server.py",
                                  "/static/", "/static/nope.js", "/server.py"])
def test_only_shipped_files_are_served(ui, path):
    _, base = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + path)
    assert e.value.code == 404


def test_control_api_on_the_same_port(ui):
    _, base = ui
    _, ctype, body = get(base + "/gateways")
    assert ctype == "application/json" and [g["id"] for g in json.loads(body)] == ["site", "us-split"]
    req = urllib.request.Request(base + "/gateways/site/load", method="POST",
                                 data=json.dumps({"w": 1234}).encode())
    with urllib.request.urlopen(req, timeout=3) as r:
        assert json.load(r)["load_w"] == pytest.approx(1234, abs=1)
    assert "actions" in json.loads(get(base + "/schema")[2])


def test_vendored_alpine_is_upstream():
    data = (STATIC / "alpinejs-3.14.1.min.js").read_bytes()
    assert hashlib.sha256(data).hexdigest() == ALPINE_SHA256


def test_page_reads_only_fields_the_state_has(ui):
    """Every g.<field> / current.<field> the page uses exists in the control API's state."""
    fleet, _ = ui
    state = gateway_state(fleet["site"])
    text = "".join((STATIC / n).read_text() for n in ("index.html", "app.js", "diagram.js", "actions.js", "tou.js"))
    used = set(re.findall(r"\b(?:g|current)\.([a-z_]+)", text))
    assert used and used <= set(state), sorted(used - set(state))
    leg_fields = set(re.findall(r"\bleg\.([a-z_]+)", text))
    leg_state = set(next(iter(state["phases"].values())))
    assert leg_fields and leg_fields <= leg_state, sorted(leg_fields - leg_state)
    assert state["native_mode_name"] == "Self-Consumption"


def test_ui_command_refuses_a_bad_fleet(tmp_path, capsys):
    bad = tmp_path / "fleet.yaml"
    bad.write_text("gateways:\n  - {id: a, colour: red}\n  - {id: a}\n")
    assert main(["ui", str(bad)]) == 2
    err = capsys.readouterr().err
    assert "gateways[0].colour: unknown key" in err and "gateways[1].id: duplicate" in err


def test_state_carries_what_the_diagram_draws(ui):
    fleet, _ = ui
    state = gateway_state(fleet["site"])
    assert state["pv_inputs"] == {"proximal": 1.0}
    assert state["apower_units"] == 1
    assert state["apower_rating"] == {"wh": 13600, "w": 5000}    # emulator constants
    assert set(state["pv_by_input"]) >= set(state["pv_inputs"])


# -- actions panel -----------------------------------------------------------------------------
JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")


def test_every_action_has_one_group():
    from franklinwh_emulator.schema import ACTIONS, GROUPS
    listed = [a for names in GROUPS.values() for a in names]
    assert sorted(listed) == sorted(ACTIONS) and len(listed) == len(set(listed))
    assert all(ACTIONS[a]["group"] for a in ACTIONS)


def test_page_handles_every_field_type():
    from franklinwh_emulator.schema import ACTIONS
    kinds = json.loads(re.search(r"const FIELD_KINDS = (\[.*?\]);",
                                 (STATIC / "actions.js").read_text()).group(1).replace("'", '"'))
    used = {t for a in ACTIONS.values() for f in a["fields"].values()
            for t in (f["type"] if isinstance(f["type"], list) else [f["type"]])}
    assert used <= set(kinds), sorted(used - set(kinds))


@pytest.mark.skipif(not JSC.exists(), reason="needs a JavaScript runtime (JavaScriptCore)")
def test_forms_rebuild_every_example(tmp_path):
    """Each schema example, entered as form values, builds the same request body again."""
    from franklinwh_emulator.schema import schema
    harness = tmp_path / "harness.js"
    harness.write_text((STATIC / "actions.js").read_text() + f"""
const S = {json.dumps(schema()["actions"])};
const out = [];
for (const [name, a] of Object.entries(S)) {{
  for (const ex of a.examples) {{
    let got;
    try {{ got = buildBody(a, formFromBody(a, ex)); }} catch (e) {{ got = 'ERROR ' + e.message; }}
    out.push([name, ex, got]);
  }}
}}
const bad = [];
for (const [n, f] of [['load', {{w: 'abc'}}], ['csip', {{control: '[1]'}}], ['csip', {{control: '{{'}}]]) {{
  try {{ buildBody(S[n], f); bad.push('accepted'); }} catch (e) {{ bad.push(e.message); }}
}}
print(JSON.stringify({{out, bad, blank: buildBody(S.grid, formFromBody(S.grid, {{}})), modeOpts: choiceOptions(S.mode.fields.mode), appOpts: choiceOptions(S.appliance.fields.name, {{appliances: {{ev: {{circuit: 2}}, base: {{circuit: null}}}}}}), noApps: choiceOptions(S.appliance.fields.name, {{appliances: {{}}}})}}));
""")
    import subprocess
    res = json.loads(subprocess.run([str(JSC), str(harness)], capture_output=True, text=True,
                                    check=True).stdout)
    mismatches = [(n, ex, got) for n, ex, got in res["out"] if got != ex]
    assert not mismatches, mismatches
    assert len(res["out"]) == sum(len(a["examples"]) for a in schema()["actions"].values())
    assert res["bad"] == ["w: not a number", "control: needs a JSON object", "control: not valid JSON"]
    assert res["blank"] == {}
    assert res["appOpts"] == [["", "— not set —"], ["ev", "ev · SW2"], ["base", "base"]]
    assert res["noApps"] == [["", "— this gateway has no appliances —"]]
    assert res["modeOpts"] == [["", "— not set —"], ["1", "1 · Emergency Backup"],
                               ["2", "2 · Self-Consumption"], ["3", "3 · TOU"]]


def test_state_has_agate_status_sun_and_temperatures(ui):
    from franklinwh_emulator.clock import ManualClock
    fleet, _ = ui
    s = gateway_state(fleet["site"])
    assert s["run_status_name"] in ("Standby", "Charging", "Discharging")
    assert s["inverter_state_name"] in ("Running", "Standby", "Throttled", "Fault", "Off")
    assert "cabinet_c" in s and "ambient_c" in s
    assert gateway_state(fleet["us-split"])["sun"] is None          # no location, no sun
    noon = Fleet.from_dict(FLEET, clock=ManualClock(1768446000.0))   # 2026-01-15 14:00 Sydney
    night = Fleet.from_dict(FLEET, clock=ManualClock(1768474800.0))  # 22:00
    day_sun = gateway_state(noon["site"])["sun"]
    night_sun = gateway_state(night["site"])["sun"]
    assert day_sun["up"] and day_sun["elevation_deg"] > 60
    assert not night_sun["up"] and night_sun["elevation_deg"] < 0


def test_version_endpoint(ui):
    from franklinwh_emulator import __version__
    _, base = ui
    v = json.loads(get(base + "/version")[2])
    assert v["version"] == __version__ and "T" in v["started"]
    assert v["commit"] is None or re.fullmatch(r"[0-9a-f]{7,12}", v["commit"])


def test_overrides_are_in_the_state(ui):
    from franklinwh_emulator.control import apply
    fleet, _ = ui
    gw = fleet["us-split"]
    assert gateway_state(gw)["overrides"] == {"solar_w": None, "load_w": None, "ambient_c": None}
    apply(gw, "pv", {"solar_w": 6000})
    s = gateway_state(gw)
    assert s["overrides"]["solar_w"] == 6000 and s["solar_w"] <= 6000 + 1    # capped by the sun (#56)
    apply(gw, "pv", {"solar_w": None})
    assert gateway_state(gw)["overrides"]["solar_w"] is None


def test_appliance_errors_name_the_fix(ui):
    from franklinwh_emulator.control import ApiError, apply
    fleet, _ = ui
    with pytest.raises(ApiError, match="this gateway has no appliances: add `loads:`"):
        apply(fleet["us-split"], "appliance", {"name": "Water Heater", "on": True})
    with pytest.raises(ApiError, match=r"no appliance 'Water Heater'; this gateway has \[.*'hot_water'"):
        apply(fleet["site"], "appliance", {"name": "Water Heater", "on": True})
    apply(fleet["site"], "appliance", {"name": "hot_water", "on": True})
    assert gateway_state(fleet["site"])["appliances"]["hot_water"]["forced"] is True


def test_state_has_firmware_and_active_reserve(ui):
    from franklinwh_emulator.control import apply
    fleet, _ = ui
    gw = fleet["us-split"]
    s = gateway_state(gw)
    assert s["firmware"] == "V10R01B04D00" and s["offgrid_floor_soc"] == 5.0
    apply(gw, "reserve", {"self": 25, "tou": 40})
    for mode, reserve in ((2, 25), (3, 40), (1, 100)):           # Self, TOU, Emergency Backup
        apply(gw, "mode", {"mode": mode})
        assert gateway_state(gw)["active_reserve"] == reserve
    v12 = Fleet.from_dict({"gateways": [{"id": "x", "profile": "agate-v12r02b60d00-candidate"},
                                        {"id": "y", "profile": "spec"}]})
    assert gateway_state(v12["x"])["firmware"] == "V12R02B60D00 (candidate)"
    assert gateway_state(v12["y"])["firmware"] == "SunSpec DER (spec)"


# -- TOU schedule editor (#58) -----------------------------------------------------------------
def test_schema_lists_the_tou_choices(ui):
    _, base = ui
    t = json.loads(get(base + "/schema")[2])["tou"]
    assert t["dispatch"]["7"]["code"] == "GRID_EXPORT" and set(t["dispatch"]) == {"1", "2", "3", "6", "7", "8"}
    assert t["waves"] == {"0": "Off-Peak", "1": "Mid-Peak", "2": "On-Peak", "4": "Super Off-Peak"}
    assert "maxChargeSoc" in t["limit_fields"]


@pytest.mark.skipif(not JSC.exists(), reason="needs a JavaScript runtime (JavaScriptCore)")
def test_tou_editor_round_trips_the_schedule(ui, tmp_path):
    """The state's schedule, opened in the editor and sent unchanged, is accepted and gives the
    same schedule; edits, the timeline and a bad price convert as they should."""
    import subprocess
    fleet, base = ui
    gw = fleet["us-split"]
    from franklinwh_emulator.control import apply
    apply(gw, "tou", {"schedule": [
        {"name": "Peak", "startHourTime": "17:00", "endHourTime": "21:00", "waveType": 2, "dispatchId": 7,
         "maxChargeSoc": 90}], "rates": {"2": {"buy": 0.54}}})
    tou = gateway_state(gw)["tou"]
    harness = tmp_path / "tou.js"
    harness.write_text((STATIC / "tou.js").read_text() + f"""
const T = {json.dumps(tou)};
const d = touDraft(T);
d.rates['0'] = {{buy: '', sell: ''}};
const same = touBody(d);
const edited = touDraft(T);
touAddRow(edited);
edited.rows[0].dispatchId = '8';
const full = touDraft({{schedule: [{{name: 'a', startHourTime: '00:00', endHourTime: '17:00'}},
                                   {{name: 'b', startHourTime: '17:00', endHourTime: '24:00'}}]}});
touAddRow(full);
const empty = touAddRow({{rows: [], rates: {{}}}});
let bad;
try {{ const b = touDraft(T); b.rates['2'].sell = 'x'; touBody(b); bad = 'accepted'; }} catch (e) {{ bad = e.message; }}
print(JSON.stringify({{same, edited: touBody(edited), segs: touSegments(T.schedule),
                      now: touNowPct({{endHourTime: '17:00', remaining_min: 60}}), limits: touLimits(d.rows[0].extra), bad,
                      full: full.rows.map(r => r.start + '-' + r.end), empty: empty.rows.map(r => r.start + '-' + r.end)}}));
""")
    res = json.loads(subprocess.run([str(JSC), str(harness)], capture_output=True, text=True, check=True).stdout)
    assert res["same"]["rates"] == {"2": {"buy": 0.54}}
    assert len(res["same"]["schedule"]) == 1                  # the gaps stay gaps
    assert res["same"]["schedule"][0] == {"maxChargeSoc": 90, "name": "Peak", "startHourTime": "17:00",
                                          "endHourTime": "21:00", "dispatchId": 7, "waveType": 2}
    assert [(s["left"], s["width"], s["dispatch"]) for s in res["segs"]] == [
        (0, 17 / 24 * 100, "SELF"), (17 / 24 * 100, 4 / 24 * 100, "GRID_EXPORT"), (21 / 24 * 100, 3 / 24 * 100, "SELF")]
    assert res["now"] == pytest.approx(16 / 24 * 100)
    assert res["limits"] == "maxChargeSoc 90"
    assert res["bad"] == "rates 2 sell: not a number"
    assert res["full"] == ["00:00-17:00", "17:00-20:30", "20:30-24:00"]   # the day was full: split the last
    assert res["empty"] == ["00:00-24:00"]
    assert res["edited"]["schedule"][0]["dispatchId"] == 8
    assert res["edited"]["schedule"][1] == {"name": "New", "startHourTime": "21:00", "endHourTime": "24:00",
                                            "dispatchId": 6, "waveType": 0}

    def post(body):
        req = urllib.request.Request(base + "/gateways/us-split/tou", method="POST", data=json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)
    status, state = post(res["same"])
    assert status == 200 and state["tou"]["schedule"] == tou["schedule"] and state["tou"]["rates"] == tou["rates"]
    status, state = post(res["edited"])
    assert status == 200 and [(b["startHourTime"], b["dispatch"]) for b in state["tou"]["schedule"]] == [
        ("00:00", "SELF"), ("17:00", "GRID_CHARGE"), ("21:00", "SELF")]
    assert not state["tou"]["schedule"][2].get("filled")      # the added row, not a gap
    edited = res["edited"]
    edited["schedule"][1]["startHourTime"] = "20:00"          # overlaps: the server's answer
    status, err = post(edited)
    assert status == 400 and err["error"].startswith("tou: 'Peak' (17:00-21:00) overlaps")   # one prefix


def test_active_gateway_select_marks_the_option():
    """#62: the select's options render after `active` is restored, so each option carries
    :selected; a select-level :value showed the first gateway after a reload."""
    page = (STATIC / "index.html").read_text()
    select = re.search(r'<select id="active-gw"[^>]*>(.*?)</select>', page, re.S)
    assert select and ":value" not in select.group(0).split(">", 1)[0]
    assert ':selected="g.id === active"' in select.group(1)
