"""The time machine: jump a gateway years ahead to see ageing (#116)."""

import json
import subprocess

import pytest

from conftest import make_gateway
from franklinwh_emulator.control import ApiError, apply, gateway_state
from test_ui import JSC, STATIC


def test_jump_ages_components_without_simulating_the_gap(clock):
    gw = make_gateway(clock, commissioned="2025-01-01")
    s = gw.site
    soc, pv_wh, soh = s.soc, s.pv_wh, s.soh
    apply(gw, "time-machine", {"years": 8})
    st = gateway_state(gw)
    assert st["time_travelled_days"] == pytest.approx(8 * 365.25)
    assert s.soh < soh - 15 and s._pv_age < 0.97                      # aged eight years
    assert s.soc == pytest.approx(soc, abs=0.5) and s.pv_wh == pytest.approx(pv_wh, abs=10)
    assert s.local_date(s.clock.now()).startswith("2034")
    clock.advance(60)
    assert s.step().ts == pytest.approx(s.clock.now())               # runs on from there
    apply(gw, "time-machine", {"to": "2036-06-01"})
    assert s.local_date(s.clock.now()) == "2036-06-01"
    apply(gw, "time-machine", {"reset": True})
    assert gateway_state(gw)["time_travelled_days"] == 0 and s.soh == pytest.approx(soh, abs=0.2)
    with pytest.raises(ApiError, match="only forward"):
        apply(gw, "time-machine", {"days": -3})


@pytest.mark.skipif(not JSC.exists(), reason="needs a JavaScript runtime (JavaScriptCore)")
def test_card_tag_text(tmp_path):
    h = tmp_path / "t.js"
    h.write_text((STATIC / "groups.js").read_text() +
                 "\nprint(JSON.stringify([timeTravelText({time_travelled_days: 2922}),"
                 " timeTravelText({time_travelled_days: 40})]));\n")
    out = json.loads(subprocess.run([str(JSC), str(h)], capture_output=True, text=True, check=True).stdout)
    assert out == ["time-travelled +8.0 years", "time-travelled +40 days"]
