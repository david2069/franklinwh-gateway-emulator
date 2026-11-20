"""#352: the optional map of where the gateways are installed (map.js), after the Direct Connect
bridge's CloudFront PoP map: Leaflet 1.9.4 vendored and loaded on first use, a shipped outline (no
online tiles), one marker per place, the Map toggle remembered."""

import hashlib
import json
import re
import subprocess
import urllib.request
from pathlib import Path

import pytest

from franklinwh_emulator.gateway import Fleet
from franklinwh_emulator.ui.server import STATIC

JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")
LEAFLET_SHA256 = "db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a"   # as the DC bridge vendors


@pytest.mark.skipif(not JSC.exists(), reason="needs a JavaScript runtime (JavaScriptCore)")
def test_one_point_per_place_and_the_tooltip(tmp_path):
    gws = [
        {"id": "a", "soc": 41.6, "location": {"latitude": -33.87, "longitude": 151.21, "city": "Sydney"}, "group": None},
        {"id": "l1", "soc": 50, "location": {"latitude": -37.81, "longitude": 144.96, "city": "Melbourne"},
         "group": {"id": "au-3phase"}},
        {"id": "l2", "soc": 60, "location": {"latitude": -37.81, "longitude": 144.96, "city": "Melbourne"},
         "group": {"id": "au-3phase"}},
        {"id": "b", "soc": 70, "location": {"latitude": -33.87, "longitude": 151.21, "city": "Sydney"}, "group": None},
        {"id": "<b>x</b>", "soc": 10, "location": {"latitude": 1, "longitude": 2}, "group": None},
        {"id": "nowhere", "soc": 5, "location": {"latitude": None, "longitude": None}, "group": None},
        {"id": "bare", "soc": 5, "location": {}, "group": None},
    ]
    harness = tmp_path / "map.js"
    harness.write_text((STATIC / "map.js").read_text() + f"""
const P = mapPoints({json.dumps(gws)});
print(JSON.stringify({{pts: P.map(p => [p.key, p.lat, p.lon, p.city, p.items.map(g => g.id)]),
                      missing: mapMissing({json.dumps(gws)}), tips: P.map(mapTooltip)}}));
""")
    r = json.loads(subprocess.run([str(JSC), str(harness)], capture_output=True, text=True, check=True).stdout)
    assert r["pts"] == [["-33.870,151.210", -33.87, 151.21, "Sydney", ["a", "b"]],          # same place: one marker
                        ["-37.810,144.960", -37.81, 144.96, "Melbourne", ["l1", "l2"]],    # a group: one marker
                        ["1.000,2.000", 1, 2, "", ["<b>x</b>"]]]
    assert r["missing"] == 2
    assert r["tips"][0] == "<b>Sydney</b><br>a · 42%<br>b · 70%"
    assert r["tips"][1] == "<b>Melbourne</b><br>l1 · 50%<br>l2 · 60%"
    assert r["tips"][2] == "&lt;b&gt;x&lt;/b&gt; · 10%"                                    # ids escaped


def test_the_toggle_the_panel_and_the_remembered_choice():
    page = (STATIC / "index.html").read_text()
    app = (STATIC / "app.js").read_text()
    assert '<script src="/static/map.js"></script>' in page
    assert re.search(r"""@click="setView\('mapOn', !mapOn\)" :aria-pressed="mapOn.toString\(\)"[^>]*>Map</button>""", page)
    assert 'x-show="mapOn"' in page and 'id="fleet-map"' in page
    assert "mapOn: false," in app                                    # off by default
    assert "localStorage.getItem('emulator.mapOn') === '1'" in app   # remembered like Compact
    assert "drawFleetMap(mapPoints(this.gateways), this.active, id => this.select(id), id => this.openDrawer(id))" in app


def test_offline_leaflet_vendored_and_loaded_on_first_use():
    js = (STATIC / "map.js").read_text()
    assert hashlib.sha256((STATIC / "leaflet-1.9.4.min.js").read_bytes()).hexdigest() == LEAFLET_SHA256
    assert "/static/leaflet-1.9.4.min.js" in js and "/static/leaflet-1.9.4.css" in js
    assert "leaflet" not in (STATIC / "index.html").read_text()        # not on every page load
    assert "tileLayer" not in js and not re.search(r"https?://[^'\"]*\{z\}", js)   # no online tiles
    outline = json.loads((STATIC / "world-110m.json").read_text())
    kinds = {f["properties"]["k"] for f in outline["features"]}
    assert kinds == {"land", "border", "state"} and "Natural Earth" in outline["source"]
    css = (STATIC / "style.css").read_text()
    assert all(f"#fleet-map .map-{k}" in css for k in kinds)          # themed from the page's tokens


@pytest.fixture(scope="module")
def ui():
    fleet = Fleet.from_dict({"gateways": [
        {"id": "a", "seed": 3, "dc_port": 0, "location": {"latitude": 40.59, "longitude": -122.39, "city": "Redding"}},
        {"id": "b", "seed": 4, "dc_port": 0}]})
    fleet.start(control_port=0, ui=True)
    yield fleet, f"http://127.0.0.1:{fleet.control.port}"
    fleet.stop()


def test_the_map_files_are_served(ui):
    _, base = ui
    for name, ctype in (("map.js", "text/javascript"), ("leaflet-1.9.4.min.js", "text/javascript"),
                        ("leaflet-1.9.4.css", "text/css"), ("world-110m.json", "application/json")):
        with urllib.request.urlopen(f"{base}/static/{name}", timeout=5) as r:
            assert r.status == 200 and r.headers["Content-Type"].split(";")[0] == ctype, name
    with urllib.request.urlopen(f"{base}/gateways/a", timeout=5) as r:
        loc = json.load(r)["location"]
    assert (loc["latitude"], loc["longitude"], loc["city"]) == (40.59, -122.39, "Redding")   # what the map reads
