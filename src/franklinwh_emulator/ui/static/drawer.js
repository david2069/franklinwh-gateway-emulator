/* Where the actions go (#361): docked left, right or below the gateways, or only in a drawer.
 * Right-clicking a card or a map marker (a long press on a touch screen) selects that gateway and
 * opens the actions in a side drawer, whatever the layout. The drawer is not modal: no backdrop,
 * the page stays usable; Esc or × closes it and focus goes back where it was.
 *
 * Pure functions (no DOM), so they can be tested without a browser.
 */
const LAYOUTS = { left: 'Actions: left', right: 'Actions: right', below: 'Actions: below', drawer: 'Actions: drawer' };
const LONG_PRESS_MS = 500;

// The remembered layout (localStorage emulator.layout): 'right' and 'below' are the old toggle's
// values and stay as they were; anything else falls back to 'right'.
function layoutFrom(stored) {
  return Object.prototype.hasOwnProperty.call(LAYOUTS, stored) ? stored : 'right';
}

// The remembered drawer side: 'left' or 'right' (default).
function sideFrom(stored) {
  return stored === 'left' ? 'left' : 'right';
}

// Choosing a docked side also moves the drawer there; below and drawer keep its side.
function sideForLayout(layout, side) {
  return layout === 'left' || layout === 'right' ? layout : sideFrom(side);
}

// Whether the actions panel shows: docked layouts always (with a gateway), 'drawer' only when open.
function actionsShown(layout, drawerOpen) {
  return layout !== 'drawer' || !!drawerOpen;
}
