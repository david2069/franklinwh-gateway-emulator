"""TOU schedule (#58): blocks with dispatch codes drive the battery in TOU mode."""

import pytest

from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.control import ApiError, apply, gateway_state
from franklinwh_emulator.gateway import Gateway, GatewayConfig
from franklinwh_emulator.site import MODE_SELF, MODE_TOU, Site, SiteConfig
from franklinwh_emulator.validate import validate_fleet

DAY = 1_767_225_600                      # 2026-01-01 00:00 UTC; the sites run at UTC


def site(hour, dispatch=None, soc=50, **kw):
    sched = kw.pop("tou_schedule", None) if dispatch is None else [
        {"name": "test", "startHourTime": "00:00", "endHourTime": "24:00", "waveType": 0,
         "dispatchId": dispatch}]
    clock = ManualClock(DAY + hour * 3600)
    s = Site(SiteConfig(seed=7, tz_offset_hours=0, native_mode=MODE_TOU, initial_soc=soc,
                        cap_forced_solar=False, tou_schedule=sched, **kw), clock)
    return s, clock


def battery(s, clock, solar, load, seconds=120):
    s.solar_override_w, s.load_override_w = solar, load
    clock.advance(seconds)
    return s.step().battery_w           # + = discharging


@pytest.mark.parametrize("dispatch,solar,load,expect", [
    (1, 0, 2000, 2000),       # HOME: battery covers the home
    (1, 3000, 1000, 0),       # HOME: surplus solar goes to the grid, not the battery
    (2, 0, 2000, 0),          # STANDBY
    (2, 3000, 1000, 0),
    (3, 3000, 1000, -2000),   # SOLAR: charge from surplus
    (3, 0, 2000, 0),          # SOLAR: never discharge
    (6, 0, 2000, 2000),       # SELF
    (6, 3000, 1000, -2000),
    (7, 0, 500, 5000),        # GRID_EXPORT: at the rating
    (8, 0, 500, -5000),       # GRID_CHARGE: at the rating
])
def test_each_dispatch_code(dispatch, solar, load, expect):
    s, clock = site(12, dispatch)
    assert battery(s, clock, solar, load) == pytest.approx(expect, abs=60)


def test_default_schedule_is_the_old_behaviour():
    """Solar-charge off-peak, self-consumption 17:00-21:00, as before #58."""
    s, clock = site(12)
    assert battery(s, clock, 0, 2000) == pytest.approx(0, abs=60)       # noon: no discharge
    s, clock = site(18)
    assert battery(s, clock, 0, 2000) == pytest.approx(2000, abs=60)    # peak: discharge


def test_only_in_tou_mode():
    s, clock = site(12, 7)
    s.set_native_mode(MODE_SELF)
    assert battery(s, clock, 0, 1000) == pytest.approx(1000, abs=60)    # self-consumption


def test_reserve_still_applies_to_grid_export():
    s, clock = site(12, 7, soc=21)
    s.set_reserves(self_reserve=20, tou_reserve=20)
    for _ in range(30):
        battery(s, clock, 0, 500, seconds=60)
    assert s.soc >= 19.5 and battery(s, clock, 0, 500) <= 60


def test_limit_fields_are_kept_not_applied():
    """The guide: minDischargeSoc is ignored by the aGate; the others aren't verified."""
    sched = [{"name": "x", "startHourTime": "00:00", "endHourTime": "24:00", "waveType": 0,
              "dispatchId": 7, "minDischargeSoc": 90, "gridFeedMax": 100}]
    s, clock = site(12, soc=80, tou_schedule=sched)
    assert battery(s, clock, 0, 500) == pytest.approx(5000, abs=60)
    assert s.tou_now(clock.now())["active"]["minDischargeSoc"] == 90


def test_gaps_are_self_off_peak_and_state_shows_the_active_block():
    sched = [{"name": "Peak", "startHourTime": "17:00", "endHourTime": "21:00", "waveType": 2,
              "dispatchId": 7}]
    s, clock = site(18, tou_schedule=sched, tou_rates={2: {"buy": 0.54, "sell": 0.03}})
    now = s.tou_now(clock.now())
    assert [(b["startHourTime"], b["endHourTime"], b["dispatch"]) for b in now["schedule"]] == [
        ("00:00", "17:00", "SELF"), ("17:00", "21:00", "GRID_EXPORT"), ("21:00", "24:00", "SELF")]
    a = now["active"]
    assert (a["name"], a["wave"], a["remaining_min"], a["rates"]) == (
        "Peak", "On-Peak", 180.0, {"buy": 0.54, "sell": 0.03})


@pytest.mark.parametrize("blocks,msg", [
    ([{"name": "a", "startHourTime": "00:00", "endHourTime": "12:00", "waveType": 0, "dispatchId": 5}],
     "dispatchId must be one of"),
    ([{"name": "a", "startHourTime": "00:00", "endHourTime": "12:00", "waveType": 3, "dispatchId": 6}],
     "waveType must be one of"),
    ([{"name": "a", "startHourTime": "12:00", "endHourTime": "10:00", "waveType": 0, "dispatchId": 6}],
     "ends .* before it starts"),
    ([{"name": "a", "startHourTime": "9", "endHourTime": "10:00", "waveType": 0, "dispatchId": 6}],
     "must be HH:MM"),
    ([{"name": "a", "startHourTime": "00:00", "endHourTime": "12:00", "waveType": 0, "dispatchId": 6},
      {"name": "b", "startHourTime": "11:00", "endHourTime": "13:00", "waveType": 0, "dispatchId": 6}],
     "overlaps"),
    ([{"name": "a", "startHourTime": "00:00", "endHourTime": "12:00", "waveType": 0}], "missing"),
])
def test_bad_schedules_are_refused(blocks, msg):
    with pytest.raises(ValueError, match=msg):
        site(12, tou_schedule=blocks)
    problems = validate_fleet({"gateways": [{"id": "g", "tou_schedule": blocks}]})
    assert problems and "tou" in problems[0]["error"]


def test_tou_action_sets_and_resets_without_changing_mode():
    gw = Gateway(GatewayConfig(seed=7, setup="in-service"), clock=ManualClock(DAY + 12 * 3600))
    gw.site.set_native_mode(MODE_SELF)
    apply(gw, "tou", {"schedule": [{"name": "Export", "startHourTime": "00:00", "endHourTime": "24:00",
                                    "waveType": 2, "dispatchId": 7}], "rates": {"2": {"buy": 0.5}}})
    s = gateway_state(gw)
    assert s["native_mode"] == MODE_SELF                              # the mode is untouched
    assert s["tou"]["active"]["dispatch"] == "GRID_EXPORT" and s["tou"]["rates"]["2"]["buy"] == 0.5
    with pytest.raises(ApiError, match="dispatchId"):
        apply(gw, "tou", {"schedule": [{"name": "x", "startHourTime": "00:00", "endHourTime": "24:00",
                                        "waveType": 0, "dispatchId": 4}]})
    apply(gw, "tou", {"reset": True})
    assert [b["dispatch"] for b in gateway_state(gw)["tou"]["schedule"]] == ["SOLAR", "SELF", "SOLAR"]
