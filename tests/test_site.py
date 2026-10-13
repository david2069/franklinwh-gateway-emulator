"""Closed-loop physics of the site model."""

from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.site import MODE_BACKUP, MODE_TOU, Site, SiteConfig

DAY0 = 1_767_225_600   # 2026-01-01 00:00 UTC


def site_at(hour, **kw):
    clock = ManualClock(DAY0 + hour * 3600)
    kw.setdefault("tz_offset_hours", 0)
    return Site(SiteConfig(seed=7, **kw), clock), clock


def test_energy_balance_always_holds():
    site, clock = site_at(0, initial_soc=50)
    for _ in range(96):
        clock.advance(900)
        s = site.step()
        assert abs(s.load_w - (s.solar_w + s.grid_w + s.battery_w)) < 1e-6


def test_no_solar_at_night_and_counters_monotonic():
    site, clock = site_at(2, initial_soc=50)
    s0 = site.step()
    assert s0.solar_w == 0
    clock.advance(3600 * 30)
    s1 = site.step()
    for k in ("grid_import_wh", "grid_export_wh", "pv_wh", "batt_discharge_wh", "batt_charge_wh"):
        assert getattr(s1, k) >= getattr(s0, k)


def test_self_consumption_never_below_reserve():
    site, clock = site_at(18, initial_soc=22, self_reserve=20)
    site.solar_override_w, site.load_override_w = 0, 3000
    clock.advance(3600 * 3)
    s = site.step()
    assert abs(s.soc - 20) < 1e-6 and s.battery_w == 0 and s.grid_w == 3000


def test_backup_mode_does_not_discharge():
    site, clock = site_at(19, initial_soc=80, native_mode=MODE_BACKUP)
    clock.advance(600)
    assert site.step().battery_w <= 0


def test_tou_discharges_only_in_peak():
    site, clock = site_at(12, initial_soc=80, native_mode=MODE_TOU)
    site.solar_override_w, site.load_override_w = 0, 1000
    clock.advance(1)
    assert site.step().battery_w == 0
    site, clock = site_at(18, initial_soc=80, native_mode=MODE_TOU)
    site.solar_override_w, site.load_override_w = 0, 1000
    clock.advance(1)
    assert site.step().battery_w == 1000


def test_deterministic_for_seed():
    a, ca = site_at(10, initial_soc=50)
    b, cb = site_at(10, initial_soc=50)
    ca.advance(5000), cb.advance(5000)
    assert a.step() == b.step()


def test_rate_scales_with_apowers():
    site, clock = site_at(12, initial_soc=50, apower_units=2)
    site.solar_override_w, site.load_override_w = 0, 9000
    clock.advance(1)
    assert site.step().battery_w == 9000
