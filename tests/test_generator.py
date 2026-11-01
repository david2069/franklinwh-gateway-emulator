"""Generator and AGS: config from 1901, start/stop state machine, energy, faults, DC payloads."""

import pytest

from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.dc_payloads import DcPayloads
from franklinwh_emulator.gateway import Gateway, GatewayConfig
from franklinwh_emulator.generator import BLOCK_DEFAULTS, Generator
from franklinwh_emulator.site import Site, SiteConfig

NIGHT = 1_767_225_600 + 14 * 3600        # 2026-01-02 00:00 at UTC+10 (no PV)


def site(gen=None, **kw):
    clock = ManualClock(NIGHT)
    kw.setdefault("tz_offset_hours", 10)
    kw.setdefault("initial_soc", 30)
    gen = {"installed": True, "rated_w": 6000, "startDelTime": 60, "charge1En": 0,
           "crank_s": 5, "warmup_s": 10, "cooldown_s": 30, "min_run_s": 120, **(gen or {})}
    s = Site(SiteConfig(seed=7, generator=gen, **kw), clock)
    s.solar_override_w, s.load_override_w = 0, 1500
    return s, clock


def after(s, clock, seconds):
    clock.advance(seconds)
    return s.step()


def test_reference_site_has_feature_off():
    g = Generator(None)
    assert not g.usable and g.block == BLOCK_DEFAULTS
    s = Site(SiteConfig(seed=7), ManualClock(NIGHT))
    snap = s.step()
    assert snap.generator_w == 0 and snap.gen_state == 0


def test_never_runs_on_grid_in_auto():
    s, clock = site(initial_soc=5)
    snap = after(s, clock, 3600)
    assert snap.gen_state == 0 and snap.generator_w == 0


def test_ags_starts_below_threshold_and_charges_to_optimal_point():
    s, clock = site(initial_soc=15)                   # below genStartElec 20
    s.set_grid(available=False)
    snap = after(s, clock, 3)                         # no wait: startDelTime is an alarm delay (#179)
    assert not snap.grid_connected and snap.gen_state == 1 and not snap.gen_relay_closed
    snap = after(s, clock, 20)                        # cranked 5 s + warmed up 10 s
    assert snap.gen_relay_closed
    # 70 % of 6 kW = 4.2 kW: 1.5 kW of house + 2.7 kW into the battery.
    assert snap.generator_w == pytest.approx(4200, abs=1)
    assert snap.battery_w == pytest.approx(-2700, abs=1)
    assert abs(snap.load_w - (snap.solar_w + snap.grid_w + snap.battery_w + snap.generator_w)) < 1e-6


def test_ags_stops_at_close_threshold_then_cools_down():
    s, clock = site(initial_soc=19, gen={"genCloseElec": 22})
    s.set_grid(available=False)
    after(s, clock, 120)
    assert s.step().gen_relay_closed
    peak = 0.0
    for _ in range(40):                                # charge past 22 %
        snap = after(s, clock, 20)
        peak = max(peak, snap.soc)
        if snap.gen_state != 1:
            break
    assert snap.gen_state == 2 and snap.generator_w == 0     # stopped, cooling down
    assert 22.0 <= peak < 22.2                                # at genCloseElec
    assert after(s, clock, 40).gen_state == 0


def test_heavy_load_uses_rated_power_and_battery_covers_the_rest():
    s, clock = site(initial_soc=10)
    s.load_override_w = 8000
    s.set_grid(available=False)
    snap = after(s, clock, 200)
    assert snap.generator_w == pytest.approx(6000, abs=1)
    assert snap.battery_w == pytest.approx(2000, abs=1)


def test_grid_return_hands_back_and_cools_down():
    s, clock = site(initial_soc=10, grid_overrides={"es_delay_s": 5})
    s.set_grid(available=False)
    assert after(s, clock, 200).gen_relay_closed
    s.restore_grid()
    snap = after(s, clock, 10)
    assert snap.grid_connected and not snap.gen_relay_closed and snap.generator_w == 0
    assert snap.gen_state == 2


def test_manual_mode_runs_unloaded_on_grid_and_loaded_off_grid():
    s, clock = site(initial_soc=80, gen={"manuSw": 2})
    s.generator.start_manual()
    snap = after(s, clock, 60)
    assert snap.gen_state == 1 and not snap.gen_relay_closed and snap.generator_w == 0
    s.set_grid(available=False)
    assert after(s, clock, 30).generator_w > 0
    s.generator.stop_manual()
    assert after(s, clock, 5).gen_state == 2


def test_exercise_run_on_schedule():
    s, clock = site(initial_soc=80, gen={"oilmanoEn": 1, "manoFre": 1, "manoStartTime": "00:30",
                                          "manoTime": 5})
    assert after(s, clock, 25 * 60).gen_state == 0
    snap = after(s, clock, 6 * 60)                     # 00:31, unloaded on grid
    assert snap.gen_state == 1 and snap.generator_w == 0
    assert after(s, clock, 3 * 60).gen_state == 1     # 00:34
    assert after(s, clock, 75).gen_state == 2         # 00:35:15: 5 min done, cooling down
    assert after(s, clock, 60).gen_state == 0


def test_faults_fail_to_start_trip_and_fuel():
    s, clock = site(initial_soc=10)
    s.generator.forced_fault = "fail-start"
    s.set_grid(available=False)
    snap = after(s, clock, 200)
    assert snap.gen_state == 3 and s.generator.fault == "fail-to-start"
    s.generator.clear_fault()
    assert after(s, clock, 60).gen_relay_closed
    s.generator.forced_fault = "trip"
    snap = after(s, clock, 1)
    assert snap.gen_state == 3 and snap.generator_w == 0 and s.generator.fault == "tripped"
    s2, c2 = site(initial_soc=10, gen={"fuel_l": 0.5})
    s2.set_grid(available=False)
    for _ in range(30):
        snap = after(s2, c2, 60)
    assert s2.generator.fault == "out-of-fuel" and snap.gen_state == 3 and s2.generator.fuel_l == 0


def test_operating_windows_gate_auto_start():
    s, clock = site(initial_soc=10, gen={"charge1En": 1, "charge1StartTime": "11:00",
                                          "charge1EndTime": "23:59"})
    s.set_grid(available=False)
    assert after(s, clock, 300).gen_state == 0         # 00:05: outside 11:00–23:59


def test_kwh_gen_and_direct_connect_payloads():
    gw = Gateway(GatewayConfig(setup="in-service", seed=7, site=SiteConfig(
        seed=7, tz_offset_hours=10, initial_soc=10,
        generator={"installed": True, "rated_w": 6000, "startDelTime": 0, "charge1En": 0,
                   "crank_s": 5, "warmup_s": 5})), clock=ManualClock(NIGHT))
    s, clock = gw.site, gw.site.clock
    s.solar_override_w, s.load_override_w = 0, 1000
    p = DcPayloads(gw)
    assert p.handlers[1709]({"opt": 0})["genRelayOpen"] == 1
    s.set_grid(available=False)
    clock.advance(3600)
    pf = p.handlers[1301]({"opt": 0})
    assert pf["p_gen"] == pytest.approx(4200, abs=1) and pf["genStat"] == 1 and pf["main_sw"][1] == 1
    assert pf["kwh_gen"] == pytest.approx(4.2 * (3600 - 10) / 3600, rel=0.02)
    g = p.handlers[1901]({"opt": 0})
    assert g["genStat"] == 1 and g["genpowerGen"] == 4200 and g["genRatedPower"] == 6000
    assert set(BLOCK_DEFAULTS) | {"opt", "result", "reason", "genStat", "power", "curr", "volt",
                                  "freq", "genpowerGen"} == set(g)
    assert p.handlers[1709]({"opt": 0})["genRelayOpen"] == 0
    assert p.handlers[1901]({"opt": 1, "genCloseElec": 90})["reason"] == -2   # partial refused
    full = {k: v for k, v in g.items() if k not in ("opt", "result", "reason")}
    full.update(opt=1, genCloseElec=90)
    assert p.handlers[1901](full)["result"] == 0 and s.generator.block["genCloseElec"] == 90


def test_reference_1902_key_set():
    """Keys of the real 1902 (reference aGate, 2026-10-07)."""
    real = {"opt", "result", "reason", "genEn", "gridVoltCheck", "mode", "genModel",
            "genRatedPower", "genOptiPPoint", "startDelTime", "genStartElec", "genCloseElec",
            "genStat", "manuSw", "power", "curr", "volt", "freq", "genpowerGen",
            "charge1En", "charge1StartTime", "charge1EndTime", "charge2En", "charge2StartTime",
            "charge2EndTime", "charge3En", "charge3StartTime", "charge3EndTime", "oilmanoEn",
            "manoFre", "manoDate", "manoStartTime", "manoTime", "manoManExit"}
    gw = Gateway(GatewayConfig(setup="in-service", seed=7))
    out = DcPayloads(gw).handlers[1901]({"opt": 0})
    assert set(out) == real
    assert (out["genEn"], out["genStartElec"], out["genCloseElec"], out["startDelTime"]) == (0, 20, 80, 1800)


def test_installed_from_the_control_api_fits_a_working_generator():
    """As `generator: {installed: true}` in a fleet file (#51): from the control API too."""
    from franklinwh_emulator.control import apply
    clock = ManualClock(NIGHT)
    gw = Gateway(GatewayConfig(seed=7, site=SiteConfig(seed=7, tz_offset_hours=10, initial_soc=30)),
                 clock=clock)
    gen = gw.site.generator
    assert not gen.hw.installed and gen.block["genEn"] == BLOCK_DEFAULTS["genEn"]
    apply(gw, "generator", {"installed": True})
    assert gen.block["genEn"] == 1 and gen.block["genRatedPower"] == 7000
    apply(gw, "grid", {"available": False})               # islanded: the generator is needed
    apply(gw, "generator", {"start": True})
    s = after(gw.site, clock, gen.hw.crank_s + gen.hw.warmup_s + 30)
    assert s.generator_w > 0


def test_rated_w_and_explicit_settings_win_over_the_fitted_defaults():
    from franklinwh_emulator.control import apply
    gw = Gateway(GatewayConfig(seed=7), clock=ManualClock(NIGHT))
    apply(gw, "generator", {"rated_w": 5000, "set": {"manuSw": 2}, "installed": True})
    b = gw.site.generator.block
    assert b["genRatedPower"] == 5000 and b["manuSw"] == 2 and b["genEn"] == 1


def test_start_del_time_is_the_fail_to_start_alarm_delay():
    """App: 'Alarm delay after starting fails' 1800 s (PCS Technical Brief p. 12; #179)."""
    s, clock = site(initial_soc=10, gen={"startDelTime": 600})
    s.generator.forced_fault = "fail-start"
    s.set_grid(available=False)
    snap = after(s, clock, 590)
    assert snap.gen_state == 1 and s.generator.fault is None        # still trying, no alarm yet
    snap = after(s, clock, 20)
    assert snap.gen_state == 3 and s.generator.fault == "fail-to-start"
    assert Generator({"installed": True, "start_fail_alarm_s": 900}).block["startDelTime"] == 900


def test_au_generator_module_limits_rating_to_80_a():
    """Generator Module Datasheet (AU & NZ), 2026-06-24: 80 A continuous (#189)."""
    from franklinwh_emulator.control import ApiError, apply
    with pytest.raises(ValueError, match="18400 W continuous limit"):     # 80 A × 230 V
        site(gen={"rated_w": 20000})
    us, _ = site(gen={"rated_w": 20000}, ac_type=1)                       # no NA limit documented
    assert us.generator.max_w is None
    gw = Gateway(GatewayConfig(seed=7), clock=ManualClock(NIGHT))
    for body in ({"rated_w": 20000}, {"set": {"genRatedPower": 20000}}):
        with pytest.raises(ApiError, match="continuous limit"):
            apply(gw, "generator", body)
    s, clock = site(initial_soc=10, gen={"mode": "manual"})
    s.generator.block["genRatedPower"] = 25000          # a Direct Connect 1901 write
    s.generator.relay_closed = True
    assert s.generator.output_w(30000, 0) == 18400
