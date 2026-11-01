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
    assert all(gw.site.config.group is None and gw.site.sky_seed == gw.config.seed
               for gw in f.gateways.values())
