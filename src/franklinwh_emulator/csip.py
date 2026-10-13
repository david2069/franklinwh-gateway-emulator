"""The aGate's CSIP-AUS (IEEE 2030.5) client, and the utility server it talks to.

On a real aGate the 2030.5 client is configured through Direct Connect ``1205
der_comms`` (``enable``, ``uri``, ``dcap2030_5``, ``lfdi``, ``sfdi``, ``pin``,
``status2030_5``); none of it is visible over Modbus. Registration, not the
``enable`` flag, is what makes it live (see franklinwh-direct-connect-api
docs/PROTOCOL.md).

The emulator does not speak 2030.5. It models what the client would *do*: once
registered it applies the utility's active DERControl, or the DefaultDERControl when no
event is active or the server has been unreachable for ``comms_loss_s`` (CSIP-AUS
requires falling back to the default on loss of communications). Tests play the
utility by posting controls through the control API.

Controls (CSIP-AUS / SA TS 5573 core set; watts, booleans):
    opModExpLimW   site export limit         opModImpLimW   site import limit
    opModGenLimW   DER generation limit      opModLoadLimW  DER consumption limit
    opModConnect   False = disconnect DER     opModEnergize  False = cease to energise
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

CONTROLS = ("opModExpLimW", "opModImpLimW", "opModGenLimW", "opModLoadLimW",
            "opModConnect", "opModEnergize")
_BOOL_CONTROLS = {"opModConnect", "opModEnergize"}


def lfdi_for(serial: str) -> str:
    """40-hex-digit LFDI. Real LFDIs are the leading 160 bits of the SHA-256 of the
    device certificate; with no certificate the serial is hashed instead, which keeps
    one serial → one LFDI."""
    return hashlib.sha256(f"franklinwh-emulator:{serial}".encode()).hexdigest()[:40].upper()


def sfdi_for(lfdi: str) -> int:
    """IEEE 2030.5 SFDI: leading 36 bits of the LFDI as decimal, plus a check digit
    making the digit sum a multiple of 10."""
    base = int(lfdi[:9], 16)
    check = (10 - sum(int(d) for d in str(base)) % 10) % 10
    return base * 10 + check


def validate(controls: dict) -> dict:
    bad = set(controls) - set(CONTROLS)
    if bad:
        raise ValueError(f"unknown CSIP controls {sorted(bad)}; choose from {CONTROLS}")
    out = {}
    for k, v in controls.items():
        if v is None:
            continue
        out[k] = bool(v) if k in _BOOL_CONTROLS else float(v)
    return out


@dataclass
class DerControl:
    mrid: str
    controls: dict
    start: float
    duration_s: float | None          # None = until replaced or cleared

    def active(self, ts: float) -> bool:
        return self.start <= ts and (self.duration_s is None or ts < self.start + self.duration_s)


@dataclass
class CsipClient:
    enabled: bool = False
    uri: str = ""
    pin: str = ""
    lfdi: str = ""
    sfdi: int = 0
    registered: bool = False
    server_up: bool = True            # can the client reach the utility server?
    register_delay_s: float = 5.0
    comms_loss_s: float = 300.0       # unreachable this long → DefaultDERControl
    default: dict = field(default_factory=dict)
    events: list[DerControl] = field(default_factory=list)
    _enabled_at: float | None = None
    _down_since: float | None = None
    _seq: int = 0

    def configure(self, ts: float, *, enabled: bool | None = None, uri: str | None = None,
                  pin: str | None = None, serial: str = "") -> None:
        if uri is not None:
            self.uri = uri
        if pin is not None:
            self.pin = pin
        if enabled is not None and enabled != self.enabled:
            self.enabled = enabled
            self.registered = False
            self._enabled_at = ts if enabled else None
            if enabled and not self.lfdi:
                self.lfdi = lfdi_for(serial)
                self.sfdi = sfdi_for(self.lfdi)

    def set_server(self, ts: float, up: bool) -> None:
        if up and not self.server_up:
            self._down_since = None
        if not up and self.server_up:
            self._down_since = ts
        self.server_up = up

    def tick(self, ts: float) -> None:
        """Advance registration."""
        if (self.enabled and not self.registered and self.server_up
                and self._enabled_at is not None and ts - self._enabled_at >= self.register_delay_s):
            self.registered = True

    def post(self, ts: float, controls: dict, duration_s: float | None = None,
             start: float | None = None) -> DerControl:
        self._seq += 1
        ev = DerControl(mrid=f"EMU{self._seq:08d}", controls=validate(controls),
                        start=ts if start is None else start, duration_s=duration_s)
        self.events.append(ev)
        return ev

    def comms_lost(self, ts: float) -> bool:
        return self._down_since is not None and ts - self._down_since >= self.comms_loss_s

    def status(self, ts: float) -> str:
        if not self.enabled:
            return "disabled"
        if not self.registered:
            return "registering"
        if self.comms_lost(ts):
            return "comms-lost"
        return "registered"

    def active_event(self, ts: float) -> DerControl | None:
        live = [e for e in self.events if e.active(ts)]
        return live[-1] if live else None

    def effective(self, ts: float) -> tuple[dict, str]:
        """(controls in force, source) — source is none/event/default/comms-loss-default."""
        if not (self.enabled and self.registered):
            return {}, "none"
        if self.comms_lost(ts):
            return dict(self.default), "comms-loss-default"
        ev = self.active_event(ts)
        if ev is not None:
            return {**self.default, **ev.controls}, "event"
        return dict(self.default), "default" if self.default else "none"

    def summary(self, ts: float) -> dict:
        controls, source = self.effective(ts)
        ev = self.active_event(ts)
        return {
            "enabled": self.enabled, "uri": self.uri, "lfdi": self.lfdi, "sfdi": self.sfdi,
            "status": self.status(ts), "server_up": self.server_up,
            "controls": controls, "source": source, "default": dict(self.default),
            "active_event": None if ev is None else {
                "mrid": ev.mrid, "controls": ev.controls, "start": ev.start,
                "remaining_s": None if ev.duration_s is None
                else max(0.0, ev.start + ev.duration_s - ts)},
        }
