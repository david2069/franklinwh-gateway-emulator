"""#316 step 1: the Cloud API on an address other than loopback, when asked for."""

import json
import subprocess
import sys
import time
import urllib.request

from franklinwh_emulator.cloud import CloudApi
from franklinwh_emulator.gateway import Fleet


def test_loopback_by_default_and_any_address_when_asked():
    assert CloudApi({}, port=0).loopback and CloudApi({}, "::1", 0).loopback
    assert CloudApi({}, "localhost", 0).loopback
    assert not CloudApi({}, "0.0.0.0", 0).loopback and not CloudApi({}, "192.0.2.10", 0).loopback
    f = Fleet.from_dict({"gateways": [{"id": "a", "port": 0, "dc_port": 0, "seed": 3}]})
    f.start(cloud_port=0, cloud_host="0.0.0.0")
    try:
        assert f.cloud.host == "0.0.0.0"
        with urllib.request.urlopen(f"http://127.0.0.1:{f.cloud.port}/hes-gateway/terminal/getHomeGatewayList",
                                    timeout=5) as r:
            assert json.load(r)["code"] == 10009           # reachable; no token
    finally:
        f.stop()
    f = Fleet.from_dict({"gateways": [{"id": "a", "port": 0, "dc_port": 0, "seed": 3}]})
    f.start(cloud_port=0)
    try:
        assert f.cloud.host == "127.0.0.1"
    finally:
        f.stop()


def test_the_cli_warns_when_not_on_loopback(tmp_path):
    fleet = tmp_path / "f.yaml"
    fleet.write_text("gateways:\n  - {id: t, port: 0, dc_port: 0, seed: 3}\n")
    p = subprocess.Popen([sys.executable, "-u", "-c", "import sys; from franklinwh_emulator.cli import main; sys.exit(main(sys.argv[1:]))", "ui", str(fleet), "--port", "0",
                          "--cloud-port", "0", "--cloud-host", "0.0.0.0", "--workspace", str(tmp_path / "ws")],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = []
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = p.stdout.readline()
            if not line:
                break
            lines.append(line)
            if line.startswith("warning: the Cloud API"):
                break
    finally:
        p.terminate()
        p.wait(10)
    out = "".join(lines)
    assert "FranklinWH Cloud API on http://0.0.0.0:" in out
    assert "warning: the Cloud API listens on 0.0.0.0, not loopback" in out
