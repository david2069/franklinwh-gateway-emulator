"""#261: the README's badges point at workflows that exist and match pyproject.toml."""

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()
PYPROJECT = (ROOT / "pyproject.toml").read_text()     # no tomllib on Python 3.10


def _field(name: str) -> str:
    return re.search(rf'^{name} = "([^"]+)"', PYPROJECT, re.M)[1]


def test_workflow_badges_name_existing_workflows():
    files = re.findall(r"/actions/workflows/([\w.-]+\.yml)/badge\.svg", README)
    assert files, "no workflow badges"
    for f in files:
        assert (ROOT / ".github/workflows" / f).is_file(), f


def test_python_and_license_badges_match_pyproject():
    py = unquote(re.search(r"badge/python-([^-)]+)-", README)[1])
    assert py == _field("requires-python")
    lic = re.search(r"badge/license-([^-)]+)-", README)[1]
    assert lic == _field("license")
