"""A pytest fixture for client repos (franklinwh-modbus, the bridges, async clients).

Copy into your repo's tests/conftest.py. Each test gets a fresh emulated aGate on an ephemeral
port, and its profile and version are recorded with the results, as the shared working rules
require for emulator-verified claims.

What an emulator run does and does not verify: see the table under "Critical changes need real
verification" in this repo's CLAUDE.md.
"""
from importlib.metadata import version

import pytest

from franklinwh_emulator import Gateway, GatewayConfig


@pytest.fixture
def agate(record_property):
    gw = Gateway(GatewayConfig(id="t", seed=7))
    host, port = gw.start()
    record_property("emulator_version", version("franklinwh-gateway-emulator"))
    record_property("emulator_profile", gw.profile.name)
    try:
        yield gw                       # gw.address -> (host, port); gw.site / gw.faults to steer
    finally:
        gw.stop()


def test_example_reads_soc(agate):
    # Replace with your client: e.g. FranklinWHController(*agate.address, unit_id=1).
    from _modbus import read_holding
    assert read_holding(*agate.address, 15035, 1)[0] == round(agate.site._last.soc * 10)
