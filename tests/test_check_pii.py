"""scripts/check_pii.py: what it must fail on, and what it must leave alone."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_pii.py"


def scan(tmp_path, text):
    """Run the scanner over a one-file git repo holding ``text``; return its output."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / "check_pii.py")
    (tmp_path / "sample.md").write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    env = {k: v for k, v in os.environ.items() if k != "CI"}
    env["PII_TERMS"] = "placeholder-term"
    r = subprocess.run([sys.executable, "scripts/check_pii.py", "--scan"], cwd=tmp_path,
                       env=env, capture_output=True, text=True)
    return r.returncode, r.stdout


# Addresses assembled at run time so the scanner doesn't read this file as a leak.
def ip(*octets):
    return ".".join(str(o) for o in octets)


def test_private_and_public_ipv4_fail(tmp_path):
    code, out = scan(tmp_path, f"lan {ip(192, 168, 1, 20)}\neth0 {ip(172, 16, 1, 1)}\nwan {ip(8, 8, 8, 8)}\n")
    assert code == 1
    assert out.count("PII Leak [IP ") == 3


def test_loopback_any_and_documentation_ranges_pass(tmp_path):
    ok = [ip(127, 0, 0, 1), ip(0, 0, 0, 0), ip(192, 0, 2, 10), ip(198, 51, 100, 7), ip(203, 0, 113, 99)]
    code, out = scan(tmp_path, "\n".join(ok) + "\n")
    assert code == 0, out


def test_versions_and_out_of_range_octets_pass(tmp_path):
    code, out = scan(tmp_path, f"version 1.2.3\nnot an address {ip(300, 1, 1, 1)}\nfive parts {ip(1, 2, 3, 4, 5)}\n")
    assert code == 0, out
