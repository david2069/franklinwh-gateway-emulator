"""Cross-check mtnears' franklinwh-modbus#18 register readings (comment 6112488024) against the
reference site's paired capture (2026-10-08 18:52 -> 2026-10-09 16:00 AEDT, 10 s).

    python3 tests/results/2026-10-12_issue18_crosscheck.py ~/franklinwh-captures/paired_2026-10-08_redacted.jsonl
"""
import json
import math
import sys
import time
from collections import Counter


def s16(v):
    return v - 65536 if v is not None and v >= 32768 else v


def fit(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx if sxx else float("nan")
    icpt = my - slope * mx
    r = sxy / math.sqrt(sxx * syy) if sxx and syy else float("nan")
    rms = math.sqrt(sum((y - (slope * x + icpt)) ** 2 for x, y in zip(xs, ys)) / n)
    return n, slope, icpt, r, rms


rows = []
for line in open(sys.argv[1]):
    d = json.loads(line)
    mb, dc = d.get("mb"), d.get("dc_1301")
    if not mb or not dc:
        continue
    rows.append((d["ts"], mb, dc))
t0, t1 = rows[0][0], rows[-1][0]
print(f"samples with both sources: {len(rows)}  ({time.ctime(t0)} -> {time.ctime(t1)}, local time)")


def reg(mb, a):
    return s16(mb.get(f"vreg_{a}"))


def pair(name, xf, yf, filt=lambda m, d: True):
    xs, ys = [], []
    for _, m, d in rows:
        if not filt(m, d):
            continue
        try:
            x, y = xf(m, d), yf(m, d)
        except (TypeError, KeyError):         # a register missing from this sample
            continue
        if x is None or y is None:
            continue
        xs.append(x); ys.append(y)
    n, sl, ic, r, rms = fit(xs, ys)
    print(f"{name}: n={n} slope={sl:.4f} intercept={ic:.1f} r={r:.4f} rms={rms:.2f} "
          f"x[{min(xs):g}..{max(xs):g}] y[{min(ys):g}..{max(ys):g}]")


def distinct(name, f, top=8):
    c = Counter()
    for _, m, d in rows:
        try:
            v = f(m, d)
        except (TypeError, KeyError):         # a register missing from this sample
            continue
        if v is not None:
            c[v] += 1
    print(f"{name}: {len(c)} distinct; top {c.most_common(top)}")


print("\n## Their questions")
pair("15035 vs 1301 soc*10", lambda m, d: d["soc"] * 10, lambda m, d: reg(m, 15035))
print("15040: not in this capture (bridge /points exposes vreg_15000..15039 only).")

print("\n## Their 'new ones'")
pair("15022 vs p_uti", lambda m, d: d["p_uti"], lambda m, d: reg(m, 15022))
pair("15013 vs p_fhp", lambda m, d: d["p_fhp"], lambda m, d: reg(m, 15013))
pair("15003 vs p_sun", lambda m, d: d["p_sun"], lambda m, d: reg(m, 15003))
distinct("15003 values mod 100", lambda m, d: reg(m, 15003) % 100, 4)
distinct("15016", lambda m, d: reg(m, 15016))
distinct("15026", lambda m, d: reg(m, 15026))
distinct("(15016,15026,1301 offgridreason,elecnet_state)",
         lambda m, d: (reg(m, 15016), reg(m, 15026), d.get("offgridreason"), d.get("elecnet_state")))
distinct("15507/15508/15509/16001 via bridge (ongrid_mode, self_reserve, tou_reserve, vreg_16001)",
         lambda m, d: (m.get("ongrid_mode"), m.get("self_reserve"), m.get("tou_reserve"), m.get("vreg_16001")))
for hi, lo in ((15006, 15007), (15010, 15011), (15012, 15013), (15014, 15015)):
    c = Counter()
    for _, m, d in rows:
        h, l = reg(m, hi), reg(m, lo)
        if h is None or l is None:
            continue
        c[(h, "neg" if l < 0 else "nonneg")] += 1
    print(f"pair {hi}:{lo} (hi word, sign of lo): {dict(c)}")

print("\n## Their 'matches' and 'differs'")
distinct("15020", lambda m, d: reg(m, 15020))
distinct("15024 (unsigned, mHz)", lambda m, d: m.get("vreg_15024"))
pair("15024/1000 vs bridge frequency_hz", lambda m, d: m.get("frequency_hz"), lambda m, d: m["vreg_15024"] / 1000)
pair("15025/10 vs bridge voltage_v", lambda m, d: m.get("voltage_v"), lambda m, d: reg(m, 15025) / 10)
distinct("15036", lambda m, d: reg(m, 15036))
distinct("16002", lambda m, d: reg(m, 16002))
distinct("15017", lambda m, d: reg(m, 15017))
pair("15017 vs t_amb", lambda m, d: d["t_amb"], lambda m, d: reg(m, 15017))
pair("15011 vs p_load", lambda m, d: d["p_load"], lambda m, d: reg(m, 15011))
pair("16000 vs p_load", lambda m, d: d["p_load"], lambda m, d: reg(m, 16000))
distinct("15011 values mod 1000", lambda m, d: reg(m, 15011) % 1000, 4)
distinct("16000 values mod 1000", lambda m, d: reg(m, 16000) % 1000, 4)


def u32(m, hi, lo):
    h, l = m.get(f"vreg_{hi}"), m.get(f"vreg_{lo}")
    return None if h is None or l is None else h * 65536 + l


VR = [r for r in rows if r[1].get("vreg_15000") is not None]


# 1301 kwh_* are daily counters; they read the previous day's totals for a few minutes after
# midnight, so the day starts at the first Friday sample where they have reset.
print("\n## Per-day counter deltas (Fri 2026-10-09, from the 1301 daily reset to the last sample)")
day = [r for r in VR if time.localtime(r[0]).tm_mday == 9 and r[2]["kwh_sun"] == 0 and r[2]["kwh_uti_out"] < 0.01]
day = [r for r in VR if r[0] >= day[0][0]]
(ta, ma, da), (tb, mb_, db) = day[0], day[-1]
for name, hi, lo, k in (("TotWhAbs 15033:15034", 15033, 15034, "kwh_uti_in"),
                        ("TotWhInj 15029:15030", 15029, 15030, "kwh_uti_out"),
                        ("15510:15511", 15510, 15511, "kwh_sun"),
                        ("15512:15513", 15512, 15513, "kwh_sun")):
    print(f"{name}: register {(u32(mb_, hi, lo) - u32(ma, hi, lo)) / 1000:.3f} kWh vs 1301 {k} {db[k] - da[k]:.3f} kWh "
          f"({time.strftime('%H:%M', time.localtime(ta))}->{time.strftime('%H:%M', time.localtime(tb))})")

print("\n## Still unknown on their site")
distinct("15007", lambda m, d: reg(m, 15007), 6)
print(f"samples without raw vregs (bridge /points lacked them): {sum(1 for _, m, d in rows if m.get('vreg_15000') is None)}")
pair("15007 vs t_amb", lambda m, d: d["t_amb"], lambda m, d: reg(m, 15007))
pair("15015 vs p_fhp", lambda m, d: d["p_fhp"], lambda m, d: reg(m, 15015))
pair("15015 vs p_sun", lambda m, d: d["p_sun"], lambda m, d: reg(m, 15015))
pair("15015 vs p_load", lambda m, d: d["p_load"], lambda m, d: reg(m, 15015))
pair("15015 vs p_uti", lambda m, d: d["p_uti"], lambda m, d: reg(m, 15015))
distinct("15015", lambda m, d: reg(m, 15015), 6)
