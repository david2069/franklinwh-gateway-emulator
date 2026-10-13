"""Read-only run of hAr1x/franklinwh-local-api against the emulator (no write methods called)."""
import asyncio, dataclasses, json, sys, traceback
from franklinwh_local_api import FranklinWHLocalClient

def ser(o):
    if dataclasses.is_dataclass(o): return {k: ser(v) for k, v in dataclasses.asdict(o).items()}
    if isinstance(o, dict): return {k: ser(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [ser(v) for v in o]
    if hasattr(o, "name") and hasattr(o, "value"): return f"{o.name}={o.value}"
    return o

async def main(port):
    out = {}
    async with FranklinWHLocalClient("127.0.0.1", port=port, unit_id=1) as c:
        for name in ("async_get_device_info", "async_get_status", "async_get_battery_limits", "async_get_battery_command_status"):
            try:
                out[name] = {"ok": True, "result": ser(await getattr(c, name)())}
            except Exception as e:
                out[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(out, indent=1, default=str))

asyncio.run(main(int(sys.argv[1])))
