"""#234: the docs name the cloud API's aPower S MPPT fields instead of calling them unknown."""

from pathlib import Path

DOC = (Path(__file__).resolve().parents[1] / "docs" / "model-baseline.md").read_text()


def test_mppt_reporting_cites_the_cloud_fields():
    assert "How a real aPower S reports DC PV is not known" not in DOC
    for field in ("mpptAllPower", "mPanPv1Power", "mpptEnFlag", "mpptExportEn", "mpptWhArray"):
        assert field in DOC
