"""#374: Force events' gateways. The picker's filters (Country, State / province, Postcode,
Timezone) and a multi-select drop-down of the gateways they match replace the wall of checkboxes;
"all" is still sent when every gateway is selected, and selections a filter hides stay selected."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from franklinwh_emulator.ui.server import STATIC

JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")

GWS = [
    {"id": "syd", "location": {"country": "AU", "state": "NSW", "postcode": "2000", "timezone": "Australia/Sydney"}},
    {"id": "mel", "location": {"country": "AU", "state": "VIC", "postcode": "3000", "timezone": "Australia/Melbourne"}},
    {"id": "phx", "location": {"country": "US", "state": "AZ", "timezone": "America/Phoenix"}},
    {"id": "site", "location": {}, "local_time": "2026-10-31T21:33:00+13:00"},
]


@pytest.mark.skipif(not JSC.exists(), reason="needs a JavaScript runtime (JavaScriptCore)")
def test_selection_counts_targets_and_hidden(tmp_path):
    harness = tmp_path / "force.js"
    harness.write_text((STATIC / "groups.js").read_text() + f"""
const G = {json.dumps(GWS)};
const ALL = {{all: true, ids: []}};
const au = {{country: 'AU'}};
let s = forceToggle(G, ALL, 'mel');                     // untick one: the rest, no longer "all"
const untick = s;
const retick = forceToggle(G, s, 'mel');                 // tick it again: "all" again
const cleared = {{all: false, ids: []}};
const matching = forceSelectMatching(G, au, cleared);   // AU only
const plus = forceToggle(G, matching, 'site');            // + one the AU filter hides
print(JSON.stringify({{
  all: [forceTargets(G, ALL), forceSelText(G, {{}}, ALL), forceSelText(G, au, ALL)],
  untick: [forceTargets(G, untick), forceSelText(G, {{}}, untick)],
  retick: [retick, forceTargets(G, retick)],
  cleared: [forceTargets(G, cleared), forceSelText(G, au, cleared)],
  matching: [forceTargets(G, matching), forceSelText(G, au, matching)],
  plus: [forceTargets(G, plus), forceSelText(G, au, plus), forceSelText(G, {{}}, plus)],
  everyMatching: forceSelectMatching(G, {{}}, cleared),
  order: forceTargets(G, forceToggle(G, forceToggle(G, cleared, 'phx'), 'syd')),
  narrowed: withFilter(G, {{country: 'AU', state: 'VIC'}}, 'country', 'US'),
  kept: withFilter(G, {{country: 'AU'}}, 'state', 'VIC'),
}}));
""")
    r = json.loads(subprocess.run([str(JSC), str(harness)], capture_output=True, text=True, check=True).stdout)
    # the default, as the old "all" box: targets "all"; a filter hides some of them: counted
    assert r["all"] == ["all", "All 4 gateways selected", "All 4 gateways selected · 2 hidden by filter"]
    assert r["untick"] == [["syd", "phx", "site"], "3 of 4 gateways selected"]
    assert r["retick"] == [{"all": True, "ids": []}, "all"]
    assert r["cleared"] == [[], "No gateways selected"]
    assert r["matching"] == [["syd", "mel"], "2 of 4 gateways selected"]
    # "site" stays selected under the AU filter, and says so
    assert r["plus"] == [["syd", "mel", "site"], "3 of 4 gateways selected · 1 hidden by filter",
                         "3 of 4 gateways selected"]
    assert r["everyMatching"] == {"all": True, "ids": []}       # no filter: select all matching = all
    assert r["order"] == ["syd", "phx"]                           # fleet order, not click order
    # the picker's narrowing, unchanged (#142): checked in PICK_FILTERS order, so with VIC set a
    # country without VIC is the value cleared; a state the country has is kept
    assert r["narrowed"] == {"country": "", "state": "VIC"}
    assert r["kept"] == {"country": "AU", "state": "VIC"}


def test_the_form_has_filters_and_a_drop_down_not_a_checkbox_wall():
    page = (STATIC / "index.html").read_text()
    app = (STATIC / "app.js").read_text()
    css = (STATIC / "style.css").read_text()
    form = re.search(r'<form class="force-form".*?</form>', page, re.S).group(0)
    # no checkbox per gateway in the form itself: they are inside the drop-down's list
    assert 'x-model="force.all"' not in form and 'x-for="g in gateways"' not in form
    # the picker's filters (all four, in its order, always shown) and its narrowing
    assert 'x-for="[f, label] in PICK_FILTERS"' in form and 'x-show' not in form.split('class="force-filters"')[1].split('class="multi"')[0]
    assert '@change="setForceFilter(f, $event.target.value)"' in form
    assert "this.force.filters = withFilter(this.gateways, this.force.filters, f, v)" in app
    assert "this.pickFilters = withFilter(this.gateways, this.pickFilters, f, v)" in app
    # the drop-down: a button with the count, aria-expanded, a list of what the filters match
    assert re.search(r'<button type="button" id="force-gw" class="multi-btn" @click="forceMenu\(!force.open\)"\s+'
                     r':aria-expanded="force.open.toString\(\)" aria-controls="force-menu">', form)
    assert '<span x-text="forceCount()"></span>' in form
    assert 'x-for="g in forceList()"' in form and '@change="forceToggle(g.id)"' in form
    assert '@click="forceAllMatching()"' in form and '@click="forceClear()">Clear</button>' in form
    # Esc and a click outside close it; Esc doesn't also close the drawer
    assert '@click.outside="force.open = false" @keydown.escape="forceMenuEsc($event)"' in form
    esc = re.search(r"forceMenuEsc\(e\) \{.*?\n    \},", app, re.S).group(0)
    assert "e.stopPropagation()" in esc and "getElementById('force-gw')" in esc and "focus()" in esc
    # sent as before: "all" or a list
    assert "targets: forceTargets(this.gateways, this.force.sel)" in app
    assert "force: { event: '', sel: { all: true, ids: [] }," in app       # every gateway by default
    # the list scrolls inside the menu; the menu is no wider than the form
    assert "overflow-y: auto" in re.search(r"\.multi-list \{[^}]*\}", css).group(0)
    menu = re.search(r"\.multi-menu \{[^}]*\}", css).group(0)
    assert "position: absolute" in menu and "left: 0; right: 0" in menu
