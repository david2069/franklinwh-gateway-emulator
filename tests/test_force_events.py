"""Force events: one preset on many gateways at once, with auto-clear (#87)."""

import time

import pytest

from conftest import make_gateway
from franklinwh_emulator.control import FORCE_EVENTS, ApiError, ControlApi


@pytest.fixture
def api(clock, tmp_path):
    gws = {gid: make_gateway(clock, id=gid, seed=n) for n, gid in enumerate(("a", "b", "c"))}
    return ControlApi(gws, workspace=str(tmp_path))


def test_event_on_all_and_on_some(api):
    r = api._route("POST", "/events", {"event": "grid-outage"})
    assert [x["ok"] for x in r["results"]] == [True, True, True]
    assert all(not g.site.utility.available["L1"] for g in api.gateways.values())
    api._route("POST", "/events", {"event": "offline", "targets": ["b"]})
    assert api.gateways["b"].faults.offline and not api.gateways["a"].faults.offline
    assert [e["source"] for e in api.log] == ["event:grid-outage"] * 3 + ["event:offline"]
    assert set(api._route("GET", "/events", {})) == set(FORCE_EVENTS)


def test_event_clears_after_its_duration(api):
    api.force_event("slow", ["a", "c"], duration_s=0.05)
    assert api.gateways["a"].faults.latency_s == 2.0
    deadline = time.time() + 2
    while api.gateways["c"].faults.latency_s and time.time() < deadline:
        time.sleep(0.01)
    assert api.gateways["a"].faults.latency_s == api.gateways["c"].faults.latency_s == 0.0
    assert api.log[-1]["source"] == "event:slow:clear"


def test_bad_requests(api):
    with pytest.raises(ApiError, match="unknown event"):
        api.force_event("meteor")
    with pytest.raises(ApiError, match="no gateway"):
        api.force_event("offline", ["zz"])
    with pytest.raises(ApiError, match="duration_s"):
        api.force_event("offline", duration_s=0)
