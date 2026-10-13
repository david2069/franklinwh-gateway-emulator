"""A small asyncio Modbus TCP server in front of an :class:`AGateDevice`.

Hand-rolled rather than pymodbus's server so the emulator controls exactly what a
real aGate does on the wire: one client at a time, any unit id answered the same,
writes acknowledged even when discarded, and injectable faults.

Function codes: 3 (read holding), 4 (read input — served from the same space),
6 (write single), 16 (write multiple). Anything else → ILLEGAL FUNCTION.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass

from .device import ILLEGAL_FUNCTION, ILLEGAL_VALUE, AGateDevice

log = logging.getLogger(__name__)


@dataclass
class Faults:
    """Live-tunable fault injection (mutate the fields while serving)."""
    latency_s: float = 0.0          # delay before every response
    offline: bool = False           # accept, then close immediately (device rebooting)
    session_held: bool = False      # behave as if another client owns the session
    drop_responses: int = 0         # swallow the next N responses (client times out)
    reset_after: int | None = None  # drop the connection after N more requests
    exception: int | None = None    # answer every request with this exception code


class ModbusServer:
    def __init__(self, device: AGateDevice, host: str = "127.0.0.1", port: int = 0,
                 faults: Faults | None = None):
        self.device = device
        self.host = host
        self.port = port
        self.faults = faults or Faults()
        self.requests = 0
        self._active = 0
        self._server: asyncio.base_events.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> tuple[str, int]:
        self._server = await asyncio.start_server(self._client, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.host, self.port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            for w in list(self._writers):
                w.close()
            await self._server.wait_closed()
            self._server = None

    # -- connections ----------------------------------------------------------------
    def _busy(self) -> bool:
        limit = self.device.profile.max_connections
        return self.faults.session_held or (limit is not None and self._active >= limit)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        if self.faults.offline:
            writer.close()
            return
        if self._busy():
            log.info("%s: session busy, %s client %s", self.device.serial,
                     self.device.profile.busy_behaviour, peer)
            if self.device.profile.busy_behaviour == "hang":
                self._writers.add(writer)
                try:
                    while await reader.read(1024):
                        pass               # port open, never answers
                finally:
                    self._writers.discard(writer)
            writer.close()
            return
        self._active += 1
        self._writers.add(writer)
        try:
            await self._serve(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._active -= 1
            self._writers.discard(writer)
            writer.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            header = await reader.readexactly(7)
            tid, pid, length, unit = struct.unpack(">HHHB", header)
            if length < 2 or length > 254:
                return
            pdu = await reader.readexactly(length - 1)
            self.requests += 1
            f = self.faults
            if f.reset_after is not None:
                if f.reset_after <= 0:
                    f.reset_after = None
                    return
                f.reset_after -= 1
            reply = self._handle(pdu) if f.exception is None else bytes([pdu[0] | 0x80, f.exception])
            if f.latency_s:
                await asyncio.sleep(f.latency_s)
            if f.drop_responses > 0:
                f.drop_responses -= 1
                continue
            # One write per frame: clients doing a single recv() must get it whole.
            writer.write(struct.pack(">HHHB", tid, pid, len(reply) + 1, unit) + reply)
            await writer.drain()

    # -- PDUs ----------------------------------------------------------------------
    def _handle(self, pdu: bytes) -> bytes:
        fc = pdu[0]
        try:
            if fc in (3, 4):
                address, count = struct.unpack(">HH", pdu[1:5])
                if not 1 <= count <= 125:
                    return bytes([fc | 0x80, ILLEGAL_VALUE])
                regs = self.device.read(address, count)
                if isinstance(regs, int):
                    return bytes([fc | 0x80, regs])
                return bytes([fc, count * 2]) + struct.pack(">%dH" % count, *regs)
            if fc == 6:
                address, value = struct.unpack(">HH", pdu[1:5])
                exc = self.device.write(address, [value])
                return bytes([fc | 0x80, exc]) if exc else pdu[:5]
            if fc == 16:
                address, count, nbytes = struct.unpack(">HHB", pdu[1:6])
                if not 1 <= count <= 123 or nbytes != count * 2 or len(pdu) < 6 + nbytes:
                    return bytes([fc | 0x80, ILLEGAL_VALUE])
                values = list(struct.unpack(">%dH" % count, pdu[6:6 + nbytes]))
                exc = self.device.write(address, values)
                return bytes([fc | 0x80, exc]) if exc else pdu[:5]
        except struct.error:
            return bytes([fc | 0x80, ILLEGAL_VALUE])
        return bytes([fc | 0x80, ILLEGAL_FUNCTION])
