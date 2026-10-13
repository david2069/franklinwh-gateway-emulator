"""Redact a capture (JSONL from paired_collect.py / bms_collect.py) for sharing.

Replaces the values of identifying keys (serials, MAC/IP, PINs, IEEE 2030.5 IDs, location)
with "<redacted>", masks serials, IPs, MACs and emails inside text, and optionally thins the samples.

    python tools/redact_capture.py in.jsonl out.jsonl [--every N] [--from HH:MM --to HH:MM]

Times are local to the machine running it. Check the output by eye before posting it.
"""
import argparse
import json
import re
from datetime import datetime

# Identifying keys, matched on their camelCase / snake_case / dotted tokens ("fhpSn" -> fhp, sn),
# so "TmpSnk" or "isTripPhsAEn" don't match by accident.
ID_TOKENS = {"sn", "serial", "mac", "ip", "ipv4", "ipv6", "lfdi", "sfdi", "pin", "ssid", "lat",
             "latitude", "lon", "lng", "longitude", "address", "addr", "uri", "email", "password",
             "passwd", "token", "location", "geo", "gps", "postcode", "postal", "zip", "city", "street"}
SAFE = {"pin_code_len"}                      # keys the tokens would catch but that are not PII
TOKEN = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")

# Identifiers inside free text. A serial is 14+ upper-case letters and digits with at least one
# of each, so firmware versions (V10R01B04D00, 12 chars) and decimals are kept.
_NB = r"(?<![0-9A-Za-z])", r"(?![0-9A-Za-z])"
TEXT = [
    re.compile(_NB[0] + r"(?=[0-9A-Z]*\d)(?=[0-9A-Z]*[A-Z])[0-9A-Z]{14,}" + _NB[1]),          # serial
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),                                             # email
    re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])"),     # MAC
    re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),                               # IPv4
    re.compile(r"(?<![\w:])(?=[0-9A-Fa-f:]*::|(?:[0-9A-Fa-f]{1,4}:){4})"                       # IPv6
               r"[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![\w:])"),
]


def is_id_key(k):
    return k not in SAFE and any(t.lower() in ID_TOKENS for t in TOKEN.findall(k))


def scrub_text(s):
    for rx in TEXT:
        s = rx.sub("<redacted>", s)
    return s


def scrub(x):
    if isinstance(x, dict):
        return {scrub_text(k): ("<redacted>" if is_id_key(k) else scrub(v)) for k, v in x.items()}
    if isinstance(x, list):
        return [scrub(v) for v in x]
    if isinstance(x, str):
        return scrub_text(x)
    return x


def in_window(hm, t_from, t_to):
    """HH:MM window, [from, to). A window with from > to wraps past midnight."""
    if t_from and t_to and t_from > t_to:
        return hm >= t_from or hm < t_to
    return not ((t_from and hm < t_from) or (t_to and hm >= t_to))


def positive_int(v):
    n = int(v)
    if n < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--every", type=positive_int, default=1, help="keep every Nth sample")
    ap.add_argument("--from", dest="t_from")
    ap.add_argument("--to", dest="t_to")
    a = ap.parse_args()
    kept = 0
    with open(a.src) as f, open(a.dst, "w") as out:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            rec = json.loads(line)
            if a.t_from or a.t_to:
                hm = datetime.fromtimestamp(rec["ts"]).strftime("%H:%M")
                if not in_window(hm, a.t_from, a.t_to):
                    continue
            if i % a.every and "dc_slow" not in rec:
                continue
            out.write(json.dumps(scrub(rec)) + "\n")
            kept += 1
    print(f"{kept} samples -> {a.dst}")


if __name__ == "__main__":
    main()
