"""Solar resource for a site: real Open-Meteo days, or a seeded synthetic sky.

Models (``SiteConfig.weather = {"model": ...}``):

``bell``         the original half-sine day (the default without a location).
``synthetic``    sun position for latitude/longitude/date, clear-sky irradiance on the array
                 plane, and a seeded cloud field (``sky``: clear | broken | overcast | storm).
                 Offline and fully deterministic.
``open-meteo``   the default when the site has a latitude and longitude (#268): real 15-minute
                 global tilted irradiance (and temperature) for the location
                 and day, from the same free API and with the same conversion as
                 franklinwh-local-bridge's solar forecast (GTI / 1000 × kWp × 0.85). Data is
                 cached per day on disk, so a replay is identical once fetched. Open-Meteo's
                 15-minute values are model averages, so seeded cloud-edge flicker scaled by
                 cloud cover is added *within* each 15 minutes, keeping its mean exactly.
                 A day that can't be fetched (no network, an API error, or ``offline`` with
                 nothing cached) falls back to ``synthetic`` for that day; the fetch is tried
                 again after ``RETRY_S``. ``FRANKLINWH_EMULATOR_OFFLINE=1`` sets ``offline``.

Array geometry uses Open-Meteo's convention: ``tilt`` degrees from horizontal, ``azimuth``
0 = south, −90 = east, 90 = west, ±180 = north. A Sydney roof facing north is
``azimuth: 180`` (the default for southern latitudes).

Open-Meteo data is CC BY 4.0 (https://open-meteo.com/).
"""

from __future__ import annotations

import calendar
import json
import logging
import math
import os
import time
import urllib.parse
import urllib.request
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PV_EFFICIENCY = 0.85            # franklinwh-local-bridge solar_forecast.PV_EFFICIENCY
FORECAST_API = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_API = "https://historical-forecast-api.open-meteo.com/v1/forecast"
FORECAST_PAST_DAYS = 85         # the forecast API serves about 92 days back; stay inside
TIMEOUT_S = 20
RETRY_S = 600                   # after a failed fetch, use synthetic and try again this much later

log = logging.getLogger(__name__)

# Synthetic skies: mean cloud cover, cloud opacity, and an overall haze transmittance.
SKIES = {
    "clear":    {"cover": 0.03, "opacity": 0.6, "haze": 1.00},
    "broken":   {"cover": 0.40, "opacity": 0.75, "haze": 0.95},
    "overcast": {"cover": 0.95, "opacity": 0.65, "haze": 0.80},
    "storm":    {"cover": 0.85, "opacity": 0.90, "haze": 0.55},
}


@dataclass
class WeatherConfig:
    model: str = "bell"                 # from_dict: open-meteo when a location is given (#268)
    latitude: float | None = None
    longitude: float | None = None
    kwp: float | None = None            # None → from the site's solar_peak_w
    tilt: float = 20.0
    azimuth: float | None = None        # None → north-facing in the south, south-facing north
    efficiency: float = PV_EFFICIENCY
    sky: str = "broken"                 # synthetic
    flicker: bool = True                # open-meteo: add sub-15-minute cloud edges
    cache_dir: str | None = None        # open-meteo; default ~/.cache/franklinwh-emulator;
                                        # "builtin" = days bundled with the package
    offline: bool = False               # open-meteo: never fetch, cache only
    refresh_h: float = 2.0              # open-meteo: re-fetch today and later after this (#269)

    @classmethod
    def from_dict(cls, d: dict | None) -> "WeatherConfig":
        d = dict(d or {})
        if "model" not in d:                           # #268: real weather wherever we know where
            d["model"] = "open-meteo" if d.get("latitude") is not None and d.get("longitude") is not None else "bell"
        if "offline" not in d and os.environ.get("FRANKLINWH_EMULATOR_OFFLINE") == "1":
            d["offline"] = True
        cfg = cls(**d)
        if cfg.model not in ("bell", "synthetic", "open-meteo"):
            raise ValueError(f"weather model must be bell, synthetic or open-meteo, not {cfg.model!r}")
        if cfg.model != "bell" and (cfg.latitude is None or cfg.longitude is None):
            raise ValueError(f"weather model {cfg.model!r} needs latitude and longitude")
        if cfg.refresh_h <= 0:
            raise ValueError("weather refresh_h must be more than 0 hours")
        if cfg.sky not in SKIES:
            raise ValueError(f"sky must be one of {sorted(SKIES)}")
        if cfg.azimuth is None and cfg.latitude is not None:
            cfg.azimuth = 180.0 if cfg.latitude < 0 else 0.0
        return cfg


# -- sun -------------------------------------------------------------------------------
def sun_position(ts: float, lat: float, lon: float) -> tuple[float, float]:
    """(zenith°, azimuth° in Open-Meteo convention: 0 = south, +90 = west) — NOAA approx."""
    jd = ts / 86400.0 + 2440587.5
    n = jd - 2451545.0
    g = math.radians((357.529 + 0.98560028 * n) % 360)
    q = (280.459 + 0.98564736 * n) % 360
    lam = math.radians((q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360)
    eps = math.radians(23.439 - 0.00000036 * n)
    dec = math.asin(math.sin(eps) * math.sin(lam))
    ra = math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))) % 360
    eot = ((q - ra + 180) % 360 - 180) * 4                       # minutes
    utc_min = (ts % 86400) / 60.0
    solar_min = utc_min + eot + 4 * lon
    ha = math.radians(solar_min / 4 - 180)                       # hour angle
    phi = math.radians(lat)
    cos_z = math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(ha)
    z = math.degrees(math.acos(max(-1.0, min(1.0, cos_z))))
    az = math.degrees(math.atan2(math.sin(ha),
                                 math.cos(ha) * math.sin(phi) - math.tan(dec) * math.cos(phi)))
    return z, az                                                 # az: 0 = south, + = west


def clear_sky_poa(ts: float, lat: float, lon: float, tilt: float, azimuth: float) -> float:
    """Clear-sky irradiance on the array plane (W/m²): Haurwitz GHI, a fixed diffuse share,
    beam by angle of incidence, isotropic diffuse."""
    z, az = sun_position(ts, lat, lon)
    if z >= 90:
        return 0.0
    cz = math.cos(math.radians(z))
    ghi = 1098.0 * cz * math.exp(-0.057 / max(cz, 0.01))
    dhi = 0.15 * ghi
    dni = (ghi - dhi) / max(cz, 0.05)
    tz, ta = math.radians(tilt), math.radians(azimuth)
    cos_i = (cz * math.cos(tz)
             + math.sin(math.radians(z)) * math.sin(tz) * math.cos(math.radians(az) - ta))
    return max(0.0, dni * max(0.0, cos_i) + dhi * (1 + math.cos(tz)) / 2)


# -- seeded cloud field -----------------------------------------------------------------------
@lru_cache(maxsize=65536)
def _knot(seed: int, octave: int, k: int) -> float:
    x = math.sin(k * 12.9898 + seed * 78.233 + octave * 37.719) * 43758.5453
    return x - math.floor(x)


def _noise(seed: int, octave: int, t: float, period: float) -> float:
    k, f = divmod(t / period, 1.0)
    k = int(k)
    f = f * f * (3 - 2 * f)                                       # smoothstep
    return _knot(seed, octave, k) * (1 - f) + _knot(seed, octave, k + 1) * f


def cloud_field(seed: int, ts: float) -> float:
    """Deterministic 0..1 cloud field: cumulus-scale structure (~6 min) plus edges (~40 s)."""
    return 0.65 * _noise(seed, 0, ts, 360.0) + 0.35 * _noise(seed, 1, ts, 40.0)


def _shade(n: float, threshold: float) -> float:
    return max(0.0, min(1.0, (n - threshold) / 0.03 + 0.5))


def cloud_factor(seed: int, ts: float, cover: float, opacity: float) -> float:
    """Transmittance 0..1: shaded (by ``opacity``) where the cloud field is above a
    threshold set so that roughly ``cover`` of the time is shaded."""
    if cover <= 0:
        return 1.0
    if cover >= 1:
        return 1.0 - opacity
    # The field is a sum of smoothed uniforms, so map cover through its quantile.
    threshold = _FIELD_QUANTILES[min(99, int((1.0 - cover) * 100))]
    return 1.0 - opacity * _shade(cloud_field(seed, ts), threshold)


def _field_quantiles() -> list[float]:
    vals = sorted(cloud_field(12345, k * 7.3) for k in range(20000))
    return [vals[int(q / 100 * (len(vals) - 1))] for q in range(100)]


_FIELD_QUANTILES = _field_quantiles()


# -- Open-Meteo --------------------------------------------------------------------------------
class OpenMeteoDay:
    """15-minute GTI/temperature plus hourly cloud cover for one UTC day."""

    def __init__(self, raw: dict):
        m = raw["minutely_15"]
        self.t = [float(x) for x in m["time"]]
        self.gti = [float(x or 0.0) for x in m["global_tilted_irradiance"]]
        self.temp = [None if x is None else float(x) for x in m.get("temperature_2m", [])]
        h = raw.get("hourly", {})
        self.ht = [float(x) for x in h.get("time", [])]
        self.cloud = [float(x or 0.0) / 100.0 for x in h.get("cloud_cover", [])]


def _cache_root(cfg: WeatherConfig) -> Path:
    if cfg.cache_dir == "builtin":                 # days bundled with the package
        from importlib import resources
        return Path(str(resources.files("franklinwh_emulator.weather_data"))) / "open-meteo"
    root = cfg.cache_dir or os.environ.get("FRANKLINWH_EMULATOR_CACHE") \
        or os.path.join(os.path.expanduser("~"), ".cache", "franklinwh-emulator")
    return Path(root) / "open-meteo"


def _cache_path(cfg: WeatherConfig, day: str) -> Path:
    return _cache_root(cfg) / f"{cfg.latitude:.4f}_{cfg.longitude:.4f}_t{cfg.tilt:g}_a{cfg.azimuth:g}_{day}.json"


def _is_current(day: str, now: float | None = None) -> bool:
    """Today (UTC, real time) or later: Open-Meteo's values for it can still change."""
    return day >= time.strftime("%Y-%m-%d", time.gmtime(time.time() if now is None else now))


def _stale(cfg: WeatherConfig, path: Path, day: str) -> bool:
    return _is_current(day) and time.time() - path.stat().st_mtime > cfg.refresh_h * 3600


def _next_day(day: str) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(_utc_midnight(day) + 86400))


def _utc_midnight(day: str) -> int:
    return calendar.timegm(time.strptime(day, "%Y-%m-%d"))


def _split_by_day(raw: dict, days: list[str]) -> dict[str, dict]:
    """One response covering several UTC days → one cache entry per day."""
    out = {}
    for day in days:
        t0 = _utc_midnight(day)
        t1 = t0 + 86400
        part = {k: v for k, v in raw.items() if k not in ("minutely_15", "hourly")}
        for block, lo, hi in (("minutely_15", t0, t1), ("hourly", t0, t1)):
            b = raw.get(block)
            if not b:
                continue
            # 15-minute values are means of the preceding window: (t0, t1] belongs to this day.
            keep = [i for i, t in enumerate(b["time"])
                    if (lo < t <= hi if block == "minutely_15" else lo <= t < hi)]
            part[block] = {k: [v[i] for i in keep] for k, v in b.items()}
        out[day] = part
    return out


def fetch_open_meteo_day(cfg: WeatherConfig, day: str) -> dict:
    """Raw Open-Meteo JSON for one UTC day, cached on disk. A past day is fetched once; today
    and later are fetched again once their copy is ``refresh_h`` old, and today's request also
    brings tomorrow (#269). If a refresh fails, the stale copy is used."""
    path = _cache_path(cfg, day)
    if path.exists() and (cfg.offline or not _stale(cfg, path, day)):
        return json.loads(path.read_text())
    if cfg.offline:
        raise FileNotFoundError(f"open-meteo day {day} not cached at {path} (offline: true)")
    days = [day, _next_day(day)] if _is_current(day) else [day]
    age_days = (time.time() - time.mktime(time.strptime(day, "%Y-%m-%d"))) / 86400
    api = HISTORICAL_API if age_days > FORECAST_PAST_DAYS else FORECAST_API
    params = {"latitude": cfg.latitude, "longitude": cfg.longitude,
              "start_date": days[0], "end_date": days[-1], "tilt": cfg.tilt, "azimuth": cfg.azimuth,
              "minutely_15": "global_tilted_irradiance,temperature_2m",
              "hourly": "cloud_cover", "timezone": "GMT", "timeformat": "unixtime"}
    req = urllib.request.Request(api + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": "franklinwh-gateway-emulator"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            raw = json.loads(r.read())
        if raw.get("error"):
            raise RuntimeError(f"open-meteo: {raw.get('reason')}")
    except (OSError, ValueError, RuntimeError):
        if path.exists():                              # a refresh failed: keep the older copy
            log.warning("weather: refreshing Open-Meteo %s failed; using the copy from %s", day,
                        time.strftime("%H:%M UTC", time.gmtime(path.stat().st_mtime)))
            return json.loads(path.read_text())
        raise
    # Cache the point we asked for, not the grid cell Open-Meteo snapped to (or its
    # elevation): the cell can lie nearer a real address than the requested point.
    raw["latitude"], raw["longitude"] = cfg.latitude, cfg.longitude
    raw.pop("elevation", None)
    parts = _split_by_day(raw, days) if len(days) > 1 else {day: raw}
    path.parent.mkdir(parents=True, exist_ok=True)
    for d, part in parts.items():
        _cache_path(cfg, d).write_text(json.dumps(part))
    return parts[day]


class Weather:
    def __init__(self, cfg: WeatherConfig, *, seed: int, kwp: float):
        self.cfg = cfg
        self.seed = seed
        self.kwp = cfg.kwp or kwp
        self._days: dict[str, OpenMeteoDay] = {}
        self._loaded_at: dict[str, float] = {}    # day → when it was read (real time, #269)
        self._window_means: dict[tuple, float] = {}
        self._retry_at: dict[str, float] = {}     # day → when to try fetching it again (#268)
        self.fallback: str | None = None          # why the last day fell back to synthetic
        self.on_fallback = None                   # (text) -> None: the gateway's event log
        self.clear_sky = clear_sky_poa           # pvlib's when pv: {model: pvlib} (#112)

    # Open-Meteo radiation values are the mean over the *preceding* 15 minutes.
    def _day(self, ts: float) -> OpenMeteoDay | None:
        """That UTC day's Open-Meteo data, or None while it can't be had (synthetic instead)."""
        day = time.strftime("%Y-%m-%d", time.gmtime(ts))
        if day in self._days and not (_is_current(day) and not self.cfg.offline
                                      and time.time() - self._loaded_at[day] > self.cfg.refresh_h * 3600):
            return self._days[day]
        if time.time() < self._retry_at.get(day, 0):
            return None
        try:
            self._days[day] = OpenMeteoDay(fetch_open_meteo_day(self.cfg, day))
            self._loaded_at[day] = time.time()
        except (OSError, ValueError, KeyError, RuntimeError) as e:   # URLError, timeout, offline miss
            if day in self._days:                  # a refresh failed: keep what we have
                self._loaded_at[day] = time.time()
                return self._days[day]
            self._retry_at[day] = time.time() + RETRY_S
            reason = f"Open-Meteo {day} unavailable ({e.__class__.__name__}: {e}); synthetic sky until it is"
            if reason != self.fallback:
                log.warning("weather: %s", reason)
                if self.on_fallback:
                    self.on_fallback(reason)
            self.fallback = reason
            return None
        self._retry_at.pop(day, None)
        self.fallback = None
        return self._days[day]

    def _synthetic_poa(self, ts: float) -> float:
        c = self.cfg
        sky = SKIES[c.sky]
        return (self.clear_sky(ts, c.latitude, c.longitude, c.tilt, c.azimuth)
                * sky["haze"] * cloud_factor(self.seed, ts, sky["cover"], sky["opacity"]))

    def _om_value(self, ts: float) -> tuple[float, float | None, float] | None:
        """(GTI W/m² window mean, temperature, cloud cover 0..1) for the 15-min window, or
        None while that day can't be fetched."""
        d = self._day(ts)
        if d is None:
            return None
        i = bisect_right(d.t, ts)                  # window (t[i-1], t[i]] holds ts
        if i >= len(d.t):
            nxt = self._day(ts + 900)
            if nxt is None:
                return None
            i, d = 0, nxt
        gti = d.gti[i]
        temp = d.temp[i] if i < len(d.temp) else None
        j = max(0, bisect_right(d.ht, ts) - 1)
        cloud = d.cloud[j] if d.cloud else 0.0
        return gti, temp, cloud

    def _flickered(self, ts: float, gti: float, cloud: float) -> float:
        """Sub-15-minute cloud edges that keep the window mean and never exceed clear sky.

        Within a window the sky alternates between the clear-sky level C and a shaded level
        S = (1 - opacity) × C. The shaded fraction x is chosen so the mean equals Open-Meteo's
        value G: x = (C - G) / (C - S). A seeded cloud field decides *when* it is shaded.
        Values at or above clear sky, fully overcast windows and low cloud cover stay smooth.
        """
        c = self.cfg
        if not c.flicker or cloud <= 0.05 or gti <= 0:
            return gti
        cs = clear_sky_poa(ts, c.latitude, c.longitude, c.tilt, c.azimuth)
        opacity = 0.75
        shaded = cs * (1 - opacity)
        if cs <= 0 or gti >= 0.95 * cs or gti <= shaded:
            return gti
        x = (cs - gti) / (cs - shaded)
        window = int(ts // 900)
        key = (window, round(x, 4))
        cached = self._window_means.get(key)
        if cached is None:
            # Threshold at this window's own (1 - x) quantile, so the shaded fraction is
            # x here rather than on average; then correct the small ramp residual.
            start = window * 900
            field = sorted(cloud_field(self.seed, start + k * 5.0) for k in range(180))
            thr = field[min(len(field) - 1, int((1.0 - x) * len(field)))]
            mean = sum(_shade(cloud_field(self.seed, start + k * 5.0), thr)
                       for k in range(180)) / 180
            cached = self._window_means[key] = (thr, mean)
        thr, mean = cached
        if mean < 1e-3:
            return gti
        shade = min(1.0, _shade(cloud_field(self.seed, ts), thr) * x / mean)
        return cs - (cs - shaded) * shade

    def poa(self, ts: float) -> float:
        """Irradiance on the array plane (W/m²)."""
        if self.cfg.model == "synthetic":
            return self._synthetic_poa(ts)
        v = self._om_value(ts)
        if v is None:                              # #268: no Open-Meteo data for now
            return self._synthetic_poa(ts)
        gti, _, cloud = v
        return self._flickered(ts, gti, cloud)

    def solar_w(self, ts: float) -> float:
        return max(0.0, self.poa(ts) / 1000.0 * self.kwp * self.cfg.efficiency * 1000.0)

    def ambient_c(self, ts: float) -> float | None:
        if self.cfg.model != "open-meteo":
            return None
        v = self._om_value(ts)
        return None if v is None else v[1]
