"""Generate a register baseline JSON from a franklinwh-modbus full-scan text dump.

Usage::

    python tools/gen_baseline.py <AGATE_FULL_SCAN_*.txt> > src/franklinwh_emulator/baselines/<name>.json

The scan prints *scaled* values (``241.8 V``); the baseline stores *raw* register
values, so each point is divided back by its scale factor from the same model.
Unimplemented points (``None``) stay ``null`` and are served as the SunSpec
unimplemented sentinel. Repeating-group counts (NPt/NCrv/...) are recorded so the
emulator rebuilds each model at exactly the scanned length.

Only top-level points appear in the scan; repeating-group contents (curve points,
per-port DC data) are not captured and are left zero/unimplemented.
"""

from __future__ import annotations

import json
import re
import sys

from sunspec2.device import Model

_MODEL_RE = re.compile(r"^Model (\d+):")
_POINT_RE = re.compile(r"^\s+(\d+)\s+(\S+)\s+=\s+(.*?)\s+\((\w+)\)\s*$")
_COUNT_POINTS = {"NPt", "NCrv", "NCrvSet", "NCtl", "NPrt"}


def parse(path: str) -> dict[int, dict]:
    models: dict[int, dict] = {}
    cur: dict | None = None
    for line in open(path, encoding="utf-8"):
        if m := _MODEL_RE.match(line):
            cur = models.setdefault(int(m.group(1)), {"addr": None, "points": {}})
            continue
        if cur is None:
            continue
        if line.startswith("====="):
            cur = None
            continue
        if m := _POINT_RE.match(line):
            addr, name, value, ptype = m.groups()
            if name == "ID":
                cur["addr"] = int(addr)
            cur["points"][name] = (value, ptype)
    return models


def _number(value: str) -> float | None:
    if value.startswith("None"):
        return None
    return float(value.split()[0])


def to_raw(mid: int, scanned: dict) -> dict:
    pts = scanned["points"]
    length = int(_number(pts["L"][0]))
    counts = {n: int(_number(v)) for n, (v, _) in pts.items() if n in _COUNT_POINTS}
    # Build the model once just to get point defs (sf names, types).
    model = Model(mid, model_len=length, data=_header(mid, length, counts))
    sfs = {n: int(_number(v)) for n, (v, t) in pts.items() if t == "sunssf" and _number(v) is not None}
    raw: dict[str, object] = {}
    for name, (value, ptype) in pts.items():
        if name in ("ID", "L"):
            continue
        point = model.points.get(name)
        if point is None:
            continue
        if ptype in ("string",):
            raw[name] = value
            continue
        if ptype == "pad":
            continue
        num = _number(value)
        if num is None:
            raw[name] = None
            continue
        sf_name = point.pdef.get("sf")
        sf = sfs.get(sf_name, 0) if isinstance(sf_name, str) else 0
        raw[name] = int(round(num / (10 ** sf)))
    return {"id": mid, "len": length, "counts": counts, "points": raw}


def _header(mid: int, length: int, counts: dict[str, int]) -> bytes:
    from sunspec2.device import get_model_def
    import struct

    regs = [0] * (length + 2)
    regs[0], regs[1] = mid, length
    off = 0
    for p in get_model_def(mid)["group"]["points"]:
        if p["name"] in counts:
            regs[off] = counts[p["name"]]
        off += p["size"]
    return struct.pack(">%dH" % len(regs), *regs)


def main(path: str) -> None:
    scanned = parse(path)
    order = sorted(scanned, key=lambda mid: scanned[mid]["addr"])
    out = {
        "source": path.rsplit("/", 1)[-1],
        "base_display_offset": 40000,
        "models": [to_raw(mid, scanned[mid]) for mid in order],
    }
    json.dump(out, sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
