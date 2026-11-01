"""Replay of recorded bridge metrics (CSV export and SQLite) as solar/load inputs."""

import csv
import sqlite3

import pytest

from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.replay import ReplayConfig, load_rows, to_bridge_csv
from franklinwh_emulator.scenario import run_scenario
from franklinwh_emulator.site import Site, SiteConfig

SYD = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney"}
DAY0 = 1768395600                  # 2026-01-15T00:00 AEDT


def write_csv(path, rows, header=("timestamp", "battery_w", "grid_w", "solar_w", "home_w", "soc")):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return str(path)


def flat_day(path, start=DAY0, hours=48, step=60, solar=1000.0, home=600.0, ts_iso=False):
    rows = []
    for k in range(int(hours * 3600 / step) + 1):
        ts = start + k * step
        stamp = ts if not ts_iso else __import__("datetime").datetime.fromtimestamp(
            ts, __import__("datetime").timezone.utc).isoformat()
        rows.append((stamp, 0, 0, solar if 8 <= ((k * step) / 3600) % 24 < 16 else 0, home, 50))
    return write_csv(path, rows)


def site(replay, t=DAY0, **kw):
    kw.setdefault("initial_soc", 50)
    return Site(SiteConfig(seed=7, location=SYD, replay=replay, **kw), ManualClock(t))


def test_round_trip_tracks_recording(tmp_path):
    gw = {"id": "site", "seed": 7, "initial_soc": 40, "location": SYD, "loads": "sydney-family",
          "weather": {"model": "synthetic", "kwp": 6.6, "tilt": 20, "sky": "broken"}}
    src = run_scenario({"name": "rec", "start": "2026-01-15T00:00:00", "duration": "24h",
                        "record_every": "30s", "fleet": {"gateways": [gw]}, "steps": []})
    path = to_bridge_csv(src.rows, tmp_path / "metrics.csv")
    rep = run_scenario({"name": "rep", "start": "2026-03-02T00:00:00", "duration": "24h",
                        "record_every": "30s",
                        "fleet": {"gateways": [{"id": "site", "seed": 7, "initial_soc": 40,
                                                "location": SYD, "replay": {"source": str(path)}}]},
                        "steps": []})
    cmp = rep.summary["site"]["replay_compare"]
    assert cmp["battery_w"]["mae"] < 5 and cmp["soc"]["mae"] < 0.1
    assert {"rec_battery_w", "rec_grid_w", "rec_soc"} <= set(rep.rows[0])


def test_day_alignment_and_loop(tmp_path):
    s = site({"source": flat_day(tmp_path / "m.csv")}, t=DAY0 + 40 * 86400)   # weeks later
    s.clock.set(DAY0 + 40 * 86400 + 10 * 3600)            # 10:00 local → recorded 10:00
    assert s.step().solar_w == pytest.approx(1000)
    s.clock.set(DAY0 + 40 * 86400 + 5 * 86400 + 20 * 3600)   # beyond 2 days: loops
    assert s.step().solar_w == 0 and s.step().load_w == pytest.approx(600)


def test_absolute_alignment_and_fallback_outside(tmp_path):
    src = flat_day(tmp_path / "m.csv")
    s = site({"source": src, "align": "absolute"}, t=DAY0 + 10 * 3600)
    assert s.step().solar_w == pytest.approx(1000)
    s2 = site({"source": src, "align": "absolute"}, t=DAY0 + 30 * 86400)
    assert set(s2.step().loads) == {"house"}             # outside the recording: model


def test_gap_falls_back_to_model(tmp_path):
    rows = [(DAY0 + k * 60, 0, 0, 0, 500, 50) for k in range(60)]
    rows += [(DAY0 + 3 * 3600 + k * 60, 0, 0, 0, 500, 50) for k in range(60)]   # 2 h gap
    s = site({"source": write_csv(tmp_path / "g.csv", rows), "align": "absolute"},
             t=DAY0 + 1800)
    assert s.step().loads == {"recorded": 500.0}
    s.clock.set(DAY0 + 2 * 3600)
    assert "recorded" not in s.step().loads


def test_iso_timestamps_and_column_mapping(tmp_path):
    p = write_csv(tmp_path / "other.csv",
                  [("2026-01-14T13:00:00Z", 0, 0, 2000, 300, 50),
                   ("2026-01-14T14:00:00Z", 0, 0, 2000, 300, 50)],
                  header=("time", "bat", "grid", "pv", "house", "pct"))
    cfg = ReplayConfig.from_dict({"source": p, "columns": {
        "ts": "time", "solar_w": "pv", "home_w": "house", "battery_w": "bat", "grid_w": "grid",
        "soc": "pct"}})
    rows = load_rows(cfg)
    assert rows[0]["solar_w"] == 2000 and rows[1]["ts"] - rows[0]["ts"] == 3600


def test_sqlite_store_with_archive_and_gateway_filter(tmp_path):
    db = tmp_path / "bridge.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE metrics (ts REAL, battery_w REAL, grid_w REAL, solar_w REAL,"
                " home_w REAL, soc REAL, gateway_id TEXT, ambient_temp_c REAL)")
    con.execute("CREATE TABLE metrics_archive (ts REAL, battery_w REAL, grid_w REAL,"
                " solar_w REAL, home_w REAL, soc REAL, sample_count INTEGER)")
    for k in range(120):
        con.execute("INSERT INTO metrics VALUES (?,?,?,?,?,?,?,?)",
                    (DAY0 + 3600 + k * 30, 0, 0, 1500, 400, 60, "default", 22.5))
        con.execute("INSERT INTO metrics VALUES (?,?,?,?,?,?,?,?)",
                    (DAY0 + 3600 + k * 30, 0, 0, 9999, 9999, 60, "other", 22.5))
    con.execute("INSERT INTO metrics_archive VALUES (?,?,?,?,?,?,?)",
                (DAY0, 0, 0, 1500, 400, 60, 10))
    con.commit()
    con.close()
    rows = load_rows(ReplayConfig.from_dict({"source": str(db), "gateway_id": "default"}))
    assert len(rows) == 121 and max(r["solar_w"] for r in rows) == 1500
    s = site({"source": str(db), "gateway_id": "default", "align": "absolute",
              "use": ["solar", "load", "ambient"], "max_gap": 7200}, t=DAY0 + 4000)
    snap = s.step()
    assert snap.solar_w == pytest.approx(1500) and snap.ambient_c == pytest.approx(22.5)


def test_replay_config_validation(tmp_path):
    with pytest.raises(ValueError):
        ReplayConfig.from_dict({"source": "x.csv", "use": ["wind"]})
    with pytest.raises(FileNotFoundError):
        site(str(tmp_path / "missing.csv"))


def test_cli_bridge_csv_option(tmp_path):
    from franklinwh_emulator.cli import main
    assert main(["scenario", "run", "sydney-summer-day", "--out", str(tmp_path),
                 "--bridge-csv"]) == 0
    out = tmp_path / "sydney-summer-day" / "metrics_home.csv"
    assert out.exists() and out.read_text().startswith("timestamp,battery_w")


def battery_csv(path, battery_w, soc=50, hours=48, step=60):
    rows = [(DAY0 + k * step, battery_w, 0, 0, 300, soc) for k in range(int(hours * 3600 / step) + 1)]
    return write_csv(path, rows)


@pytest.mark.parametrize("preset,expected", [
    ("ideal", 1000 / 13600 * 100),
    ("legacy", 1000 / 13600 * 100),
    # 1 kWh delivered at 91.5 % plus 72 W of overhead for the hour.
    ("agate-measured", (1000 / 0.915 + 72) / 13600 * 100),
])
def test_replayed_battery_drives_soc_through_the_loss_model(tmp_path, preset, expected):
    src = battery_csv(tmp_path / "b.csv", 1000)
    s = site({"source": src, "use": ["solar", "load", "battery"]}, initial_soc=60, battery=preset,
             self_reserve=5)
    s.clock.advance(3600)
    snap = s.step()
    assert snap.battery_w == pytest.approx(1000)          # follows the recording, not the mode
    # expected is per 13.6 kWh; the usable capacity follows SoH (#115)
    assert 60 - snap.soc == pytest.approx(expected * 13600 / s.capacity_wh, rel=1e-3)


def test_charging_efficiency_and_no_drain_while_idle(tmp_path):
    s = site({"source": battery_csv(tmp_path / "c.csv", -1000), "use": ["battery"]},
             initial_soc=40, battery="agate-measured")
    s.clock.advance(3600)
    assert s.step().soc - 40 == pytest.approx(1000 * 0.971 / s.capacity_wh * 100, rel=1e-3)
    idle = site({"source": battery_csv(tmp_path / "i.csv", 0), "use": ["battery"]},
                initial_soc=80, battery="agate-measured")
    idle.clock.advance(6 * 3600)
    assert idle.step().soc == pytest.approx(80)            # full/idle batteries hold


def test_full_recording_reanchors_soc(tmp_path):
    src = battery_csv(tmp_path / "f.csv", 0, soc=100)
    s = site({"source": src, "use": ["battery"]}, initial_soc=96)
    s.clock.advance(60)
    assert s.step().soc == 100.0                           # BMS top-of-charge recalibration
    low = site({"source": src, "use": ["battery"]}, initial_soc=70)
    low.clock.advance(60)
    assert low.step().soc == pytest.approx(70)             # only near the top


def test_battery_config_validation():
    with pytest.raises(ValueError):
        Site(SiteConfig(battery="nope"))
    with pytest.raises(ValueError):
        Site(SiteConfig(battery={"charge_efficiency": 1.2}))
    with pytest.raises(ValueError):
        Site(SiteConfig(battery={"bogus": 1}))
    Site(SiteConfig(battery={"preset": "ideal", "standby_w": 10}))


def dispatch_day(path, start=DAY0, days=2, step=30):
    """Night load 300 W covered by the battery, an 18:00–19:30 discharge at 4.9 kW to the grid
    (a dispatch: Self-Consumption would only cover the load), and a mode/reserve change."""
    header = ("timestamp", "battery_w", "grid_w", "solar_w", "home_w", "soc", "mode_name",
              "self_reserve_pct", "tou_reserve_pct")
    rows = []
    soc = 90.0
    for k in range(int(days * 86400 / step) + 1):
        ts = start + k * step
        h = ((ts - start) % 86400) / 3600
        batt = 4900.0 if 18 <= h < 19.5 else 300.0
        mode, res = ("TOU", 15) if (ts - start) >= 86400 else ("Self-Consumption", 5)
        rows.append((ts, batt, 300 - batt, 0, 300, soc, mode, res, res))
    return write_csv(path, rows, header)


def test_infer_dispatches_finds_the_evening_discharge(tmp_path):
    from franklinwh_emulator.replay import infer_dispatches
    cfg = ReplayConfig.from_dict({"source": dispatch_day(tmp_path / "d.csv"),
                                  "use": ["solar", "load", "dispatch"]})
    found = infer_dispatches(load_rows(cfg), cfg)
    assert len(found) == 2
    for i, d in enumerate(found):
        assert d["setpoint_w"] == 4900.0
        assert abs(d["start"] - (DAY0 + i * 86400 + 18 * 3600)) <= 30
        assert abs((d["end"] - d["start"]) - 5400) <= 60


def test_dispatch_replay_commands_battery_and_native_runs_between(tmp_path):
    src = dispatch_day(tmp_path / "d.csv")
    s = site({"source": src, "use": ["solar", "load", "dispatch"]}, t=DAY0 + 17 * 3600,
             initial_soc=90, self_reserve=5)
    s.clock.advance(3600 + 600)                                   # 18:10
    snap = s.step()
    assert snap.battery_w == pytest.approx(4900)                  # commanded
    assert snap.grid_w == pytest.approx(300 - 4900)                # exported
    s.clock.advance(2 * 3600)
    assert s.step().battery_w == pytest.approx(300)                # native again: load only
    # the emulator's own floor still applies to a commanded discharge
    low = site({"source": src, "use": ["solar", "load", "dispatch"]}, t=DAY0 + 17 * 3600 + 3540,
               initial_soc=6, self_reserve=5)
    low.clock.advance(3600)
    assert low.step().soc == pytest.approx(5.0, abs=0.01)


def test_mode_replay_steps_mode_and_reserves(tmp_path):
    from franklinwh_emulator.site import MODE_SELF, MODE_TOU
    s = site({"source": dispatch_day(tmp_path / "d.csv"), "use": ["load", "mode"]},
             native_mode=MODE_SELF, self_reserve=20, tou_reserve=20)
    s.clock.advance(3600)
    s.step()
    assert (s.native_mode, s.self_reserve) == (MODE_SELF, 5)
    s.clock.advance(86400)
    s.step()
    assert (s.native_mode, s.tou_reserve) == (MODE_TOU, 15)


def test_battery_and_dispatch_are_exclusive(tmp_path):
    with pytest.raises(ValueError):
        ReplayConfig.from_dict({"source": "x.csv", "use": ["battery", "dispatch"]})


def test_compare_splits_native_and_dispatched(tmp_path):
    from franklinwh_emulator.replay import compare
    rows = [{"gateway": "g", "t": i * 10, "battery_w": 100, "grid_w": 0, "soc": 50,
             "rec_battery_w": 100 if i < 5 else 0, "rec_grid_w": 0, "rec_soc": 50,
             "rec_dispatch": None if i < 5 else 4900} for i in range(10)]
    rc = compare(rows, "g")
    assert rc["native"]["battery_w"]["mae"] == 0 and rc["dispatched"]["battery_w"]["mae"] == 100


def test_bms_declares_full_from_98_percent():
    from franklinwh_emulator.clock import ManualClock
    from franklinwh_emulator.site import MODE_SELF
    s = Site(SiteConfig(seed=7, location=SYD, initial_soc=97.9, native_mode=MODE_SELF,
                        solar_peak_w=6000, home_base_w=100), ManualClock(DAY0 + 12 * 3600))
    s.clock.advance(60)
    assert s.step().soc == 100.0
    ideal = Site(SiteConfig(seed=7, location=SYD, initial_soc=97.9, battery="ideal",
                            solar_peak_w=6000, home_base_w=100), ManualClock(DAY0 + 12 * 3600))
    ideal.clock.advance(60)
    assert ideal.step().soc < 100.0
