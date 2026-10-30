"""The UI server: the pages, plus the control API on the same loopback port.

    GET /                   the UI
    GET /static/<file>      its files (only those shipped in ui/static)
    everything else         the control API (control.py): /schema, /gateways, ...
"""

from __future__ import annotations

from pathlib import Path

from ..control import ControlApi, Raw

STATIC = Path(__file__).resolve().parent / "static"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


def static_file(name: str) -> Raw | None:
    """A shipped file by bare name; anything else (paths, unknown names) is None."""
    files = {p.name: p for p in STATIC.iterdir() if p.is_file() and p.suffix in TYPES}
    p = files.get(name)
    return None if p is None else Raw(TYPES[p.suffix], p.read_bytes())


class UiServer(ControlApi):
    def _route(self, method: str, path: str, body: dict):
        p = path.split("?")[0]
        if method == "GET" and p in ("/", "/index.html"):
            return static_file("index.html")
        if method == "GET" and p.startswith("/static/"):
            f = static_file(p[len("/static/"):])
            if f is not None:
                return f
        return super()._route(method, path, body)
