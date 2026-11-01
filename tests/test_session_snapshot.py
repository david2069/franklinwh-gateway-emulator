"""Session snapshots: save and restore the running state, not only the actions (#88)."""

import pytest

from conftest import make_gateway
from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.control import ControlApi


def test_snapshot_restores_clock_counters_and_history(clock, tmp_path):
    gw = make_gateway(clock, id="home", site={"loads": "sydney-family"})
    api = ControlApi({"home": gw}, workspace=str(tmp_path))
    for _ in range(6 * 60):                                           # six hours
        clock.advance(60)
        gw.site.step()
    api.apply_logged(gw, "load", {"w": 700})
    saved = gw.site.snapshot()
    assert api.save_session("snap", snapshot=True)["snapshot"]

    fresh = make_gateway(ManualClock(1_800_000_000), id="home", site={"loads": "sydney-family"})
    api2 = ControlApi({"home": fresh}, workspace=str(tmp_path))
    r = api2.restore_session("snap")
    assert r["failed"] == 0 and r["snapshot_restored"] == ["home"]
    s = fresh.site
    assert s.clock.now() == pytest.approx(saved["ts"])                # the run continues there
    assert s.soc == pytest.approx(saved["soc"])
    assert s.pv_wh == pytest.approx(saved["counters"]["pv_wh"])
    assert s.history == saved["history"] and s.today_kwh() == gw.site.today_kwh()
    assert s.load_override_w == 700                                   # the actions too
    with pytest.raises(ValueError, match="snapshot version"):
        s.restore_snapshot({**saved, "version": 99})


def test_actions_only_save_has_no_snapshot(clock, tmp_path):
    gw = make_gateway(clock, id="home")
    api = ControlApi({"home": gw}, workspace=str(tmp_path))
    api.save_session("plain")
    assert api.restore_session("plain")["snapshot_restored"] == []
