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


def test_open_meteo_offline_missing_day_falls_back_to_synthetic(tmp_path):
    """#268: a day that can't be had runs on the synthetic sky, and says why."""
    from franklinwh_emulator import weather as wmod
    w = weather(model="open-meteo", cache_dir=str(tmp_path), offline=True)
    seen = []
    w.on_fallback = seen.append
    syn = weather(model="synthetic")
    assert w.solar_w(NOON_AEST) == pytest.approx(syn.solar_w(NOON_AEST)) and w.solar_w(NOON_AEST) > 0
    assert w.ambient_c(NOON_AEST) is None
    assert "unavailable" in w.fallback and "synthetic" in w.fallback and len(seen) == 1   # said once
    with pytest.raises(FileNotFoundError):                 # the fetch itself still reports the miss
        wmod.fetch_open_meteo_day(w.cfg, "2026-01-16")


def test_open_meteo_fetch_is_retried_after_a_failure(tmp_path, monkeypatch):
    """#268: after a failed fetch the day is synthetic until RETRY_S has passed, then real."""
    from franklinwh_emulator import weather as wmod
    calls = []
    def fail(cfg, day):
        calls.append(day)
        raise OSError("network is unreachable")
    monkeypatch.setattr(wmod, "fetch_open_meteo_day", fail)
    w = weather(model="open-meteo", cache_dir=str(tmp_path), offline=False)
    w.solar_w(NOON_AEST); w.solar_w(NOON_AEST + 60)
    assert len(calls) == 1                                 # not again within RETRY_S
    now = wmod.time.time()
    monkeypatch.setattr(wmod.time, "time", lambda: now + wmod.RETRY_S + 1)
    w.solar_w(NOON_AEST)
    assert len(calls) == 2


def test_a_location_alone_means_open_meteo():
    """#268: no `model` with a location → open-meteo; without one → bell; explicit wins."""
    assert WeatherConfig.from_dict({**SYD}).model == "open-meteo"
    assert WeatherConfig.from_dict({}).model == "bell"
    assert WeatherConfig.from_dict({**SYD, "model": "bell"}).model == "bell"
    assert WeatherConfig.from_dict({**SYD}).offline                    # conftest: FRANKLINWH_EMULATOR_OFFLINE=1
    assert not WeatherConfig.from_dict({**SYD, "offline": False}).offline


def test_site_with_a_location_uses_open_meteo_and_reports_the_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("FRANKLINWH_EMULATOR_CACHE", str(tmp_path))       # empty cache, offline
    from franklinwh_emulator.control import gateway_state
    from franklinwh_emulator.gateway import Gateway, GatewayConfig
    cfg = GatewayConfig(setup="in-service", seed=7)
    cfg.site = SiteConfig(seed=7, location={**SYD, "timezone": "Australia/Sydney"})
    gw = Gateway(cfg, clock=ManualClock(NOON_AEST))
    st = gateway_state(gw)
    assert st["weather"]["model"] == "open-meteo" and "synthetic" in st["weather"]["fallback"]
    assert any(e["kind"] == "weather" for e in gw.events.recent(0))


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
    cfg = WeatherConfig.from_dict({**SYD, "kwp": 6.6, "model": "open-meteo", "cache_dir": str(tmp_path),
                                   "offline": False})
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
    with pytest.raises(ApiError, match=r"bell curve, which has no sky: send model: synthetic"):
        apply(gw, "weather", {"sky": "storm"})


def test_weather_action_sets_the_model_at_runtime():
    """#278: a gateway with no weather and no location gets a model and a place, and back."""
    from franklinwh_emulator.control import ApiError, apply, gateway_state
    from franklinwh_emulator.gateway import Gateway, GatewayConfig
    gw = Gateway(GatewayConfig(setup="in-service", seed=7), clock=ManualClock(NOON_AEST))
    assert gw.site.weather is None
    with pytest.raises(ApiError, match="needs a place"):
        apply(gw, "weather", {"model": "synthetic"})
    with pytest.raises(ApiError, match="together"):
        apply(gw, "weather", {"model": "synthetic", "latitude": -33.87})
    apply(gw, "weather", {"model": "synthetic", **SYD, "sky": "storm"})
    w = gateway_state(gw)["weather"]
    assert w["model"] == "synthetic" and w["sky"] == "storm"
    assert gw.site.weather.cfg.azimuth == 180.0                   # north-facing in the south
    assert gw.site.location.latitude is None                      # the site's location is untouched
    assert gw.site.solar_model_w(NOON_AEST) > 0
    apply(gw, "weather", {"sky": "clear"})                        # sky alone now works
    apply(gw, "weather", {"model": "open-meteo"})                 # keeps the place; offline → fallback
    assert gateway_state(gw)["weather"]["model"] == "open-meteo" and gw.site.weather.cfg.latitude == SYD["latitude"]
    apply(gw, "weather", {"model": "bell"})
    assert gateway_state(gw)["weather"] is None


def test_ctl_weather_verb():
    from franklinwh_emulator.cli import _ctl_request
    assert _ctl_request("weather", ["model=synthetic", "latitude=-33.87", "longitude=151.21", "sky=storm"]) == (
        "POST", "weather", {"model": "synthetic", "latitude": -33.87, "longitude": 151.21, "sky": "storm"})


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


def _om_answer(days):
    """A fake Open-Meteo reply covering whole UTC days: 15-min GTI 100 W/m², hourly cloud."""
    import calendar
    import time as _t
    t0 = calendar.timegm(_t.strptime(days[0], "%Y-%m-%d"))
    n = 96 * len(days)
    return {"latitude": -33.1, "longitude": 151.9,
            "minutely_15": {"time": [t0 + 900 * (i + 1) for i in range(n)],
                            "global_tilted_irradiance": [100.0] * n, "temperature_2m": [20.0] * n},
            "hourly": {"time": [t0 + 3600 * i for i in range(24 * len(days))],
                       "cloud_cover": [10] * (24 * len(days))}}


def _fake_api(monkeypatch, calls):
    import io
    import json
    from urllib.parse import parse_qs, urlsplit
    from franklinwh_emulator import weather as wmod

    def urlopen(req, timeout):
        q = parse_qs(urlsplit(req.full_url).query)
        calls.append((q["start_date"][0], q["end_date"][0]))
        start, end = q["start_date"][0], q["end_date"][0]
        days = [start] if start == end else [start, end]
        return io.BytesIO(json.dumps(_om_answer(days)).encode())
    monkeypatch.setattr(wmod.urllib.request, "urlopen", urlopen)


def test_today_brings_tomorrow_and_is_refreshed(tmp_path, monkeypatch):
    """#269: today's request also caches tomorrow; today is fetched again after refresh_h; a
    past day is fetched once."""
    import os
    import time as _t
    from franklinwh_emulator import weather as wmod
    calls = []
    _fake_api(monkeypatch, calls)
    cfg = WeatherConfig.from_dict({**SYD, "model": "open-meteo", "cache_dir": str(tmp_path),
                                   "offline": False, "refresh_h": 2})
    today = _t.strftime("%Y-%m-%d", _t.gmtime())
    tomorrow = wmod._next_day(today)
    d = wmod.fetch_open_meteo_day(cfg, today)
    assert calls == [(today, tomorrow)]
    assert len(d["minutely_15"]["time"]) == 96 and len(d["hourly"]["time"]) == 24   # split per day
    wmod.fetch_open_meteo_day(cfg, tomorrow)               # already cached by today's call
    wmod.fetch_open_meteo_day(cfg, today)                  # fresh: no new call
    assert len(calls) == 1
    path = wmod._cache_path(cfg, today)
    old = _t.time() - 3 * 3600
    os.utime(path, (old, old))                             # 3 h old > refresh_h
    wmod.fetch_open_meteo_day(cfg, today)
    assert len(calls) == 2
    past = "2026-01-10"
    wmod.fetch_open_meteo_day(cfg, past)
    p = wmod._cache_path(cfg, past)
    os.utime(p, (0, 0))                                    # very old, but a past day never refreshes
    wmod.fetch_open_meteo_day(cfg, past)
    assert calls[2:] == [(past, past)]


def test_failed_refresh_keeps_the_older_copy(tmp_path, monkeypatch):
    import os
    import time as _t
    from franklinwh_emulator import weather as wmod
    calls = []
    _fake_api(monkeypatch, calls)
    cfg = WeatherConfig.from_dict({**SYD, "model": "open-meteo", "cache_dir": str(tmp_path), "offline": False})
    today = _t.strftime("%Y-%m-%d", _t.gmtime())
    wmod.fetch_open_meteo_day(cfg, today)
    path = wmod._cache_path(cfg, today)
    os.utime(path, (0, 0))
    def down(req, timeout):
        raise OSError("network is unreachable")
    monkeypatch.setattr(wmod.urllib.request, "urlopen", down)
    assert len(wmod.fetch_open_meteo_day(cfg, today)["minutely_15"]["time"]) == 96


def test_refresh_h_must_be_positive():
    with pytest.raises(ValueError, match="refresh_h"):
        WeatherConfig.from_dict({**SYD, "refresh_h": 0})


def test_running_model_picks_up_a_refreshed_today(tmp_path, monkeypatch):
    """#269: a running site reads today again once its copy is refresh_h old."""
    import time as _t
    from franklinwh_emulator import weather as wmod
    calls = []
    _fake_api(monkeypatch, calls)
    w = weather(model="open-meteo", cache_dir=str(tmp_path), offline=False, refresh_h=1)
    now = _t.time()
    w.solar_w(now); w.solar_w(now + 60)
    assert len(calls) == 1
    import os
    for f in tmp_path.rglob("*.json"):
        os.utime(f, (0, 0))
    w._loaded_at = {k: 0 for k in w._loaded_at}
    w.solar_w(now)
    assert len(calls) == 2


def test_weather_action_reaches_every_aGate_in_a_group():
    """#283: a grouped site has one sky; the weather action changes every member."""
    from franklinwh_emulator.control import ApiError, apply, gateway_state
    from franklinwh_emulator.gateway import Fleet
    f = Fleet.from_dict({"groups": {"g": {"seed": 3, "location": {**SYD, "timezone": "Australia/Sydney"},
                                          "weather": {"model": "synthetic", "sky": "clear"}}},
                         "gateways": [{"id": "a", "group": "g", "phases": ["L1"]},
                                      {"id": "b", "group": "g", "phases": ["L2"]},
                                      {"id": "c", "group": "g", "phases": ["L3"]},
                                      {"id": "solo", "seed": 5, "weather": {"model": "synthetic", **SYD}}]},
                        clock=ManualClock(NOON_AEST))
    apply(f["b"], "weather", {"sky": "storm"})
    assert [gateway_state(f[g])["weather"]["sky"] for g in "abc"] == ["storm"] * 3
    assert gateway_state(f["solo"])["weather"]["sky"] == "broken"          # not in the group
    apply(f["a"], "weather", {"model": "bell"})
    assert [gateway_state(f[g])["weather"] for g in "abc"] == [None] * 3
    with pytest.raises(ApiError, match="sky must be one of"):
        apply(f["a"], "weather", {"model": "synthetic", "sky": "fog"})
    assert [gateway_state(f[g])["weather"] for g in "abc"] == [None] * 3   # refused: nothing changed


def test_a_slow_open_meteo_never_holds_up_the_simulation(tmp_path, monkeypatch):
    """#291: the fetch runs in the background; the site runs on the synthetic sky meanwhile and
    picks the real day up once it lands."""
    import threading
    import time as _t
    from franklinwh_emulator import weather as wmod
    monkeypatch.setattr(wmod, "_start", lambda fn: threading.Thread(target=fn, daemon=True).start(), raising=False)
    release = threading.Event()
    calls = []

    def slow(cfg, day):
        calls.append(day)
        release.wait(5)
        return _om_answer([day])
    monkeypatch.setattr(wmod, "fetch_open_meteo_day", slow)
    w = weather(model="open-meteo", cache_dir=str(tmp_path), offline=False)
    syn = weather(model="synthetic")
    t0 = _t.monotonic()
    assert w.solar_w(NOON_AEST) == pytest.approx(syn.solar_w(NOON_AEST))   # synthetic for now
    w.solar_w(NOON_AEST + 60)
    assert _t.monotonic() - t0 < 1 and len(calls) == 1                      # no wait, one fetch
    release.set()
    for _ in range(100):
        if w._results:
            break
        _t.sleep(0.02)
    w.solar_w(NOON_AEST)
    day = _t.strftime("%Y-%m-%d", _t.gmtime(NOON_AEST))
    assert day in w._days and w.fallback is None


def test_a_network_failure_pauses_every_site_with_one_warning(tmp_path, monkeypatch, caplog):
    """#291: one site's failed fetch pauses fetching for all of them for RETRY_S; only the site
    that tried logs it, and every site still reports the fallback."""
    from franklinwh_emulator import weather as wmod
    calls = []

    def down(cfg, day):
        calls.append(day)
        raise OSError("handshake timed out")
    monkeypatch.setattr(wmod, "fetch_open_meteo_day", down)
    sites = [weather(model="open-meteo", cache_dir=str(tmp_path), offline=False, latitude=-33.87 + i)
             for i in range(3)]
    with caplog.at_level("WARNING", logger="franklinwh_emulator.weather"):
        for w in sites:
            w.solar_w(NOON_AEST); w.solar_w(NOON_AEST - 86400)
    assert len(calls) == 1
    assert len([r for r in caplog.records if "unavailable" in r.getMessage()]) == 1
    assert all(w.fallback and "unavailable" in w.fallback for w in sites)


def test_sites_fetching_together_log_one_network_failure(tmp_path, monkeypatch, caplog):
    """#291: at start-up every site starts its fetch before any fails; the failures that land
    while the pause is on are not logged again."""
    from franklinwh_emulator import weather as wmod
    pending = []
    monkeypatch.setattr(wmod, "_start", pending.append, raising=False)   # hold the fetches
    monkeypatch.setattr(wmod, "fetch_open_meteo_day",
                        lambda cfg, day: (_ for _ in ()).throw(OSError("timed out")))
    sites = [weather(model="open-meteo", cache_dir=str(tmp_path), offline=False, latitude=-33.87 + i)
             for i in range(3)]
    for w in sites:
        w.solar_w(NOON_AEST)
    assert len(pending) == 3                               # all three in flight together
    for run in pending:
        run()
    with caplog.at_level("WARNING", logger="franklinwh_emulator.weather"):
        for w in sites:
            w.solar_w(NOON_AEST)
    assert len([r for r in caplog.records if "unavailable" in r.getMessage()]) == 1
    assert all(w.fallback and "unavailable" in w.fallback for w in sites)
