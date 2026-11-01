from conftest import make_gateway


def test_solar_peak_zero_means_no_solar(clock):
    """#238: ``solar_peak_w: 0`` is a site without solar, not the seed's default."""
    gw = make_gateway(clock, seed=1, site={"solar_peak_w": 0})
    s = gw.site
    assert s.solar_peak_w == 0
    for _ in range(5):                       # noon UTC, tz offset 0: full sun if there were PV
        clock.advance(60)
        snap = s.step()
    assert snap.solar_w == 0 and snap.solar_available_w == 0


def test_solar_peak_unset_still_from_seed(clock):
    a = make_gateway(clock, seed=1).site.solar_peak_w
    assert 3500 <= a <= 6000 and a == make_gateway(clock, seed=1).site.solar_peak_w
