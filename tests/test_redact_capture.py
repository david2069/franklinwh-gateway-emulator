"""tools/redact_capture.py: what it must mask, and what it must leave alone."""
import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "redact_capture", Path(__file__).resolve().parents[1] / "tools" / "redact_capture.py")
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)

R = "<redacted>"
# Fake identifiers, assembled at run time so the PII scanner doesn't read them as real ones.
MAC_COLON = ":".join(["00", "1A", "2b", "3C", "4d", "5E"])
MAC_DASH = "-".join(["00", "1A", "2B", "3C", "4D", "5E"])


# --- identifying keys -------------------------------------------------------

@pytest.mark.parametrize("key", [
    "sn", "SN", "1.SN", "fhpSn", "devSn", "serial", "serialNumber", "bat_sn", "mac", "wifiMac",
    "ip", "lan_ip", "ipAddr", "lfdi", "sfdi", "pin", "ssid", "lat", "latitude", "lon", "lng",
    "longitude", "address", "email", "password", "token", "location", "postcode", "zip", "city",
    "street"])
def test_identifying_keys_are_masked(key):
    assert rc.scrub({key: "x123"}) == {key: R}


@pytest.mark.parametrize("key", [
    "p_sun", "soc", "fhpPower", "p_fhp", "mode", "run_status", "TmpSnk", "701.TmpSnk",
    "electricity_type", "flat", "isTripPhsAEn", "dspRunstatus", "remoteSolarEn", "installPV1port"])
def test_measurement_keys_are_kept(key):
    assert rc.scrub({key: 12.5}) == {key: 12.5}


def test_nested_and_list_values():
    rec = {"dc": {"list": [{"sn": "A", "soc": 50}, {"sn": "B", "soc": 51}]}}
    assert rc.scrub(rec) == {"dc": {"list": [{"sn": R, "soc": 50}, {"sn": R, "soc": 51}]}}


def test_dict_keys_that_are_serials_are_masked():
    out = rc.scrub({"10050012A3B4C5D6": {"soc": 50}})
    assert list(out) == [R] and out[R] == {"soc": 50}


# --- identifiers inside free-text values -----------------------------------

@pytest.mark.parametrize("text", [
    "aPower 10050012A3B4C5D6 offline",
    "fhp_10050012A3B4C5D6",                       # underscore-joined
    "Connection to 192.0.2.10:502 refused",
    "host fe80::1ff:fe23:4567:890a down",
    "mac " + MAC_COLON,
    "mac " + MAC_DASH,
    "contact someone@example.com",
], ids=["serial", "serial_underscore", "ipv4", "ipv6", "mac_colon", "mac_dash", "email"])
def test_identifiers_in_text_are_masked(text):
    out = rc.scrub({"msg": text})["msg"]
    assert R in out
    for frag in ("10050012A3B4C5D6", "192.0.2.10", "fe80::", "00:1A", "00-1A", "@example.com"):
        assert frag not in out


@pytest.mark.parametrize("text", [
    "V10R01B04D00", "V12R02B30D06", "-0.12345678901234567", "1791442816.169296",
    "2026-10-09T16:00:00+11:00", "18:00:12", "Self-Consumption", "aGate X",
    "FranklinWH Technologies Co., Ltd", "Command Sent: -5000W (100.0% of 5000W)",
])
def test_ordinary_text_is_kept(text):
    assert rc.scrub({"msg": text}) == {"msg": text}


def test_numbers_and_bools_untouched():
    rec = {"p": 4900, "pf": -0.994, "ok": True, "none": None}
    assert rc.scrub(rec) == rec


# --- CLI: thinning and time windows ----------------------------------------

def _write(tmp_path, stamps, slow_at=()):
    src = tmp_path / "in.jsonl"
    with src.open("w") as f:
        for i, ts in enumerate(stamps):
            rec = {"ts": ts, "dc_1301": {"soc": i}}
            if i in slow_at:
                rec["dc_slow"] = {"1405": {}}
            f.write(json.dumps(rec) + "\n")
    return src


def _run(src, dst, *args, monkeypatch):
    monkeypatch.setattr("sys.argv", ["redact_capture.py", str(src), str(dst), *args])
    rc.main()
    return [json.loads(l) for l in dst.read_text().splitlines()]


def _ts(hhmm, day=9):
    return datetime(2026, 10, day, *map(int, hhmm.split(":"))).timestamp()


def test_every_keeps_slow_reads(tmp_path, monkeypatch):
    src = _write(tmp_path, [_ts("12:00") + i for i in range(10)], slow_at={3})
    out = _run(src, tmp_path / "o.jsonl", "--every", "5", monkeypatch=monkeypatch)
    assert [r["dc_1301"]["soc"] for r in out] == [0, 3, 5]


def test_every_must_be_positive(tmp_path, monkeypatch):
    src = _write(tmp_path, [_ts("12:00")])
    with pytest.raises(SystemExit):
        _run(src, tmp_path / "o.jsonl", "--every", "0", monkeypatch=monkeypatch)


def test_window_across_midnight(tmp_path, monkeypatch):
    stamps = [_ts("21:59", 8), _ts("22:30", 8), _ts("01:30", 9), _ts("02:00", 9), _ts("12:00", 9)]
    src = _write(tmp_path, stamps)
    out = _run(src, tmp_path / "o.jsonl", "--from", "22:00", "--to", "02:00", monkeypatch=monkeypatch)
    assert [r["dc_1301"]["soc"] for r in out] == [1, 2]


def test_blank_lines_are_skipped(tmp_path, monkeypatch):
    src = tmp_path / "in.jsonl"
    src.write_text(json.dumps({"ts": _ts("12:00"), "sn": "X"}) + "\n\n")
    out = _run(src, tmp_path / "o.jsonl", monkeypatch=monkeypatch)
    assert out == [{"ts": _ts("12:00"), "sn": R}]
