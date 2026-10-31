"""Draw docs/assets/model-flow.svg: what drives the emulator's site model.

Hand-laid on purpose (auto-layout tangles it): edit BOXES and EDGES, then run
``python3 tools/gen_model_flow.py``. Boxes marked planned are drawn dashed with their issue.
Each box carries its own fill, so the diagram reads on light and dark pages alike.
"""

from pathlib import Path

W, H = 1060, 800
OUT = Path(__file__).resolve().parents[1] / "docs" / "assets" / "model-flow.svg"

# id: (x, y, w, h, title, subtitle, kind, planned issue or None)
BOXES = {
    "loc": (20, 40, 190, 52, "Location", "lat, lon, timezone", "in", None),
    "wx": (20, 150, 190, 52, "Weather model", "bell · synthetic · Open-Meteo", "in", None),
    "place": (20, 300, 190, 52, "Placement", "indoor / outdoor, sun, cover", "in", None),
    "ctrl": (20, 410, 190, 52, "Controls", "Modbus · Direct Connect · UI", "in", None),
    "code": (20, 500, 190, 52, "Grid code", "trips, ride-through, responses", "in", None),
    "util": (20, 585, 190, 84, "Utility limits", "utility · DNSP (AU) · lines company (NZ): export limit or ban, import limit, battery export", "in", None),
    "svc": (20, 700, 190, 52, "Service main breaker", "amps; trips on overload", "in", None),
    "sun": (275, 40, 190, 52, "Sun position", "height, east/west, local time", "mid", None),
    "irr": (275, 130, 190, 52, "Sunlight on panels", "cloud, haze", "mid", None),
    "air": (275, 210, 190, 52, "Outside air temperature", "", "mid", None),
    "cab": (275, 300, 190, 52, "Cabinet temperature", "lags its surroundings", "mid", None),
    "pv": (535, 100, 200, 52, "PV output", "hot panels give less", "eff", None),
    "home": (535, 210, 200, 52, "Home load", "appliances, aircon, heater/fan", "eff", None),
    "lim": (535, 300, 200, 52, "Battery limits", "SoC, reserve, temperature", "eff", None),
    "tgt": (535, 410, 200, 52, "Battery target", "mode, TOU, FORCE", "eff", None),
    "glim": (535, 590, 200, 84, "Grid limits", "export limit or ban; import cap (stop charging, then discharge); breaker trip", "eff", None),
    "bal": (810, 250, 230, 64, "Site balance", "solar + battery + grid = home", "bal", None),
    "out": (810, 430, 230, 72, "Outputs", "battery, SoC, grid, energy, alarms → Modbus · Direct Connect · UI", "out", None),
}
# (from, to, "h" right-to-left side | "v" bottom-to-top, dashed?)
EDGES = [
    ("loc", "sun", "h"), ("sun", "irr", "v"), ("wx", "irr", "h"), ("wx", "air", "h"),
    ("irr", "pv", "h"), ("air", "pv", "h"), ("air", "home", "h"), ("irr", "cab", "h"),
    ("air", "cab", "v"), ("place", "cab", "h"), ("cab", "lim", "h"), ("cab", "home", "h"),
    ("ctrl", "tgt", "h"), ("util", "glim", "h"), ("svc", "glim", "h"), ("code", "bal", "h"),
    ("pv", "bal", "h"), ("home", "bal", "h"), ("lim", "bal", "h"), ("tgt", "bal", "h"),
    ("glim", "bal", "h"), ("bal", "out", "v"),
]
FILL = {"in": "#dcecf7", "mid": "#e6f2ee", "eff": "#f4eedc", "bal": "#d6efe4", "out": "#ece6f4"}
STROKE = {"in": "#3a6f96", "mid": "#2f7a64", "eff": "#8a6d1f", "bal": "#1f7a52", "out": "#5d4a86"}
GREY = "#7a8a99"


def side(b, where):
    x, y, w, h = BOXES[b][:4]
    return {"R": (x + w, y + h / 2), "L": (x, y + h / 2), "T": (x + w / 2, y), "B": (x + w / 2, y + h)}[where]


def path(a, b, kind):
    if kind == "v":
        down = BOXES[b][1] > BOXES[a][1]
        (x1, y1), (x2, y2) = (side(a, "B"), side(b, "T")) if down else (side(a, "T"), side(b, "B"))
        return f"M{x1},{y1} L{x2},{y2}"
    (x1, y1), (x2, y2) = side(a, "R"), side(b, "L")
    if abs(y1 - y2) < 1:
        return f"M{x1},{y1} L{x2},{y2}"
    mx = x2 - 26
    return f"M{x1},{y1} L{mx},{y1} L{mx},{y2} L{x2},{y2}"


def wrap(text, width=36):
    words, lines, line = text.split(" "), [], ""
    for w in words:
        if line and len(line) + 1 + len(w) > width:
            lines.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    return lines + ([line] if line else [])


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;")


def main():
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
         'aria-labelledby="t d" font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif">',
         "<title id=\"t\">How the inputs drive the emulator's site model</title>",
         '<desc id="d">Location, weather, placement, controls, the grid code, utility limits and the service '
         'breaker feed sun position, sunlight, outside air and cabinet temperature; these set PV output, home '
         'load, battery limits, the battery target and grid limits; the site balance combines them into the '
         'outputs served over Modbus TCP, Direct Connect and the UI. Battery power heats the cabinet. Dashed '
         'boxes are planned.</desc>',
         f'<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
         f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{GREY}"/></marker></defs>']
    for x, t in [(115, "Inputs"), (370, "Conditions"), (635, "Demand and limits"), (925, "Balance and outputs")]:
        o.append(f'<text x="{x}" y="22" text-anchor="middle" font-size="13" font-weight="600" fill="{GREY}">{t}</text>')
    for a, b, k in EDGES:
        o.append(f'<path d="{path(a, b, k)}" fill="none" stroke="{GREY}" stroke-width="1.6" marker-end="url(#a)"/>')
    # Feedback: battery power warms the cabinet.
    bx, by, bw, bh = BOXES["bal"][:4]
    cx, cy, cw, ch = BOXES["cab"][:4]
    loop_y = by + bh + 68
    o.append(f'<path d="M{bx + 30},{by + bh} L{bx + 30},{loop_y} L{cx + cw / 2},{loop_y} L{cx + cw / 2},{cy + ch}" '
             f'fill="none" stroke="{GREY}" stroke-width="1.4" stroke-dasharray="5 4" marker-end="url(#a)"/>')
    o.append(f'<text x="{cx + cw / 2 + 12}" y="{loop_y + 16}" font-size="11.5" fill="{GREY}">'
             "battery power warms the cabinet</text>")
    for x, y, w, h, title, sub, kind, planned in BOXES.values():
        dash = ' stroke-dasharray="6 4"' if planned else ""
        o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{FILL[kind]}" '
                 f'stroke="{STROKE[kind]}" stroke-width="1.4"{dash}/>')
        lines = wrap(sub) if sub else []
        if planned:
            lines.append(f"planned {planned}")
        ty = y + h / 2 - 6 * len(lines) + 4
        o.append(f'<text x="{x + w / 2}" y="{ty}" text-anchor="middle" font-size="13.5" font-weight="600" '
                 f'fill="#14232f">{esc(title)}</text>')
        for i, line in enumerate(lines):
            colour = "#8a4b00" if planned and i == len(lines) - 1 else "#33434f"
            o.append(f'<text x="{x + w / 2}" y="{ty + 16 + i * 14}" text-anchor="middle" font-size="11.5" '
                     f'fill="{colour}">{esc(line)}</text>')
    # Legend
    o.append(f'<rect x="810" y="740" width="34" height="18" rx="4" fill="#f4f4f4" stroke="{GREY}" '
             'stroke-dasharray="6 4"/>')
    o.append(f'<text x="852" y="753" font-size="11.5" fill="{GREY}">dashed = planned (issue shown)</text>')
    o.append("</svg>")
    OUT.write_text("\n".join(o) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
