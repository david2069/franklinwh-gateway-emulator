"""#163: a build after a release must not report the release's version."""

import re
from pathlib import Path

from franklinwh_emulator import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_package_and_pyproject_agree():
    assert re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)[1] == __version__


def test_unreleased_changes_mean_a_dev_version():
    log = (ROOT / "CHANGELOG.md").read_text()
    unreleased = log.split("## Unreleased", 1)[1].split("\n## ", 1)[0] if "## Unreleased" in log else ""
    if re.search(r"^- ", unreleased, re.M):
        assert ".dev" in __version__, "CHANGELOG has unreleased entries: the version must be <next>.devN"


def test_changelog_unreleased_uses_each_category_once():
    """#136: Keep a Changelog categories, each heading once, in the standard order."""
    log = (ROOT / "CHANGELOG.md").read_text()
    unreleased = log.split("## Unreleased", 1)[1].split("\n## ", 1)[0]
    heads = re.findall(r"^### (\w+)", unreleased, re.M)
    kac = ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"]
    assert heads == [h for h in kac if h in heads], heads


def test_pypi_accepts_the_package():
    """#381: PyPI rejects direct (git, URL) dependencies, extras included, and the README is the
    PyPI page, where relative links don't resolve."""
    import tomllib
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    deps = project["dependencies"] + [d for x in project["optional-dependencies"].values() for d in x]
    assert [d for d in deps if "@" in d or "://" in d] == []
    assert re.findall(r"\]\((?!https?://|#)[^)]*\)", (ROOT / "README.md").read_text()) == []
