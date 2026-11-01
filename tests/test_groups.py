"""Fleet groups (#241): one site and grid connection, one single-phase aGate per phase."""
import pytest

from franklinwh_emulator.gateway import Fleet, load_yaml
from franklinwh_emulator.validate import validate_fleet

EXAMPLE = "examples/au-3phase-group.yaml"


def _fleet(**group):
    grp = {"seed": 5, "location": {"latitude": -33.87, "longitude": 151.21,
                                   "timezone": "Australia/Sydney"},
           "weather": {"model": "synthetic", "sky": "broken"}, "service": {"main_breaker_a": 63},
           **group}
    return {"groups": {"home": grp},
            "gateways": [{"id": f"g{i}", "seed": 10 + i, "group": "home", "phases": [ph]}
                         for i, ph in enumerate(("L1", "L2", "L3"))]}


def test_members_share_the_group_settings_and_sky(clock):
    f = Fleet.from_dict(_fleet(), clock=clock)
    sites = [gw.site for gw in f.gateways.values()]
    assert [list(s.phases) for s in sites] == [["L1"], ["L2"], ["L3"]]
    assert {s.config.group for s in sites} == {"home"}
    assert {s.sky_seed for s in sites} == {5}
    assert {s.config.location["timezone"] for s in sites} == {"Australia/Sydney"}
    assert {s.service.main_breaker_a for s in sites} == {63}
    ts = clock.now()
    assert len({round(s.ambient_c(ts), 3) for s in sites}) == 1          # one sky, one ambient
    assert len({round(s.weather.poa(ts), 3) for s in sites}) == 1
    assert len({s.solar_peak_w for s in sites}) == 3                      # the rest stays per aGate


def test_group_seed_defaults_from_its_id(clock):
    f = Fleet.from_dict(_fleet(seed=None) | {"groups": {"home": {"service": {"main_breaker_a": 63}}}},
                        clock=clock)
    assert len({gw.site.sky_seed for gw in f.gateways.values()}) == 1


def test_members_share_one_clock():
    f = Fleet.from_dict(_fleet(time_scale=60))
    clocks = {id(gw.site.clock) for gw in f.gateways.values()}
    base = {id(getattr(gw.site.clock, "base", gw.site.clock)) for gw in f.gateways.values()}
    assert len(clocks) == 1 or len(base) == 1


@pytest.mark.parametrize("change, path, text", [
    (lambda d: d["gateways"][1].update(location={"latitude": 0}), "gateways[1].location", "set by group"),
    (lambda d: d["gateways"][1].update(phases=["L1"]), "gateways[1].phases", "L1 already has gateways[0]"),
    (lambda d: d["gateways"][2].update(phases=["L1", "L2"]), "gateways[2].phases", "one phase"),
    (lambda d: d["gateways"][0].pop("phases"), "gateways[0].phases", "one phase"),
    (lambda d: d["gateways"][0].update(ac_type=2), "gateways[0].ac_type", "single phase"),
    (lambda d: d["gateways"][0].update(group="away"), "gateways[0].group", "no group 'away'"),
    (lambda d: d["groups"]["home"].update(loads="small-flat"), "groups.home.loads", "not a group setting"),
    (lambda d: d["groups"].update(empty={}), "groups.empty", "no gateway names this group"),
])
def test_validate_reports_group_problems(change, path, text):
    d = _fleet()
    change(d)
    problems = validate_fleet(d)
    assert any(p["path"] == path and text in p["error"] for p in problems), problems
    with pytest.raises(ValueError, match=text.split("[")[0]):
        Fleet.from_dict(d)


def test_example_three_phase_home(clock):
    d = load_yaml(EXAMPLE)
    assert validate_fleet(d) == []
    f = Fleet.from_dict(d, clock=clock)
    l1, l2, l3 = (f[g].site for g in ("agate-l1", "agate-l2", "agate-l3"))
    assert (l1.solar_peak_w, l2.solar_peak_w, l3.solar_peak_w) == (6000, 0, 0)
    assert l1.circuit_range.startswith("none") and l2.circuit_range == l3.circuit_range == "1–2"
    assert {s.grid.name for s in (l1, l2, l3)} == {"as4777-au-a"}


def test_ungrouped_fleet_unchanged(clock):
    f = Fleet.from_dict(load_yaml("examples/fleet.yaml"), clock=clock)
    alone = [gw for gw in f.gateways.values() if gw.site.config.group is None]
    assert len(alone) == 5 and all(gw.site.sky_seed == gw.config.seed and gw.group_members == [gw]
                                   for gw in alone)


# --- step 2: one grid connection (#241) ----------------------------------------------------

from franklinwh_emulator.control import ApiError, apply, gateway_state, group_state  # noqa: E402


def test_outage_on_one_member_reaches_the_group(clock):
    f = Fleet.from_dict(_fleet(), clock=clock)
    apply(f["g1"], "grid", {"available": False})
    assert all(not any(gw.site.utility.available.values()) for gw in f.gateways.values())
    apply(f["g0"], "grid", {"restore": True})
    assert all(all(gw.site.utility.available.values()) for gw in f.gateways.values())


def test_phase_event_hits_only_that_phase_but_frequency_is_common(clock):
    f = Fleet.from_dict(_fleet(), clock=clock)
    apply(f["g0"], "grid", {"available": False, "phase": "L2", "hz_offset": -0.5})
    up = {gw.site.phases[0]: all(gw.site.utility.available.values()) for gw in f.gateways.values()}
    assert up == {"L1": True, "L2": False, "L3": True}
    assert {gw.site.utility.hz for gw in f.gateways.values()} == {49.5}
    with pytest.raises(ApiError, match="not connected"):
        apply(f["g0"], "grid", {"available": False, "phase": "L4"})


def test_ungrouped_grid_event_stays_on_its_gateway(clock):
    f = Fleet.from_dict(load_yaml("examples/fleet.yaml"), clock=clock)
    apply(f["site"], "grid", {"available": False})
    assert not any(f["site"].site.utility.available.values())
    assert all(all(f[g].site.utility.available.values()) for g in ("au-3phase-l1", "us-split", "fresh"))


def test_group_state_sums_the_phases(clock):
    f = Fleet.from_dict(load_yaml(EXAMPLE), clock=clock)
    for _ in range(3):
        clock.advance(60)
        for gw in f.gateways.values():
            gw.site.step()
    g = group_state(f["agate-l2"].group_members)
    assert list(g["phases"]) == ["L1", "L2", "L3"]
    assert g["phases"]["L1"]["gateway"] == "agate-l1"
    for k in ("grid_w", "load_w", "solar_w", "battery_w"):
        assert g["totals"][k] == pytest.approx(sum(p[k] for p in g["phases"].values()), abs=0.5)
    assert g["phases"]["L1"]["load_w"] > 0 and g["totals"]["load_w"] > g["phases"]["L1"]["load_w"]
    assert g["service"]["label"] == "3 × 63 A · 400 V"
    st = gateway_state(f["agate-l3"])["group"]
    assert st == {"id": "au-3phase-home", "phase": "L3",
                  "members": {"L1": "agate-l1", "L2": "agate-l2", "L3": "agate-l3"}}
    assert gateway_state(Fleet.from_dict(_fleet(), clock=clock)["g0"])["group"]["phase"] == "L1"


def test_groups_endpoint():
    import json
    import urllib.request
    d = load_yaml(EXAMPLE)
    for g in d["gateways"]:
        g["modbus"], g["directconnect"] = {"port": 0}, {"port": 0}
    f = Fleet.from_dict(d)
    try:
        f.start(control_port=0)
        base = f"http://127.0.0.1:{f.control.port}"
        allg = json.load(urllib.request.urlopen(f"{base}/groups"))
        assert list(allg) == ["au-3phase-home"]
        one = json.load(urllib.request.urlopen(f"{base}/groups/au-3phase-home"))
        assert set(one["phases"]) == {"L1", "L2", "L3"}
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"{base}/groups/nope")
    finally:
        f.stop()


# --- #242: the AU/NZ aGate is single phase ---------------------------------------------------

@pytest.mark.parametrize("site", [{"ac_type": 2}, {"ac_type": 2, "phases": ["L1", "L2"]},
                                  {"ac_type": 1, "grid_code": "as4777-au-a"},
                                  {"ac_type": 2, "location": {"country": "NZ"}}])
def test_au_nz_agate_refuses_more_than_one_phase(clock, site):
    from conftest import make_gateway
    with pytest.raises(ValueError, match="single phase.*one aGate per phase in a fleet group"):
        make_gateway(clock, site=site)
    problems = validate_fleet({"gateways": [{"id": "a", **site}]})
    assert len(problems) == 1 and "fleet group" in problems[0]["error"]


def test_single_phase_au_na_multi_leg_and_spec_still_allowed(clock):
    from conftest import make_gateway
    make_gateway(clock, site={"phases": ["L3"]})                              # AU, one leg
    make_gateway(clock, site={"ac_type": 1})                                  # US split phase
    make_gateway(clock, profile="spec", site={"ac_type": 2})                  # not an aGate
