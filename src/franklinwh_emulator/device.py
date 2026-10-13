"""The aGate's Modbus register space, bound to a :class:`~franklinwh_emulator.site.Site`.

Three regions are served (PDU addresses):

* the SunSpec chain at 0..1127 (plus any profile ``base_aliases``),
* FranklinWH extension registers 15000–15513 (15500–15513 documented, 15000–15039
  undocumented diagnostics), and
* the undocumented 16000–16499 block (16000 = high-resolution home load).

Anything else raises ILLEGAL DATA ADDRESS, as 40000+ does on a real aGate. Writes
are decoded to SunSpec points and given the profile's semantics: some drive the
site (M704 VPP control, SPAN-unlocked mode/reserves), some are accepted and
silently discarded, the rest are stored as written.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

from .profiles import Profile
from .site import MODE_NAMES, Site, Snapshot
from .sunspec import SunSpecImage, _walk, load_baseline

AU_A_CODES = ("as4777-au-a", "ausgrid")

ILLEGAL_FUNCTION = 1
ILLEGAL_ADDRESS = 2
ILLEGAL_VALUE = 3

EXT_START, EXT_END = 15000, 15514          # exclusive end
HIRES_START, HIRES_END = 16000, 16500

# 15000–15043 as dumped from a live aGate (FRANKLINWH_EXTENSIONS_MANIFEST.md); live
# mirrors are overwritten on every refresh.
_DIAG_STATIC = {
    15007: 257, 15011: 583, 15012: 0xFFFF, 15013: 0xEC7A, 15015: 61, 15016: 2,
    15018: 0xFFFF, 15019: 0xFFFF, 15021: 1, 15022: 5581, 15024: 0xC331, 15026: 1,
    15029: 70, 15030: 30761, 15033: 22, 15034: 22637, 15035: 268, 15040: 0xFFF7,
    15043: 1,
}

_M702_SETTINGS = {"WMax", "WMaxOvrExt", "WOvrExtPF", "WMaxUndExt", "WUndExtPF", "VAMax",
                  "VarMaxInj", "VarMaxAbs", "WChaRteMax", "WDisChaRteMax", "VAChaRteMax",
                  "VADisChaRteMax", "VNom", "VMax", "VMin", "AMax", "PFOvrExt", "PFUndExt",
                  "IntIslandCat"}


def _q(value: float, step: int) -> int:
    return int(round(value / step) * step) if step > 1 else int(round(value))


def _u16(v: int) -> int:
    return int(v) & 0xFFFF


@dataclass
class WriteResult:
    exception: int | None = None


class AGateDevice:
    def __init__(self, site: Site, profile: Profile, *, serial: str,
                 span_unlock: bool = False):
        self.site = site
        self.profile = profile
        self.serial = serial
        self.span_unlock = span_unlock
        self.image = SunSpecImage(load_baseline(profile.baseline))
        self.ext = [0] * (EXT_END - EXT_START)
        self.hires = [0] * (HIRES_END - HIRES_START)
        for a, v in _DIAG_STATIC.items():
            self.ext[a - EXT_START] = v
        self.lock = threading.RLock()
        self.write_log: list[tuple[int, list[int], str]] = []   # (addr, values, outcome)
        img = self.image
        img.set(1, "SN", serial)
        img.set(1, "Vr", profile.firmware)
        img.set(1, "Md", profile.model_name)
        img.set(701, "ACType", site.config.ac_type)
        if profile.m702_wmaxrtg is not None:
            img.set(702, "WMaxRtg", profile.m702_wmaxrtg)
        img.set(713, "WHRtg", min(0xFFFF, int(site.capacity_wh)))
        # 703 enter-service settings from the grid code. The AU firmware reports volts
        # (V_SF -1) in ESVHi/ESVLo despite the SunSpec %VRef unit; keep that convention.
        code = site.grid
        es_lo, es_hi = code.es_volts()
        img.set(703, "ESVHi", int(round(es_hi * 10)))
        img.set(703, "ESVLo", int(round(es_lo * 10)))
        img.set(703, "ESHzHi", int(round(code.es_hz[1] * 100)))
        img.set(703, "ESHzLo", int(round(code.es_hz[0] * 100)))
        img.set(703, "ESDlyTms", int(code.es_delay_s))
        img.set(703, "ESRmpTms", int(code.es_ramp_s))
        # 704–712 curve groups are the reference aGate's (Australia A, scanned 2026-10-07).
        # For other grid codes there is no real scan to copy, so they read unimplemented
        # rather than invented.
        if code.name not in AU_A_CODES:
            for mid in (705, 706, 707, 708, 709, 710, 711, 712):
                for path, point in _walk(img.models[mid]):
                    if "[" in path and not path.endswith(("ActPt", "ReadOnly")):
                        point.value = None

    # -- addressing -------------------------------------------------------------
    def _sunspec_offset(self, address: int, count: int) -> int | None:
        """Map an address in the chain (base 0 or an alias) to base 0, else None."""
        for base in (0, *self.profile.base_aliases):
            a = address - base
            if 0 <= a and a + count <= self.image.end:
                return a
        return None

    # -- reads ------------------------------------------------------------------
    def read(self, address: int, count: int) -> list[int] | int:
        with self.lock:
            snap = self.site.step()
            self.refresh(snap)
            a = self._sunspec_offset(address, count)
            if a is not None:
                return self.image.read(a, count)
            if EXT_START <= address and address + count <= EXT_END:
                return self.ext[address - EXT_START:address - EXT_START + count]
            if HIRES_START <= address and address + count <= HIRES_END:
                return self.hires[address - HIRES_START:address - HIRES_START + count]
            return ILLEGAL_ADDRESS

    def refresh(self, s: Snapshot) -> None:
        """Copy live site state into the register image."""
        p, img, site = self.profile, self.image, self.site
        legs = site.phases
        grid = int(round(s.grid_w))
        unused = None if p.unused_phase_registers == "unimplemented" else 0
        v = {ph: st.v_ln for ph, st in s.phases.items()}
        v_ln10 = int(round(s.v_ln * 10))

        # Grid / inverter state. DERMode: bit 0 following, bit 1 forming, bit 2 PV clipped.
        img.set(701, "St", 0 if s.inverter_state in (6, 7) else 1)
        img.set(701, "InvSt", s.inverter_state)
        img.set(701, "ConnSt", 1 if s.grid_connected else 0)
        img.set(701, "Alrm", s.alarms)
        der_mode = 1 if s.grid_connected else 2
        if s.solar_clipped_w > 1:
            der_mode |= 4
        img.set(701, "DERMode", der_mode)
        # The reference aGate leaves ThrotPct unimplemented (scan 2026-10-07).
        img.set(701, "ThrotPct", None if p.throtpct_unimplemented else s.throttle_pct)
        img.set(714, "PrtAlrms", s.dc_alarms)

        # Per-leg points; legs not wired read 0 (as on the single-phase scan) or
        # unimplemented, per profile.
        total_a10 = 0
        for ph in ("L1", "L2", "L3"):
            st = s.phases.get(ph)
            if st is None:
                for name in ("W", "VA", "Var", "PF", "A", "V", "TotWhInj", "TotWhAbs"):
                    img.set(701, f"{name}{ph}", unused)
                continue
            w, q = int(round(st.grid_w)), int(round(st.var))
            va = math.hypot(w, q)
            a10 = int(round(va / st.v_ln * 10)) if st.v_ln > 1 else 0
            total_a10 += a10
            img.set(701, f"W{ph}", w)
            img.set(701, f"VA{ph}", int(round(va)))
            img.set(701, f"Var{ph}", q)
            img.set(701, f"PF{ph}", int(round(w / va * 1000)) if va else 0)
            img.set(701, f"A{ph}", a10)
            img.set(701, f"V{ph}", int(round(st.v_ln * 10)))
            img.set(701, f"TotWhInj{ph}", int(st.export_wh))
            img.set(701, f"TotWhAbs{ph}", int(st.import_wh))
        pairs = [("L1", "L2"), ("L2", "L3"), ("L3", "L1")]
        ll = {f"V{a}{b}": int(round(site.v_ll(a, b, v) * 10))
              for a, b in pairs if a in s.phases and b in s.phases}
        if len(legs) == 1:
            # The single-phase aGate reports its L-N voltage as VL1L2 and LLV.
            ll = {"VL1L2": v_ln10}
        for name in ("VL1L2", "VL2L3", "VL3L1"):
            img.set(701, name, ll.get(name, unused))
        q = int(round(s.var))
        va = math.hypot(grid, q)
        img.set(701, "W", grid)
        img.set(701, "VA", int(round(va)))
        img.set(701, "Var", q)
        img.set(701, "PF", int(round(grid / va * 1000)) if va else 0)
        img.set(701, "A", total_a10)
        img.set(701, "LLV", int(round(sum(ll.values()) / len(ll))))
        img.set(701, "LNV", v_ln10)
        img.set(701, "Hz", int(round(s.hz * 1000)))
        img.set(701, "TotWhInj", int(s.grid_export_wh))
        img.set(701, "TotWhAbs", int(s.grid_import_wh))
        img.set(701, "TmpAmb", int(round(s.ambient_c * 10)))
        img.set(701, "TmpCab", int(round(s.cabinet_c * 10)))

        r = site.remote
        img.set(704, "WSetEna", int(r.enabled))
        img.set(704, "WSetMod", r.mode)
        img.set(704, "WSet", r.watts if p.wset_positive_charge else -r.watts)
        pct = r.pct_hw if p.wsetpct_positive_discharge else -r.pct_hw
        img.set(704, "WSetPct", int(round(pct * 10)))
        img.set(704, "WSetEnaRvrt", int(r.revert_enabled))
        img.set(704, "WSetRvrtTms", r.revert_s)
        img.set(704, "WSetRvrtRem", r.remaining(s.ts))

        # Real exports: 99 % of 30 days of 10 s samples sit on whole percent and 100 W steps.
        soc10 = int(round(s.soc)) * 10 if p.soc_whole_percent else int(round(s.soc * 10))
        img.set(713, "SoC", soc10)
        img.set(713, "WHAvail", min(0xFFFF, int(s.soc / 100 * site.capacity_wh)))
        if not p.m713_sta_zero:
            # 713.Sta enum is not modelled by FranklinWH; report 1 (OK) when spec.
            img.set(713, "Sta", 1)

        batt = _q(s.battery_w, p.quantize_w)
        img.set(714, "DCW", batt)
        if not p.m714_port_zero:
            img.set(714, "Prt[0].DCW", batt)
        img.set(714, "DCWhInj", int(s.batt_discharge_wh))
        img.set(714, "DCWhAbs", int(s.batt_charge_wh))
        if not p.m714_port_zero:
            img.set(714, "Prt[0].DCWhInj", int(s.batt_discharge_wh))
            img.set(714, "Prt[0].DCWhAbs", int(s.batt_charge_wh))
        if not p.m714_dca_zero:
            img.set(714, "DCA", int(round(batt / 400)))
            img.set(714, "Prt[0].DCV", 400)

        if p.locremctl_fixed_local:
            img.set(715, "LocRemCtl", 1)
        else:
            img.set(715, "LocRemCtl", 0 if r.enabled else 1)

        solar_q = _q(s.solar_w, p.quantize_w)
        img.set(502, "OutPw", solar_q)
        img.set(502, "OutWh", int(s.pv_wh) & 0xFFFFFFFF)

        e = self.ext
        pv_wh = int(s.pv_wh) & 0xFFFFFFFF
        tou = site.self_reserve if p.tou_reserve_mirrors_self else site.tou_reserve
        for addr, val in {
            15502: solar_q,
            15503: _q(s.pv_by_input.get("proximal", 0), p.quantize_w),
            15504: _q(s.pv_by_input.get("remote1", 0), p.quantize_w),
            15505: _q(s.pv_by_input.get("remote2", 0), p.quantize_w),
            15506: _q(s.load_w, p.quantize_w),
            15507: site.native_mode, 15508: site.self_reserve, 15509: tou,
            15510: pv_wh >> 16, 15511: pv_wh & 0xFFFF,
            15512: pv_wh >> 16, 15513: pv_wh & 0xFFFF,
            15017: int(round(s.ambient_c)), 15020: min(0xFFFF, int(site.capacity_wh)),
            15025: v_ln10, 15036: img.get(713, "SoH") or 0,
            # Mapped from the 2026-10-07 reference scan (franklinwh-modbus#18): exact matches
            # within one scan, so live here rather than static.
            15011: int(round(s.load_w)),                  # candidate: = 16000 home load
            15024: int(round(s.hz * 1000)),               # grid frequency, mHz
            15029: (int(s.grid_export_wh) >> 16) & 0xFFFF, 15030: int(s.grid_export_wh) & 0xFFFF,
            15033: (int(s.grid_import_wh) >> 16) & 0xFFFF, 15034: int(s.grid_import_wh) & 0xFFFF,
            15040: img.get(701, "PF") if img.get(701, "PF") is not None else 0,
        }.items():
            e[addr - EXT_START] = _u16(val)
        self.hires[0] = _u16(int(round(s.load_w)))
        self.hires[1] = _u16(site.self_reserve)
        # 16002: the reference read 20 with both reserves at 5 % and TOU at 15 %; 20 is
        # 1406 BBBackupSoc (candidate, franklinwh-modbus#18), not the TOU reserve.
        self.hires[2] = _u16(20)

    # -- writes -----------------------------------------------------------------
    def write(self, address: int, values: list[int]) -> int | None:
        """Apply a write; return a Modbus exception code or None."""
        with self.lock:
            self.site.step()
            a = self._sunspec_offset(address, len(values))
            if a is not None:
                return self._write_sunspec(address, a, values)
            if EXT_START <= address and address + len(values) <= EXT_END:
                for i, v in enumerate(values):
                    self._write_ext(address + i, v)
                return None
            if HIRES_START <= address and address + len(values) <= HIRES_END:
                self.write_log.append((address, list(values), "discarded"))
                return None
            return ILLEGAL_ADDRESS

    def _write_ext(self, address: int, value: int) -> None:
        site = self.site
        outcome = "discarded"
        if self.span_unlock and address in (15507, 15508, 15509):
            if address == 15507 and value in MODE_NAMES:
                site.set_native_mode(value)
                outcome = "applied"
            elif address == 15508 and 0 <= value <= 100:
                site.set_reserves(self_reserve=value)
                outcome = "applied"
            elif address == 15509 and 0 <= value <= 100:
                site.set_reserves(tou_reserve=value)
                outcome = "applied"
        self.write_log.append((address, [value], outcome))

    def _write_sunspec(self, address: int, offset: int, values: list[int]) -> int | None:
        p, site = self.profile, self.site
        decoded = self.image.decode_write(offset, values)
        if p.readonly_write == "exception":
            for ref, _ in decoded:
                if ref.point.pdef.get("access", "R") != "RW":
                    return ILLEGAL_ADDRESS
        remote: dict = {}
        outcomes = []
        for ref, raw in decoded:
            mid, name = ref.model_id, ref.path
            access = ref.point.pdef.get("access", "R")
            outcome = "applied"
            if access != "RW":
                outcome = "discarded"
            elif mid == 704 and name == "WSetEna":
                remote["enabled"] = bool(raw)
            elif mid == 704 and name == "WSetMod":
                remote["mode"] = int(raw or 0)
            elif mid == 704 and name == "WSet":
                w = int(raw or 0)
                remote["watts"] = w if p.wset_positive_charge else -w
            elif mid == 704 and name == "WSetPct":
                pct = (raw or 0) / 10.0
                remote["pct_hw"] = pct if p.wsetpct_positive_discharge else -pct
            elif mid == 704 and name == "WSetEnaRvrt":
                remote["revert_enabled"] = bool(raw)
            elif mid == 704 and name == "WSetRvrtTms":
                remote["revert_s"] = int(raw or 0)
            elif mid == 704 and name == "WMaxLimPctEna" and p.discard_wmaxlimpct_ena:
                outcome = "discarded"
            elif mid == 704 and name == "VarSetEna" and p.discard_varset_ena:
                outcome = "discarded"
            elif mid == 715 and name == "AlarmReset":
                if raw:
                    site.reset_alarms()
                ref.point.value = raw
            elif mid == 715 and name == "ControllerHb" and p.discard_controller_hb:
                outcome = "discarded"
            elif mid == 715 and name == "LocRemCtl" and p.locremctl_fixed_local:
                outcome = "discarded"
            elif mid == 702 and name in _M702_SETTINGS and p.discard_m702_settings:
                outcome = "discarded"
            else:
                ref.point.value = raw                     # sticky setting
            outcomes.append(f"{mid}.{name}={raw}:{outcome}")
        if remote:
            remote["revert_effective"] = p.reversion_effective
            site.update_remote(**remote)
        self.write_log.append((address, list(values), ", ".join(outcomes) or "no points"))
        return None
