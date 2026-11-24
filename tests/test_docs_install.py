"""#164: the install steps work on any supported Python, and the editable install comes after the
clone; #381: the PyPI install comes first."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_install_steps_use_any_supported_python_and_clone_first():
    assert 'requires-python = ">=3.10"' in (ROOT / "pyproject.toml").read_text()
    for f in ("README.md", "docs/getting-started.md"):
        text = (ROOT / f).read_text()
        assert not re.search(r"python3\.\d+ -m venv", text), f          # no pinned minor version
        clone, editable = text.find("git clone "), text.find('pip install -e ".[dev]"')
        assert 0 <= clone < editable, f
        assert 0 <= text.find("pip install franklinwh-gateway-emulator") < clone, f
