"""Per-gateway location: IANA timezones with DST, weather inheritance, Direct Connect 1202."""

import pytest
import yaml

from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.scenario import Runner, Scenario, builtin_path
from franklinwh_emulator.site import Location, Site, SiteConfig

SYD = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney",
       "postcode": "2000"}
JAN_NOON_UTC = 1768478400        # 2026-01-15T12:00Z
JUL_NOON_UTC = 1783944000        # 2026-07-13T12:00Z


def site(ts, **kw):
    return Site(SiteConfig(seed=7, **kw), ManualClock(ts))


def test_sydney_dst_summer_and_winter():
    s = site(JAN_NOON_UTC, location=SYD)
    assert s.utc_offset_hours(JAN_NOON_UTC) == 11 and s.is_dst(JAN_NOON_UTC)
    assert s.local_datetime(JAN_NOON_UTC).hour == 23
    assert s.utc_offset_hours(JUL_NOON_UTC) == 10 and not s.is_dst(JUL_NOON_UTC)


def test_half_hour_zone_and_precedence():
    s = site(JAN_NOON_UTC, location={"timezone": "Australia/Adelaide"}, tz_offset_hours=3)
    assert s.utc_offset_hours(JAN_NOON_UTC) == 10.5          # location wins over the offset
    assert site(JAN_NOON_UTC, tz_offset_hours=-7).utc_offset_hours(JAN_NOON_UTC) == -7


def test_location_validation():
    with pytest.raises(ValueError):
        Location.from_dict({"timezone": "Mars/Olympus_Mons"})
    with pytest.raises(ValueError):
        Location.from_dict({"latitude": -33.87})               # needs both
    with pytest.raises(ValueError):
        Location.from_dict({"latitude": 95, "longitude": 0})


def test_weather_inherits_location():
    s = site(JAN_NOON_UTC, location=SYD, weather={"model": "synthetic"})
    assert s.weather.cfg.latitude == -33.87 and s.weather.cfg.azimuth == 180
    s2 = site(JAN_NOON_UTC, location=SYD, weather={"model": "synthetic", "latitude": 51.5,
                                                    "longitude": 0.0})
    assert s2.weather.cfg.latitude == 51.5                    # explicit weather wins


def test_dst_scenario_needs_a_real_timezone():
    d = yaml.safe_load(open(builtin_path("sydney-dst-end")))
    assert Runner(Scenario.from_dict(d)).run().passed
    g = d["fleet"]["gateways"][0]
    g.pop("location")
    g["tz_offset_hours"] = 11                                 # negative control
    assert not Runner(Scenario.from_dict(d)).run().passed


def test_direct_connect_1202_reports_location():
    pytest.importorskip("franklinwh_local")
    from franklinwh_local.client import LocalClient
    from franklinwh_emulator.gateway import Gateway, GatewayConfig
    gw = Gateway(GatewayConfig(seed=7, dc_port=0, site=SiteConfig(location=SYD)),
                 clock=ManualClock(JAN_NOON_UTC))
    with gw:
        c = LocalClient(*gw.dc_address, timeout=2, retries=0)
        c.transport.connect()
        c.login()
        tl = c.time_location()
        c.transport.close()
    # Reference aGate under AEDT: the *standard* offset (10) plus DST 1, an empty
    # timezoneStr, and coordinates as strings.
    assert tl["time"] == "2026-01-15 23:00:00" and tl["timezone"] == 10 and tl["DST"] == 1
    assert tl["laitude"] == "-33.87" and tl["postcode"] == "2000"
    assert tl["timezoneStr"] == ""


def test_control_state_has_local_time():
    from franklinwh_emulator.control import gateway_state
    from franklinwh_emulator.gateway import Gateway, GatewayConfig
    gw = Gateway(GatewayConfig(seed=7, site=SiteConfig(location=SYD)),
                 clock=ManualClock(JAN_NOON_UTC))
    st = gateway_state(gw)
    assert st["local_time"] == "2026-01-15T23:00:00+11:00" and st["dst"] is True
    assert st["location"]["timezone"] == "Australia/Sydney"


def test_cli_serve_location_flags():
    import argparse
    from franklinwh_emulator import cli
    captured = {}

    def fake_run(gateways, control_port):
        captured["site"] = gateways[0].site
        async def done():
            return None
        return done()
    orig = cli._run
    cli._run = fake_run
    try:
        cli.main(["serve", "--no-control", "--timezone", "Australia/Perth",
                  "--lat", "-31.95", "--lon", "115.86"])
    finally:
        cli._run = orig
    s = captured["site"]
    assert s.location.timezone == "Australia/Perth" and s.utc_offset_hours(JAN_NOON_UTC) == 8
