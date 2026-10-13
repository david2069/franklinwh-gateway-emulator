"""SunSpec register image built from pysunspec2 model definitions.

Every model is a real :class:`sunspec2.device.Model`, constructed at the exact length
(and repeating-group counts) recorded in a baseline, so point offsets are correct by
construction. Live values are written into the models' points and the image is
re-rendered on demand; writes are decoded back to (model, point) pairs so the device
layer can decide what each write means.

Layout (PDU addresses, base 0, as served by a real aGate)::

    0..1     'SunS' marker
    2..      model chain in baseline order, each [ID, L, body...]
    end      0xFFFF, 0  (end-of-chain)
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from importlib import resources
from typing import Any, Iterator

from sunspec2.device import Group, Model, Point, get_model_def

SUNS = (0x5375, 0x6E53)
END_MARKER = (0xFFFF, 0x0000)


def load_baseline(name: str) -> dict:
    with resources.files("franklinwh_emulator.baselines").joinpath(f"{name}.json").open() as f:
        return json.load(f)


def _seed_bytes(mid: int, length: int, counts: dict[str, int]) -> bytes:
    """ID/L header plus repeating-group counts, enough for Model() to size its groups."""
    regs = [0] * (length + 2)
    regs[0], regs[1] = mid, length
    off = 0
    for p in get_model_def(mid)["group"]["points"]:
        if p["name"] in counts:
            regs[off] = counts[p["name"]]
        off += p["size"]
    return struct.pack(">%dH" % len(regs), *regs)


def _walk(group: Group, prefix: str = "") -> Iterator[tuple[str, Point]]:
    """Yield (path, point) for every point, groups as 'Crv[0].Pt[1].V'."""
    for name, point in group.points.items():
        yield prefix + name, point
    for gname, g in group.groups.items():
        items = g if isinstance(g, list) else [g]
        for i, sub in enumerate(items):
            yield from _walk(sub, f"{prefix}{gname}[{i}].")


@dataclass
class PointRef:
    model_id: int
    path: str          # 'WSetEna' or 'Prt[0].DCW'
    point: Point
    address: int       # absolute PDU address of the point's first register

    @property
    def length(self) -> int:
        return int(self.point.len)


class SunSpecImage:
    """The 'SunS' chain: models, their addresses, rendering and write decoding."""

    def __init__(self, baseline: dict, base: int = 0):
        self.base = base
        self.models: dict[int, Model] = {}
        self.model_addr: dict[int, int] = {}
        addr = base + 2
        for spec in baseline["models"]:
            mid, length = spec["id"], spec["len"]
            model = Model(mid, model_addr=addr, model_len=length,
                          data=_seed_bytes(mid, length, spec.get("counts", {})))
            if model.error_info.strip():
                raise ValueError(f"model {mid}: {model.error_info.strip()}")
            # Points the baseline does not mention start unimplemented, like the
            # real device's 0x8000/0xFFFF sentinels (counts stay as seeded).
            for path, point in _walk(model):
                if point.pdef["name"] not in ("ID", "L") and path not in spec.get("counts", {}):
                    point.value = None
            for name, value in spec.get("points", {}).items():
                if name in model.points:
                    model.points[name].value = value
            self.models[mid] = model
            self.model_addr[mid] = addr
            for path, value in spec.get("groups", {}).items():   # repeating-group points
                self.ref(mid, path).point.value = value
            addr += length + 2
        self.end = addr + 2           # exclusive end, after the end marker
        self._index: dict[int, PointRef] = {}
        for mid, model in self.models.items():
            for path, point in _walk(model):
                ref = PointRef(mid, path, point, self.model_addr[mid] + point.offset)
                for a in range(ref.address, ref.address + ref.length):
                    self._index[a] = ref

    # -- point access --------------------------------------------------------
    def ref(self, model_id: int, path: str) -> PointRef:
        model = self.models[model_id]
        node: Any = model
        parts = path.split(".")
        for part in parts[:-1]:
            gname, _, idx = part.partition("[")
            g = node.groups[gname]
            node = g[int(idx.rstrip("]"))] if isinstance(g, list) else g
        point = node.points[parts[-1]]
        return PointRef(model_id, path, point, self.model_addr[model_id] + point.offset)

    def get(self, model_id: int, path: str) -> Any:
        return self.ref(model_id, path).point.value

    def set(self, model_id: int, path: str, value: Any) -> None:
        self.ref(model_id, path).point.value = value

    def contains(self, address: int, count: int) -> bool:
        return self.base <= address and address + count <= self.end

    # -- rendering -------------------------------------------------------------
    def render(self) -> list[int]:
        regs: list[int] = list(SUNS)
        for model in self.models.values():
            data = model.get_mb()
            regs.extend(struct.unpack(">%dH" % (len(data) // 2), data))
        regs.extend(END_MARKER)
        return regs

    def read(self, address: int, count: int) -> list[int]:
        regs = self.render()
        off = address - self.base
        return regs[off:off + count]

    # -- write decoding --------------------------------------------------------
    def decode_write(self, address: int, values: list[int]) -> list[tuple[PointRef, Any]]:
        """Split a register write into (point, new raw value) pairs.

        A write covering only part of a multi-register point (e.g. one half of an
        int32) is merged with the point's current registers before decoding. Registers
        outside any point (the 'SunS' marker, the end marker) yield nothing.
        """
        touched: dict[str, PointRef] = {}
        for a in range(address, address + len(values)):
            ref = self._index.get(a)
            if ref is not None:
                touched.setdefault(f"{ref.model_id}:{ref.path}", ref)
        out: list[tuple[PointRef, Any]] = []
        for ref in touched.values():
            regs = self.read(ref.address, ref.length)
            for i in range(ref.length):
                a = ref.address + i
                if address <= a < address + len(values):
                    regs[i] = values[a - address]
            data = struct.pack(">%dH" % ref.length, *regs)
            info = ref.point.info
            raw = info.data_to(data)
            out.append((ref, raw if info.is_impl(raw) else None))
        return out
