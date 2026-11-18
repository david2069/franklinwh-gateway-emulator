"""#270: timed and seeded-random weather events from the fleet file."""

import pytest

from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.site import Site, SiteConfig
from franklinwh_emulator.weather_events import WeatherEvents

SYD = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney"}
MIDNIGHT = 1768395600.0          # 2026-01-15 00:00 AEDT
H = 3600.0


def events(specs, seed=7):
    return WeatherEvents(specs, seed=seed, start=MIDNIGHT + 8 * H,
                         local_midnight=lambda ts: MIDNIGHT + ((ts - MIDNIGHT) // 86400) * 86400)


def test_fixed_event_runs_at_its_time_for_its_times():
    ev = events([{"sky": "storm", "at": "13:00", "for": "2h", "every": "1d", "times": 2}])
    assert ev.active(MIDNIGHT + 12.9 * H) is None
    a = ev.active(MIDNIGHT + 13.5 * H)
    assert a["sky"] == "storm" and a["start"] == MIDNIGHT + 13 * H and a["end"] == MIDNIGHT + 15 * H
    assert ev.active(MIDNIGHT + 15 * H) is None                            # end is exclusive
    assert ev.active(MIDNIGHT + 86400 + 14 * H) is not None                # day 2
    assert ev.active(MIDNIGHT + 2 * 86400 + 14 * H) is None                # times: 2 → over
    assert 0 < ev.factor(MIDNIGHT + 13.5 * H) < 1 and ev.factor(MIDNIGHT + 12 * H) == 1.0


def test_random_event_is_seeded_and_follows_chance():
    spec = [{"sky": "overcast", "for": "30m..3h", "chance": 0.3, "per": "day"}]
    def hits(seed):
        ev = events(spec, seed)
        out = []
        for day in range(200):
            spans = {ev.active(MIDNIGHT + day * 86400 + m * 60) is not None for m in range(0, 1440, 10)}
            out.append(True in spans)
        return out
    a, b = hits(7), hits(7)
    assert a == b                                                        # repeatable
    assert a != hits(8)                                                  # another seed, another sky
    assert 0.2 < sum(a) / len(a) < 0.4                                   # about 30 % of days
    ev = events(spec)
    for day in range(60):
        for m in range(0, 1440, 5):
            x = ev.active(MIDNIGHT + day * 86400 + m * 60)
            if x:
                assert 7 * H <= x["start"] - (MIDNIGHT + day * 86400) <= 17 * H
                assert 30 * 60 - 1 <= x["end"] - x["start"] <= 3 * H + 1


def test_random_times_limits_the_hits():
    ev = events([{"sky": "storm", "for": "1h", "chance": 1.0, "per": "day", "times": 3}])
    days = [any(ev.active(MIDNIGHT + d * 86400 + m * 60) for m in range(0, 1440, 10)) for d in range(6)]
    assert days == [True, True, True, False, False, False]


@pytest.mark.parametrize("spec, msg", [
    ({"sky": "fog", "at": "13:00", "for": "1h"}, "sky must be one of"),
    ({"sky": "storm", "at": "13:00"}, "needs `for`"),
    ({"sky": "storm", "for": "1h"}, "either `at`"),
    ({"sky": "storm", "for": "1h", "at": "13:00", "chance": 0.5}, "either `at`"),
    ({"sky": "storm", "for": "3h..1h", "at": "13:00"}, "in order"),
    ({"sky": "storm", "for": "1h", "at": "25:00"}, "HH:MM"),
    ({"sky": "storm", "for": "1h", "chance": 1.5}, "at most 1"),
    ({"sky": "storm", "for": "1h", "chance": 0.5, "per": "week"}, "per: day"),
    ({"sky": "storm", "for": "1h", "at": "13:00", "times": 0}, "times"),
    ({"sky": "storm", "for": "1h", "at": "13:00", "colour": "red"}, "unknown keys"),
])
def test_bad_entries_say_what_is_wrong(spec, msg):
    with pytest.raises(ValueError, match=msg):
        events([spec])


def test_site_solar_dips_and_state_and_log_report_it():
    from franklinwh_emulator.control import gateway_state
    from franklinwh_emulator.gateway import Gateway, GatewayConfig
    cfg = GatewayConfig(setup="in-service", seed=7)
    cfg.site = SiteConfig(seed=7, location=SYD, weather={"model": "bell"},
                          weather_events=[{"sky": "storm", "at": "12:00", "for": "1h"}])
    clock = ManualClock(MIDNIGHT + 11.9 * H)
    gw = Gateway(cfg, clock=clock)
    plain = Site(SiteConfig(seed=7, location=SYD, weather={"model": "bell"}), ManualClock(MIDNIGHT))
    assert gw.site.solar_model_w(MIDNIGHT + 11.9 * H) == pytest.approx(plain.solar_model_w(MIDNIGHT + 11.9 * H), rel=0.02)   # no event yet; other sites differ only in panel temperature history
    assert gw.site.solar_model_w(MIDNIGHT + 12.5 * H) < 0.7 * plain.solar_model_w(MIDNIGHT + 12.5 * H)
    gw.site.step()                                        # the event log's baseline
    clock.advance(0.3 * H)
    gw.site.step()
    st = gateway_state(gw)
    assert st["weather_event"]["sky"] == "storm" and st["weather_event"]["end"].endswith("13:00+11:00")
    assert any("Weather event: storm sky until 13:00" in e["text"] for e in gw.events.recent(0))
    clock.advance(1 * H)
    gw.site.step()
    assert gateway_state(gw)["weather_event"] is None
    assert any("Weather event over" in e["text"] for e in gw.events.recent(0))


def test_group_members_share_the_events():
    from franklinwh_emulator.gateway import Fleet
    f = Fleet.from_dict({"groups": {"g": {"seed": 3, "location": SYD, "weather": {"model": "bell"},
                                          "weather_events": [{"sky": "overcast", "for": "1h..3h", "chance": 1.0, "per": "day"}]}},
                         "gateways": [{"id": "a", "group": "g", "phases": ["L1"]},
                                      {"id": "b", "group": "g", "phases": ["L2"]}]},
                        clock=ManualClock(MIDNIGHT + 6 * H))
    a, b = f["a"].site.weather_events, f["b"].site.weather_events
    spans = [(a.active(MIDNIGHT + m * 60) or {}).get("start") for m in range(0, 1440, 10)]
    assert any(spans) and spans == [(b.active(MIDNIGHT + m * 60) or {}).get("start") for m in range(0, 1440, 10)]
