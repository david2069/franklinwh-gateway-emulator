"""One emulated aGate: start it, read live values over Modbus TCP, stop it.

    python examples/single_gateway.py

No hardware and no extra packages: the emulator plus the standard library. Point your own
client at the printed host and port instead of a real aGate (one client at a time, as on the
real device).
"""
from _modbus import read_holding, s16, s32

from franklinwh_emulator import Gateway, GatewayConfig


def main() -> dict:
    gw = Gateway(GatewayConfig(id="site", seed=7, setup="in-service"))
    host, port = gw.start()                    # background thread, ephemeral port
    try:
        ext = read_holding(host, port, 15000, 44)
        values = {
            "address": f"{host}:{port}",
            "soc_pct": ext[35] / 10,                              # 15035 = SoC x 10
            "battery_w": s32(ext[12], ext[13]),                   # 15012:15013, + discharge
            "grid_w": s16(ext[22]),                               # 15022, + import
            "solar_w": s16(ext[40]),                              # 15040, solar-side term
            "home_load_w": read_holding(host, port, 16000, 1)[0],  # 16000, 1 W
        }
    finally:
        gw.stop()
    for k, v in values.items():
        print(f"{k:12} {v}")
    return values


if __name__ == "__main__":
    main()
