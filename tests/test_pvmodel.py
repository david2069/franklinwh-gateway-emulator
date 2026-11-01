"""pvlib as an optional PV model (#112)."""

import pytest

from conftest import make_gateway
from franklinwh_emulator.pvmodel import from_config
from franklinwh_emulator.weather import clear_sky_poa

pvlib = pytest.importorskip("pvlib")
SYD = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney"}
NOON = 1_767_225_600 + 2 * 3600      # 2026-01-01 13:00 Sydney (summer)


def test_pvlib_transposition_and_cell_temperature():
    m = from_config({"model": "pvlib", "transposition": "perez", "temperature_model": "faiman"})
    poa = m.clear_sky_poa(NOON, -33.87, 151.21, 20.0, 180.0)
    built_in = clear_sky_poa(NOON, -33.87, 151.21, 20.0, 180.0)
    assert 800 < poa < 1200 and poa != pytest.approx(built_in, abs=1)   # its own transposition
    assert m.clear_sky_poa(NOON + 12 * 3600, -33.87, 151.21, 20.0, 180.0) == 0  # night
    assert 40 < m.cell_c(1000, 25) < 70
    assert from_config(None) is None and from_config({"model": "builtin"}) is None
    with pytest.raises(ValueError, match="pv.model"):
        from_config({"model": "sam"})
    with pytest.raises(ValueError, match="transposition"):
        from_config({"model": "pvlib", "transposition": "magic"})


def test_site_uses_pvlib_for_solar(clock):
    clock.t = NOON
    w = {"model": "synthetic", "kwp": 6.6, "sky": "clear", **{k: SYD[k] for k in ("latitude", "longitude")}}
    base = dict(location=SYD, weather=w, cap_forced_solar=False, pv_temperature={})
    a = make_gateway(clock, site=base)
    b = make_gateway(clock, site={**base, "pv": {"model": "pvlib"}})
    assert b.site.weather.clear_sky.__self__ is b.site.pvlib
    sa, sb = a.site.solar_model_w(NOON), b.site.solar_model_w(NOON)
    assert sa > 3000 and sb > 3000 and sa != pytest.approx(sb, abs=1)
