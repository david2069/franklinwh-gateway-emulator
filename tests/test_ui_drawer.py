"""#361: where the actions go (Left / Right / Below / Drawer) and the actions drawer: right-click
or a long press on a card or a map marker opens it, not modal, Esc or × closes it."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from franklinwh_emulator.ui.server import STATIC

JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")


@pytest.mark.skipif(not JSC.exists(), reason="needs a JavaScript runtime (JavaScriptCore)")
def test_layouts_their_migration_and_the_drawer_side(tmp_path):
    harness = tmp_path / "drawer.js"
    harness.write_text((STATIC / "drawer.js").read_text() + """
print(JSON.stringify({
  layouts: Object.keys(LAYOUTS),
  stored: [null, '', 'right', 'below', 'left', 'drawer', 'sideways', 'toString'].map(layoutFrom),
  side: [null, 'left', 'right', 'up'].map(sideFrom),
  forLayout: [['left', 'right'], ['right', 'left'], ['below', 'left'], ['drawer', 'left'], ['below', null]]
    .map(([l, s]) => sideForLayout(l, s)),
  shown: [['right', false], ['below', false], ['left', false], ['drawer', false], ['drawer', true]]
    .map(([l, o]) => actionsShown(l, o)),
  press: LONG_PRESS_MS,
}));
""")
    r = json.loads(subprocess.run([str(JSC), str(harness)], capture_output=True, text=True, check=True).stdout)
    assert r["layouts"] == ["left", "right", "below", "drawer"]
    # the old toggle's values stay as they were; anything unknown is 'right' (the old default)
    assert r["stored"] == ["right", "right", "right", "below", "left", "drawer", "right", "right"]
    assert r["side"] == ["right", "left", "right", "right"]
    assert r["forLayout"] == ["left", "right", "left", "left", "right"]
    assert r["shown"] == [True, True, True, False, True]
    assert r["press"] == 500


def test_cards_and_markers_open_the_drawer_and_it_is_not_modal():
    page = (STATIC / "index.html").read_text()
    app = (STATIC / "app.js").read_text()
    css = (STATIC / "style.css").read_text()
    card = re.search(r'<button type="button" class="card".*?>', page, re.S).group(0)
    assert '@contextmenu.prevent="openDrawer(g.id, $event)"' in card
    assert '@touchstart.passive="pressStart(g.id, $event)"' in card and 'pressEnd()' in card
    assert '@click="clickCard(g.id)"' in card
    assert "id => this.openDrawer(id)" in app                        # the map's right-click
    assert "m.off('contextmenu').on('contextmenu'" in (STATIC / "map.js").read_text()
    # one drop-down instead of the old toggle; remembered and migrated
    assert 'id="layout"' in page and "toggleLayout" not in page + app
    assert "this.layout = layoutFrom(localStorage.getItem('emulator.layout'))" in app
    assert '<script src="/static/drawer.js"></script>' in page
    # Esc and × close it; focus moves to × and back afterwards
    assert '@keydown.escape.window="closeDrawer()"' in page and 'id="drawer-close"' in page
    assert "back.focus()" in app and "document.getElementById('drawer-close')" in app
    # not modal: a fixed panel with no backdrop, and the page padded clear of it on wide screens
    drawer_css = re.search(r"main \.actions\.drawer \{[^}]*\}", css).group(0)
    assert "position: fixed" in drawer_css
    assert not re.search(r"\.backdrop|::backdrop|\.overlay", css)
    assert "aria-modal" not in page.split('class="actions')[1].split(">")[0]
    assert "main.drawer-on-right { padding-right: calc(var(--drawer-w) + 16px); }" in css
    assert "@media (max-width: 600px) { :root { --drawer-w: 100vw; } }" in css
    assert ".fleet-map {" in css and "isolation: isolate" in css     # Leaflet's layers under the drawer
