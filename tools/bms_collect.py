"""Read-only BMS + power-flow collector, via the franklinwh-local-bridge HTTP API.

Polls ``GET /api/power`` (1301 power_flow) and ``GET /api/battery?id=N`` (1705 cells, 1703
power electronics, 1835 states, 1833 firmware, 1105 units, read in one Direct Connect session
by the bridge) and appends one JSON line per sample. It only issues GETs: it never writes
to the bridge or the aGate, and never touches Modbus.

    python tools/bms_collect.py http://<local-bridge>:8101 out.jsonl 2026-10-08T09:30:00 10 \\
        [2026-10-08T00:05:00] [--stop-after-dispatch] [--tz Australia/Sydney] [--id 1]

Positional: bridge URL, output file, end time (local), interval seconds, optional start time.
``--stop-after-dispatch`` ends 10 min after the battery falls back below 1 kW;
``--stop-after-full`` ends 15 min after SoC first reaches 99.5 %. Stops after 5
consecutive errors, with exponential back-off between them. Output holds serials: keep it out
of public repositories.
"""
import json
import sys
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


def opt(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main():
    flags = {"--tz", "--id"}
    pos = [a for i, a in enumerate(sys.argv[1:], 1)
           if not a.startswith("--") and sys.argv[i - 1] not in flags]
    host, out, until, every = pos[0].rstrip("/"), pos[1], pos[2], float(pos[3])
    start = pos[4] if len(pos) > 4 else None
    tz = ZoneInfo(opt("--tz", "Australia/Sydney"))
    dev = int(opt("--id", "1"))
    stop_after_dispatch = "--stop-after-dispatch" in sys.argv
    stop_after_full = "--stop-after-full" in sys.argv
    full_at = None
    end = datetime.fromisoformat(until).replace(tzinfo=tz).timestamp()
    if start:
        begin = datetime.fromisoformat(start).replace(tzinfo=tz).timestamp()
        print("waiting until", start, flush=True)
        while time.time() < begin:
            time.sleep(min(60, begin - time.time()))

    def get(path):
        with urllib.request.urlopen(host + path, timeout=15) as r:
            return json.load(r)

    seen = quiet = None
    fails = n = 0
    with open(out, "a") as f:
        while time.time() < end:
            t0 = time.time()
            rec = {"ts": t0}
            try:
                rec["power"] = get("/api/power")
                rec["battery"] = get(f"/api/battery?id={dev}")
                rec["dt_read"] = round(time.time() - t0, 3)
                if not rec["battery"].get("ok"):
                    raise RuntimeError(rec["battery"].get("error"))
                fails = 0
            except Exception as e:                       # noqa: BLE001 - log and back off
                fails += 1
                rec["error"] = repr(e)
            f.write(json.dumps(rec) + "\n")
            f.flush()
            n += 1
            if fails >= 5:
                print("stopping: 5 consecutive errors", rec["error"], flush=True)
                break
            if stop_after_dispatch and "power" in rec:
                if abs(rec["power"].get("p_fhp") or 0) > 1000:
                    seen, quiet = t0, None
                elif seen is not None:
                    quiet = quiet or t0
                    if t0 - quiet > 600:
                        print("dispatch ended; stopping", flush=True)
                        break
            if stop_after_full and "power" in rec:
                if full_at is None and (rec["power"].get("soc") or 0) >= 99.5:
                    full_at = t0
                    print("full at", datetime.now(tz).strftime("%H:%M:%S"), flush=True)
                if full_at is not None and t0 - full_at > 900:
                    print("full + 15 min; stopping", flush=True)
                    break
            if n % 60 == 0:
                p = rec.get("power", {})
                print(datetime.now(tz).strftime("%H:%M"), "samples", n, "p_fhp", p.get("p_fhp"),
                      "soc", p.get("soc"), flush=True)
            time.sleep(max(0.0, every - (time.time() - t0)) if fails == 0
                       else min(60.0, every * 2 ** fails))
    print("done", n, "samples", flush=True)


if __name__ == "__main__":
    main()
