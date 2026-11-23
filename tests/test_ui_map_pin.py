"""#365: pin the map. With the map shown, a Pin toggle keeps it at the top of the cards' column
(position: sticky) while the cards scroll, in every actions layout; remembered by the browser;
under the drawer; at most 40% of the window's height (phones and short windows included)."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from franklinwh_emulator.ui.server import STATIC

JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")


@pytest.mark.skipif(not JSC.exists(), reason="needs a JavaScript runtime (JavaScriptCore)")
def test_pinning_tells_leaflet_to_measure_again(tmp_path):
    harness = tmp_path / "map.js"
    harness.write_text((STATIC / "map.js").read_text() + """
resizeFleetMap();                               // no map yet: nothing to do, no error
let n = 0;
_map = { invalidateSize() { n++; } };
resizeFleetMap();
print(JSON.stringify({ calls: n }));
""")
    r = json.loads(subprocess.run([str(JSC), str(harness)], capture_output=True, text=True, check=True).stdout)
    assert r["calls"] == 1


def test_the_pin_toggle_and_the_remembered_choice():
    page = (STATIC / "index.html").read_text()
    app = (STATIC / "app.js").read_text()
    # beside Map, shown only with the map, a pressed/unpressed toggle like Compact and Map
    pin = re.search(r'<button type="button" x-show="mapOn"[^>]*>Pin</button>', page, re.S).group(0)
    assert """@click="setView('mapPinned', !mapPinned)" :aria-pressed="mapPinned.toString()\"""" in pin
    assert page.index(">Map</button>") < page.index(pin)
    assert 'class="fleet-map" x-show="mapOn" x-cloak :class="{ pinned: mapPinned }"' in page
    assert "mapPinned: false," in app                                        # off by default
    assert "this.mapPinned = localStorage.getItem('emulator.mapPinned') === '1';" in app
    # setView stores it (in try/catch) and has Leaflet measure the new height
    set_view = re.search(r"setView\(key, value\) \{.*?\n    \},", app, re.S).group(0)
    assert "if (key === 'mapPinned') this.$nextTick(() => resizeFleetMap());" in set_view
    assert "try { localStorage.setItem('emulator.' + key" in set_view


def test_pinned_is_sticky_under_the_drawer_and_capped():
    css = (STATIC / "style.css").read_text()
    pinned = re.search(r"\.fleet-map\.pinned \{[^}]*\}", css).group(0)
    assert "position: sticky" in pinned and "top: 0" in pinned
    z = int(re.search(r"z-index: (\d+)", pinned).group(1))
    drawer_z = int(re.search(r"main \.actions\.drawer \{[^}]*z-index: (\d+)", css).group(1))
    splash_z = int(re.search(r"\.splash \{[^}]*z-index: (\d+)", css).group(1))
    assert 0 < z < drawer_z < splash_z
    assert "isolation: isolate" in re.search(r"\.fleet-map \{[^}]*\}", css).group(0)   # Leaflet's 400-1000 stay inside
    assert ".fleet-map.pinned #fleet-map { height: min(380px, 40vh); }" in css
    assert "@media (max-width: 600px) { .fleet-map.pinned #fleet-map { height: min(260px, 40vh); } }" in css
    # nothing between the map and the page clips or scrolls (sticky would stick to that instead)
    own = re.compile(r"main(\.[\w-]+)*|\.col-main")
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        if all(own.fullmatch(s.strip()) for s in sel.split(",")):
            assert "overflow" not in body, sel


def test_view_options_wrap_on_a_phone():
    """#373: with the map on, the Pin button made the view options 404 px wide; they didn't wrap,
    so at 390 px the page was 421 px wide and the drawer sat 31 px off-screen."""
    css = (STATIC / "style.css").read_text()
    assert "flex-wrap: wrap" in re.search(r"\.view-opts \{[^}]*\}", css).group(0)
