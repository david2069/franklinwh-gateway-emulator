"""#165: the control API page's examples name a gateway that `serve` starts."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "control-api.md"


def test_examples_use_the_serve_gateway_id():
    serve_id = re.search(r'add_argument\("--id", default="([^"]+)"\)', (ROOT / "src/franklinwh_emulator/cli.py").read_text())[1]
    text = DOC.read_text()
    ids = set(re.findall(r"ctl (?:--json )?(\w[\w-]*) ", text)) | set(re.findall(r"/gateways/(\w[\w-]*)/", text))
    assert ids == {serve_id}, ids
