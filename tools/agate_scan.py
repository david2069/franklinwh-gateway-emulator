"""Read-only register scan of an aGate (or the emulator) over Modbus TCP.

One connection, holding-register reads only (function 3); never writes. It walks the
SunSpec chain from base 0 (marker, then each model's ID/L header and body), reads the
extension blocks 15000–15513 and 16000–16499, probes the base aliases 40000/50000 and one
out-of-map address, and records the exception codes and per-read latency.

    python tools/agate_scan.py <host> [port] out.json

Output holds the serial number: keep it out of public repositories.
"""
import json
import sys
import time

from pymodbus.client import ModbusTcpClient

CHUNK = 100
PAUSE_S = 0.05


def main():
    host = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 3 else 502
    out_path = sys.argv[-1]
    c = ModbusTcpClient(host, port=port, timeout=5, retries=1)
    if not c.connect():
        raise SystemExit(f"cannot connect to {host}:{port}")
    log: list[dict] = []

    def read(addr: int, count: int):
        t0 = time.monotonic()
        try:
            r = c.read_holding_registers(addr, count=count, device_id=1)
        except Exception as e:                      # noqa: BLE001 - recorded, not fatal
            log.append({"addr": addr, "count": count, "error": repr(e)})
            return None
        dt = round(time.monotonic() - t0, 3)
        time.sleep(PAUSE_S)
        if r.isError():
            log.append({"addr": addr, "count": count, "exception": getattr(r, "exception_code", None),
                        "s": dt})
            return None
        log.append({"addr": addr, "count": count, "s": dt})
        return list(r.registers)

    def read_block(addr: int, count: int) -> list[int | None]:
        regs: list[int | None] = []
        for a in range(addr, addr + count, CHUNK):
            n = min(CHUNK, addr + count - a)
            got = read(a, n)
            regs.extend(got if got is not None else [None] * n)
        return regs

    started = time.time()
    result: dict = {"host": host, "port": port, "started": started, "models": [], "blocks": {}}
    marker = read(0, 2)
    result["marker"] = marker
    if marker == [0x5375, 0x6E53]:                  # "SunS"
        addr = 2
        while True:
            hdr = read(addr, 2)
            if not hdr or hdr[0] == 0xFFFF:
                result["end"] = {"addr": addr, "hdr": hdr}
                break
            mid, length = hdr
            body = read_block(addr + 2, length)
            result["models"].append({"id": mid, "addr": addr, "len": length, "regs": body})
            addr += 2 + length
            if len(result["models"]) > 60:
                break
    result["blocks"]["15000"] = read_block(15000, 514)
    result["blocks"]["16000"] = read_block(16000, 500)
    result["aliases"] = {"40000": read(40000, 2), "50000": read(50000, 2)}
    result["out_of_map"] = {"addr": 3000, "regs": read(3000, 1)}
    c.close()
    result["duration_s"] = round(time.time() - started, 1)
    result["reads"] = log
    with open(out_path, "w") as f:
        json.dump(result, f)
    errs = [x for x in log if "error" in x or "exception" in x]
    print(f"{len(result['models'])} models, {len(log)} reads, {len(errs)} errors/exceptions, "
          f"{result['duration_s']} s -> {out_path}")


if __name__ == "__main__":
    main()
