"""The UI server: pages and static files beside the control API, on one loopback port."""

import hashlib
import json
import re
import urllib.error
import urllib.request

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
    assert set(refs) == {"style.css", "app.js", "alpinejs-3.14.1.min.js"}
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
    text = (STATIC / "index.html").read_text() + (STATIC / "app.js").read_text()
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
