"""franklinwh-local-api status decode vs emulator truth, clock frozen.
Client-side patch only: the 15500x17 read becomes 15500x14 + [0, 0, 0] PADDING (15514-15516 are not
in the emulator's map), so tou_dispatch_* below is padding, not data."""
import asyncio, json
from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.gateway import Gateway, GatewayConfig
from franklinwh_emulator.site import SiteConfig
import franklinwh_local_api.client as flc
from franklinwh_local_api import FranklinWHLocalClient

NOON_AEST = 1768442400  # 2026-01-15 12:00 AEST
cfg = GatewayConfig(id="t", seed=7)
cfg.site = SiteConfig(seed=7, tz_offset_hours=10, initial_soc=60, self_reserve=20, tou_reserve=35)
gw = Gateway(cfg, clock=ManualClock(NOON_AEST))
gw.start()
host, port = gw.address

orig = flc.FranklinWHLocalClient._read_registers
async def patched(self, address, count):
    if address == 15500 and count == 17:
        return list(await orig(self, 15500, 14)) + [0, 0, 0]
    return await orig(self, address, count)
flc.FranklinWHLocalClient._read_registers = patched

async def main():
    async with FranklinWHLocalClient(host, port=port, unit_id=1) as c:
        return await c.async_get_status()

st = asyncio.run(main())
s = gw.site
snap = s._last
truth = {
  "battery_soc_pct": snap.soc, "battery_power_w (+discharge)": snap.battery_w, "grid_power_w (+import)": snap.grid_w,
  "solar_power_w": snap.solar_w, "home_load_w": snap.load_w, "grid_voltage_ln_v": snap.v_ln, "grid_frequency_hz": snap.hz,
  "grid_connected": snap.grid_connected, "operating_mode (native)": s.native_mode, "self_reserve_pct": s.self_reserve,
  "tou_reserve_pct": s.tou_reserve, "ambient_temp_c": snap.ambient_c, "cabinet_temp_c": snap.cabinet_c,
}
lib = {
  "battery_soc_pct": st.battery_soc_pct, "battery_power_w (+discharge)": st.battery_power_w, "grid_power_w (+import)": st.grid_power_w,
  "solar_power_w": st.solar_power_w, "home_load_w": st.home_load_w, "grid_voltage_ln_v": st.grid_voltage_ln_v,
  "grid_frequency_hz": st.grid_frequency_hz, "grid_connected": st.grid_connected,
  "operating_mode (native)": f"{st.operating_mode.name}={st.operating_mode.value}", "self_reserve_pct": st.self_reserve_pct,
  "tou_reserve_pct": st.tou_reserve_pct, "ambient_temp_c": st.ambient_temp_c, "cabinet_temp_c": st.cabinet_temp_c,
}
print(json.dumps({"emulator_truth": truth, "library": lib,
                  "library_extra": {"battery_capacity_wh": st.battery_capacity_wh, "battery_soh_pct": st.battery_soh_pct,
                                    "grid_voltage_ll_v": st.grid_voltage_ll_v, "alarms": st.alarms,
                                    "battery_command_active": st.battery_command_active,
                                    "tou_dispatch (PADDING)": [st.tou_dispatch_state, st.tou_dispatch_raw]},
                  "reg_15508_15509": gw.device.read(15508, 2)}, indent=1, default=str))
gw.stop()
