"""Direct Connect (TCP/9000, "sendMqtt" frames) frontend for an emulated aGate.

Speaks the aGate's local broker protocol using franklinwh_local's own framing (cipher,
len/crc) so the emulator and the client library can never disagree about the wire
format. Every reply is rendered from the same :class:`~franklinwh_emulator.site.Site`
the Modbus frontend uses, so both ports describe one consistent device: a CSIP control
or an outage shows up on both, and a ``1205 sunsMdEn`` write closes :502.

Requires the optional dependency ``franklinwh-direct-connect-api`` (``pip install
franklinwh-gateway-emulator[dc]``).

Unsupported in-band cmdTypes get ``result:1, reason:4``; out-of-band codes close the
connection; some wrong-shape requests get no reply at all, all as a real aGate does
(see :mod:`.dc_payloads`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

try:
    from franklinwh_local import catalog as dc_catalog
    from franklinwh_local import protocol as dc_protocol
except ImportError as e:  # pragma: no cover - exercised only without the extra
    raise ImportError("the Direct Connect frontend needs franklinwh-direct-connect-api "
                      "(pip install franklinwh-gateway-emulator[dc])") from e

from .dc_payloads import DROP, DcPayloads

if TYPE_CHECKING:
    from .gateway import Gateway

log = logging.getLogger(__name__)

LOGIN_REQUEST = 1101


class DirectConnectServer:
    def __init__(self, gateway: "Gateway", host: str = "127.0.0.1", port: int = 0):
        self.gateway = gateway
        self.host = host
        self.port = port
        self.payloads = DcPayloads(gateway)
        self.requests = 0
        self.request_log: list[tuple[float, int, Any]] = []   # (ts, cmdType, dataArea)
        self._server: asyncio.base_events.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def equip_no(self) -> str:
        return self.gateway.serial

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

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        faults = self.gateway.dc_faults
        if faults.offline:
            writer.close()
            return
        stream = dc_protocol.FrameStream()
        self._writers.add(writer)
        try:
            while True:
                chunk = await reader.read(8192)
                if not chunk:
                    return
                for frame in stream.feed(chunk):
                    reply = self._reply(frame)
                    if reply is DROP:
                        return
                    if reply is None:
                        continue
                    if faults.latency_s:
                        await asyncio.sleep(faults.latency_s)
                    if faults.drop_responses > 0:
                        faults.drop_responses -= 1
                        continue
                    writer.write(reply)
                    await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    def _reply(self, frame) -> Any:
        if not frame.is_request:
            return None
        self.requests += 1
        data = frame.data_area if isinstance(frame.data_area, dict) else {}
        self.request_log.append((time.time(), frame.cmd_type, data))
        if len(self.request_log) > 1000:
            del self.request_log[:500]
        faults = self.gateway.dc_faults
        handler: Callable[[dict], Any] | None = self.payloads.handlers.get(frame.cmd_type)
        if faults.exception is not None:
            body: Any = {"opt": 0, "result": 1, "reason": faults.exception}
        elif handler is None:
            log.debug("%s: no handler for cmdType %s", self.equip_no, frame.cmd_type)
            body = self.payloads.unhandled(frame.cmd_type)
        else:
            body = handler(data)
        if body is None or body is DROP:
            return body
        resp_cmd = dc_catalog.response_for(frame.cmd_type)
        return dc_protocol.encode_frame(resp_cmd, self.equip_no, body, snno=frame.snno,
                                        time_stamp=int(time.time()))
