import socket
import struct

import pytest

from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.gateway import Gateway, GatewayConfig
from franklinwh_emulator.site import SiteConfig

NOON_UTC = 1_767_225_600 + 12 * 3600      # 2026-01-01 12:00 UTC


@pytest.fixture
def clock():
    return ManualClock(NOON_UTC)


def make_gateway(clock=None, **kw) -> Gateway:
    site_kw = kw.pop("site", {})
    site_kw.setdefault("tz_offset_hours", 0)
    kw.setdefault("setup", "in-service")      # Modbus on from the start, unless a test says otherwise
    cfg = GatewayConfig(**kw)
    cfg.site = SiteConfig(seed=cfg.seed, **site_kw)
    return Gateway(cfg, clock=clock)


@pytest.fixture
def gateway(clock):
    gw = make_gateway(clock, id="t", seed=7, site={"initial_soc": 60})
    gw.start()
    yield gw
    gw.stop()


class RawClient:
    """Minimal Modbus TCP client so tests see exactly what is on the wire."""

    def __init__(self, host, port, unit=1, timeout=2.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.unit = unit
        self.tid = 0

    def _call(self, pdu: bytes) -> bytes:
        self.tid += 1
        self.sock.sendall(struct.pack(">HHHB", self.tid, 0, len(pdu) + 1, self.unit) + pdu)
        head = self._recv(7)
        tid, _, length, _ = struct.unpack(">HHHB", head)
        assert tid == self.tid
        return self._recv(length - 1)

    def _recv(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return buf

    def read(self, address, count):
        r = self._call(struct.pack(">BHH", 3, address, count))
        if r[0] & 0x80:
            return ("exc", r[1])
        return list(struct.unpack(">%dH" % (r[1] // 2), r[2:]))

    def write(self, address, values):
        if isinstance(values, int):
            r = self._call(struct.pack(">BHH", 6, address, values))
        else:
            r = self._call(struct.pack(">BHHB", 16, address, len(values), 2 * len(values))
                           + struct.pack(">%dH" % len(values), *values))
        return ("exc", r[1]) if r[0] & 0x80 else "ok"

    def close(self):
        self.sock.close()


@pytest.fixture
def client(gateway):
    c = RawClient(*gateway.address)
    yield c
    c.close()
