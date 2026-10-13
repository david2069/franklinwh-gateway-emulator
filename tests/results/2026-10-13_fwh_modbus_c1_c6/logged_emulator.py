"""Run one emulated aGate (default profile) and log every TCP connection and Modbus request.

Test harness only: wraps ModbusServer methods at run time; the emulator code is unchanged.
    python logged_emulator.py LOG.jsonl PORT UNTIL_ISO
"""
import asyncio
import json
import struct
import sys
import time
from datetime import datetime

from franklinwh_emulator.gateway import Gateway, GatewayConfig
from franklinwh_emulator.server import ModbusServer

LOG, PORT, UNTIL = sys.argv[1], int(sys.argv[2]), datetime.fromisoformat(sys.argv[3]).timestamp()
_conn = {}


def emit(**rec):
    rec["ts"] = round(time.time(), 3)
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")


orig_client, orig_handle = ModbusServer._client, ModbusServer._handle


async def client(self, reader, writer):
    peer = writer.get_extra_info("peername")
    cid = f"{peer[0]}:{peer[1]}"
    busy = self._busy()
    emit(ev="connect", conn=cid, active_before=self._active, refused_busy=busy)
    _conn[id(writer)] = cid
    # remember the connection for _handle via the task
    asyncio.current_task().conn_id = cid
    try:
        await orig_client(self, reader, writer)
    finally:
        emit(ev="disconnect", conn=cid)


def handle(self, pdu):
    task = asyncio.current_task()
    cid = getattr(task, "conn_id", None)
    fc = pdu[0]
    rec = {"ev": "request", "conn": cid, "fc": fc}
    try:
        if fc in (3, 4, 6, 16):
            a, b = struct.unpack(">HH", pdu[1:5])
            rec["address"] = a
            rec["count" if fc in (3, 4, 16) else "value"] = b
            if fc == 16:
                n = pdu[5]
                rec["values"] = list(struct.unpack(">%dH" % (n // 2), pdu[6:6 + n]))
    except struct.error:
        rec["malformed"] = True
    reply = orig_handle(self, pdu)
    rec["exception"] = reply[1] if reply[0] & 0x80 else None
    if fc in (6, 16):
        rec["after_15507_15509"] = self.device.read(15507, 3)
    emit(**rec)
    return reply


ModbusServer._client, ModbusServer._handle = client, handle


async def main():
    gw = Gateway(GatewayConfig(id="agate", seed=0, host="127.0.0.1", port=PORT))
    host, port = await gw.start_async()
    emit(ev="start", host=host, port=port, profile=gw.device.profile.name,
         max_connections=gw.device.profile.max_connections,
         busy_behaviour=gw.device.profile.busy_behaviour,
         ext_15507_15509=gw.device.read(15507, 3))
    print(f"serving {host}:{port} until {sys.argv[3]}", flush=True)
    while time.time() < UNTIL:
        await asyncio.sleep(1)
    emit(ev="stop", requests=gw.server.requests)


asyncio.run(main())
