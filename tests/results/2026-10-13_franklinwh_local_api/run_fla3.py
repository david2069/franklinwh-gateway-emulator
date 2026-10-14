"""Unpatched franklinwh-local-api async_get_status against the emulator (after #3)."""
import asyncio
from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.gateway import Gateway, GatewayConfig
from franklinwh_emulator.site import SiteConfig
from franklinwh_local_api import FranklinWHLocalClient
cfg = GatewayConfig(id="t", seed=7); cfg.site = SiteConfig(seed=7, tz_offset_hours=10, initial_soc=60)
gw = Gateway(cfg, clock=ManualClock(1768442400)); gw.start()
async def main():
    async with FranklinWHLocalClient(*gw.address, unit_id=1) as c:
        st = await c.async_get_status()
        print("async_get_status OK:", "soc", st.battery_soc_pct, "solar", st.solar_power_w, "load", st.home_load_w,
              "tou_dispatch", st.tou_dispatch_state, st.tou_dispatch_raw)
asyncio.run(main()); gw.stop()
