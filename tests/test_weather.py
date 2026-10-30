"""Weather models: sun geometry, synthetic skies, Open-Meteo replay (bundled, offline)."""

import pytest

from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.site import Site, SiteConfig
from franklinwh_emulator.weather import (Weather, WeatherConfig, clear_sky_poa, sun_position)

SYD = {"latitude": -33.87, "longitude": 151.21}
NOON_AEST = 1768442400          # 2026-01-15 12:00 AEST (02:00Z)
MIDNIGHT_AEST = NOON_AEST - 12 * 3600


def weather(**kw):
    cfg = WeatherConfig.from_dict({**SYD, "kwp": 6.6, **kw})
    return Weather(cfg, seed=7, kwp=6.6)


def test_sun_position_sydney_summer():
    z, az = sun_position(NOON_AEST, -33.87, 151.21)
    assert 10 < z < 16 and abs(abs(az) - 180) < 10          # high, to the north
    z_night, _ = sun_position(MIDNIGHT_AEST, -33.87, 151.21)
    assert z_night > 90


def test_default_azimuth_faces_the_equator():
    assert WeatherConfig.from_dict({"model": "synthetic", **SYD}).azimuth == 180
    assert WeatherConfig.from_dict({"model": "synthetic", "latitude": 40, "longitude": -100}).azimuth == 0


def test_north_facing_beats_south_facing_in_sydney():
    north = clear_sky_poa(NOON_AEST, -33.87, 151.21, 30, 180)
    south = clear_sky_poa(NOON_AEST, -33.87, 151.21, 30, 0)
    assert north > south


def test_synthetic_clear_vs_broken():
    clear, broken = weather(model="synthetic", sky="clear"), weather(model="synthetic", sky="broken")
    day = range(MIDNIGHT_AEST, MIDNIGHT_AEST + 86400, 60)
    e_clear = sum(clear.solar_w(t) for t in day)
    e_broken = sum(broken.solar_w(t) for t in day)
    assert e_broken < e_clear
    noon = [broken.solar_w(NOON_AEST + k * 10) for k in range(60)]
    assert max(noon) - min(noon) > 2000                      # real cloud edges
    assert max(clear.solar_w(t) for t in day) <= 6600 * 0.85 * 1.1


def test_synthetic_is_deterministic():
    a, b = weather(model="synthetic"), weather(model="synthetic")
    assert [a.solar_w(NOON_AEST + k) for k in range(100)] == \
           [b.solar_w(NOON_AEST + k) for k in range(100)]


def test_open_meteo_bundled_day():
    w = weather(model="open-meteo", cache_dir="builtin", offline=True)
    day = range(MIDNIGHT_AEST, MIDNIGHT_AEST + 86400, 30)
    vals = [w.solar_w(t) for t in day]
    kwh = sum(vals) * 30 / 3600 / 1000
    assert 25 < kwh < 40                                    # raw Open-Meteo: 31.9 kWh
    assert max(vals) <= 6600 * 0.85 * 1.15                  # never above clear sky (+margin)
    assert w.ambient_c(NOON_AEST) == pytest.approx(24.0, abs=3)


def test_open_meteo_offline_missing_day_raises(tmp_path):
    w = weather(model="open-meteo", cache_dir=str(tmp_path), offline=True)
    with pytest.raises(FileNotFoundError):
        w.solar_w(NOON_AEST)


def test_open_meteo_cache_stores_requested_point(tmp_path, monkeypatch):
    # The API answers with its snapped grid cell; the cache must keep only the point asked for.
    import io
    import json

    from franklinwh_emulator import weather as wmod
    answer = {"latitude": -33.1, "longitude": 151.9, "elevation": 42.0,
              "minutely_15": {"time": [], "global_tilted_irradiance": [], "temperature_2m": []},
              "hourly": {"time": [], "cloud_cover": []}}
    monkeypatch.setattr(wmod.urllib.request, "urlopen",
                        lambda req, timeout: io.BytesIO(json.dumps(answer).encode()))
    cfg = WeatherConfig.from_dict({**SYD, "kwp": 6.6, "model": "open-meteo", "cache_dir": str(tmp_path)})
    wmod.fetch_open_meteo_day(cfg, "2026-01-16")
    (cached,) = tmp_path.rglob("*.json")
    stored = json.loads(cached.read_text())
    assert (stored["latitude"], stored["longitude"]) == (SYD["latitude"], SYD["longitude"])
    assert "elevation" not in stored


def test_site_uses_weather_and_temperature():
    clock = ManualClock(NOON_AEST)
    s = Site(SiteConfig(seed=7, tz_offset_hours=10, initial_soc=50,
                        weather={"model": "open-meteo", **SYD, "kwp": 6.6,
                                 "cache_dir": "builtin", "offline": True}), clock)
    snap = s.step()
    assert snap.solar_w > 0 and snap.ambient_c == pytest.approx(24.0, abs=3)


def test_invalid_weather_config():
    with pytest.raises(ValueError):
        WeatherConfig.from_dict({"model": "synthetic"})       # no location
    with pytest.raises(ValueError):
        WeatherConfig.from_dict({"model": "rain"})
    with pytest.raises(ValueError):
        WeatherConfig.from_dict({"model": "synthetic", **SYD, "sky": "hail"})


def test_weather_control_action():
    from franklinwh_emulator.control import ApiError, apply
    from franklinwh_emulator.gateway import Gateway, GatewayConfig
    gw = Gateway(GatewayConfig(setup="in-service", seed=7, site=SiteConfig(
        weather={"model": "synthetic", **SYD})), clock=ManualClock(NOON_AEST))
    apply(gw, "weather", {"sky": "storm"})
    assert gw.site.weather.cfg.sky == "storm"
    with pytest.raises(ApiError):
        apply(gw, "weather", {"sky": "hail"})


def test_weather_action_without_a_model_says_how_to_add_one():
    from franklinwh_emulator.control import ApiError, apply
    from franklinwh_emulator.gateway import Gateway, GatewayConfig
    gw = Gateway(GatewayConfig(setup="in-service", seed=7))
    with pytest.raises(ApiError, match=r"this gateway has no weather model.*weather: \{model: synthetic\}"):
        apply(gw, "weather", {"sky": "storm"})


def test_forced_solar_is_capped_by_the_sun():
    """#56: no sunshine, no solar; forced solar never exceeds the model's value."""
    syd = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney"}
    noon, night = 1768446000.0, 1768474800.0           # 2026-01-15 14:00 and 22:00 Sydney
    for cap in (True, False):
        for ts in (noon, night):
            s = Site(SiteConfig(seed=7, location=syd, weather={"model": "synthetic", "kwp": 6.6},
                                cap_forced_solar=cap), ManualClock(ts))
            model = s.solar_model_w(ts)
            s.solar_override_w = 16000
            got = s.solar_w(ts)
            if cap:
                assert got == pytest.approx(model) and got < 16000
            else:
                assert got == 16000
    s = Site(SiteConfig(seed=7, location=syd, weather={"model": "synthetic", "kwp": 6.6}), ManualClock(night))
    s.solar_override_w = 16000
    assert s.solar_w(night) == 0                       # night is enforced
    s = Site(SiteConfig(seed=7, location=syd, weather={"model": "synthetic", "kwp": 6.6}), ManualClock(noon))
    s.solar_override_w = 1000
    assert s.solar_w(noon) == 1000                     # below the sun: as forced
