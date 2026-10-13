"""Appliance load components, presets, smart circuits and their Direct Connect view."""

import pytest

from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.gateway import fix_bool_keys
from franklinwh_emulator.loads import PRESETS, resolve
from franklinwh_emulator.site import Site, SiteConfig

SYD = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney"}
MIDNIGHT = 1768395600            # 2026-01-15T00:00 AEDT


def site(loads, t=MIDNIGHT, **kw):
    kw.setdefault("initial_soc", 50)
    s = Site(SiteConfig(seed=7, location=SYD, loads=loads, **kw), ManualClock(t))
    s.solar_override_w = 0
    return s


def at(s, hours):
    s.clock.set(MIDNIGHT + hours * 3600)
    return s.step()


def test_schedule_and_base():
    s = site([{"name": "base", "type": "base", "w": 300, "drift": 0},
              {"name": "pump", "type": "schedule", "w": 1100, "on": ["10:00-14:00"]}])
    assert at(s, 9).loads == {"base": 300, "pump": 0}
    assert at(s, 11).loads["pump"] == 1100


def test_cycle_duty():
    s = site([{"name": "fridge", "type": "cycle", "w": 100, "period": "10m", "duty": 0.3}])
    on = sum(1 for k in range(600) if at(s, k / 60).loads["fridge"] > 0)
    assert 150 < on < 210                                  # ~30 % of 600 minutes


def test_events_are_seeded_per_day_and_repeatable():
    spec = [{"name": "kettle", "type": "events", "w": 2000, "duration": "3m", "per_day": 4,
             "windows": ["06:00-09:00"]}]
    a, b = site(spec), site(spec)
    ra = [at(a, 6 + k / 60).loads["kettle"] for k in range(180)]
    rb = [at(b, 6 + k / 60).loads["kettle"] for k in range(180)]
    assert ra == rb and 4 <= sum(1 for x in ra if x) <= 12   # four ~3-minute uses
    assert all(at(a, h).loads["kettle"] == 0 for h in (12, 20))   # clock only moves forward
    assert at(site(spec), 3).loads["kettle"] == 0


def test_hvac_follows_temperature():
    s = site([{"name": "ac", "type": "hvac", "w": 2400, "cool_above": 26, "heat_below": 15,
               "span": 4}])
    s.ambient_override_c = 22
    assert at(s, 12).loads["ac"] == 0
    s.ambient_override_c = 35                              # far above: full duty
    assert at(s, 12.1).loads["ac"] == 2400
    s.ambient_override_c = 5
    assert at(s, 12.2).loads["ac"] == 2400                 # heating


def test_ev_offpeak_session_stops_at_kwh():
    s = site([{"name": "ev", "type": "ev", "w": 7000, "arrive": "18:00", "depart": "07:30",
               "kwh": 14, "mode": "offpeak", "start": "22:00"}], initial_soc=20)
    assert at(s, 20).loads["ev"] == 0                      # plugged in, waiting for 22:00
    assert at(s, 22.5).loads["ev"] == 7000
    assert at(s, 24.0).loads["ev"] == 0                    # 14 kWh delivered after 2 h
    assert s.loads.by_name("ev").state["delivered"] == pytest.approx(14000, rel=0.01)


def test_ev_solar_divert_follows_surplus():
    s = site([{"name": "ev", "type": "ev", "w": 7000, "min_w": 1400, "arrive": "08:00",
               "depart": "17:00", "kwh": 40, "mode": "solar"}], initial_soc=100)
    s.solar_override_w = 3000
    assert at(s, 11).loads["ev"] == 3000
    s.solar_override_w = 1000                              # below 6 A: stop
    assert at(s, 11.1).loads["ev"] == 0


def test_circuit_switch_removes_load():
    s = site([{"name": "hw", "type": "schedule", "w": 3600, "on": ["10:00-14:00"], "circuit": 1}])
    assert at(s, 11).circuit_w[1] == 3600
    s.set_circuit(1, False)
    snap = at(s, 11.1)
    assert snap.loads["hw"] == 0 and snap.circuit_w[1] == 0


def test_offgrid_sheds_circuits_first():
    s = site([{"name": "base", "type": "base", "w": 800, "drift": 0},
              {"name": "ac", "type": "schedule", "w": 6000, "on": ["00:00-23:59"], "circuit": 3}],
             initial_soc=60)
    s.set_grid(available=False)
    snap = at(s, 0.1)                                      # shortly after the blackout
    assert snap.circuits_shed and snap.loads["ac"] == 0 and snap.load_w == 800
    assert snap.load_shed_w == 0


def test_offgrid_soc_cutoff():
    s = site([{"name": "base", "type": "base", "w": 300, "drift": 0},
              {"name": "ev", "type": "schedule", "w": 1000, "on": ["00:00-23:59"], "circuit": 2}],
             initial_soc=30, circuits={2: {"soc_low_cutoff": 40}})
    s.set_grid(available=False)
    assert at(s, 0.1).loads["ev"] == 0                     # below its cutoff off-grid


def test_force_appliance():
    s = site([{"name": "kettle", "type": "events", "w": 2200, "duration": "3m", "per_day": 1,
               "windows": ["06:00-07:00"]}])
    s.force_appliance("kettle", True)
    assert at(s, 15).loads["kettle"] == 2200
    s.force_appliance("kettle", None)
    assert at(s, 15.1).loads["kettle"] == 0


def test_preset_resolution_and_validation():
    comps = resolve({"preset": "sydney-family", "remove": ["pool_pump"],
                     "set": {"ev": {"kwh": 30}}, "add": [{"name": "sauna", "type": "base", "w": 1}]})
    names = [c["name"] for c in comps]
    assert "pool_pump" not in names and "sauna" in names
    assert next(c for c in comps if c["name"] == "ev")["kwh"] == 30
    assert PRESETS["sydney-family"][0]["name"] == "base"   # presets are not mutated
    with pytest.raises(ValueError):
        resolve("no-such-preset")
    with pytest.raises(ValueError):
        site([{"name": "x", "type": "teleporter"}])
    with pytest.raises(ValueError):
        site([{"name": "x", "type": "base", "w": 1, "circuit": 4}])


def test_legacy_curve_unchanged_without_loads():
    s = Site(SiteConfig(seed=7, location=SYD), ManualClock(MIDNIGHT))
    assert set(at(s, 19).loads) == {"house"}


def test_yaml_on_key_normalised():
    assert fix_bool_keys({True: ["10:00-14:00"], "n": 1, False: 0}) == \
        {"on": ["10:00-14:00"], "n": 1, "off": 0}


def test_direct_connect_circuits_and_meter():
    pytest.importorskip("franklinwh_local")
    from franklinwh_local.client import LocalClient
    from franklinwh_emulator.gateway import Gateway, GatewayConfig
    clock = ManualClock(MIDNIGHT + 11 * 3600)
    gw = Gateway(GatewayConfig(seed=7, dc_port=0, site=SiteConfig(
        location=SYD, loads=[{"name": "hw", "type": "schedule", "w": 3600,
                              "on": ["10:00-14:00"], "circuit": 1}])), clock=clock)
    with gw:
        c = LocalClient(*gw.dc_address, timeout=2, retries=0)
        c.transport.connect()
        c.login()
        assert c.smart_circuits()["Sw1Mode"] == 1
        assert c.smart_circuit_meter()["SW1ExpPower"] == 3600
        c.set_smart_circuit(1, False)                      # the library's full-block write
        assert gw.site.circuits[1].on is False
        assert c.smart_circuit_meter()["SW1ExpPower"] == 0
        assert c.ibg_run_status()["smartRelay1Status"] == 0
        c.transport.close()
