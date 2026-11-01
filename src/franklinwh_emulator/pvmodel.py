"""pvlib as an optional PV model (#112): ``pv: {model: pvlib}``.

With ``pip install '.[pvlib]'``, `pvlib-python <https://github.com/pvlib/pvlib-python>`_ (BSD)
takes over two steps of the built-in model:

- **Plane of array** (synthetic weather): the clear-sky GHI is split into beam and diffuse by
  Erbs, and transposed with ``transposition`` (``haydavies`` default, ``perez`` or
  ``isotropic``) instead of a fixed 15 % diffuse share with isotropic sky. Open-Meteo already
  serves irradiance on the array, so it is used as it is.
- **Cell temperature**: ``temperature_model`` ``sapm`` (default, open rack glass/glass),
  ``faiman`` or ``pvsyst`` instead of NOCT, at ``wind_m_s`` (default 1 m/s; no wind data).

The built-in model stays the default. Sun position is the emulator's own (NOAA), so both
models see the same sun.
"""

from __future__ import annotations

import math
import time

from .weather import sun_position

MODELS = ("builtin", "pvlib")
TRANSPOSITIONS = ("haydavies", "perez", "isotropic")
TEMPERATURE_MODELS = ("sapm", "faiman", "pvsyst")
KEYS = ("model", "transposition", "temperature_model", "wind_m_s")


class PvlibModel:
    def __init__(self, cfg: dict):
        try:
            import pvlib
        except ImportError:
            raise ValueError("pv model pvlib needs pvlib: pip install '.[pvlib]'") from None
        self.pvlib = pvlib
        self.transposition = cfg.get("transposition", "haydavies")
        self.temperature_model = cfg.get("temperature_model", "sapm")
        self.wind = float(cfg.get("wind_m_s", 1.0))
        if self.transposition not in TRANSPOSITIONS:
            raise ValueError(f"pv.transposition must be one of {list(TRANSPOSITIONS)}")
        if self.temperature_model not in TEMPERATURE_MODELS:
            raise ValueError(f"pv.temperature_model must be one of {list(TEMPERATURE_MODELS)}")

    def clear_sky_poa(self, ts: float, lat: float, lon: float, tilt: float, azimuth: float) -> float:
        """Clear-sky irradiance on the array (W/m²): Haurwitz GHI, Erbs split, pvlib transposition.
        Azimuths here are Open-Meteo's (0 = south); pvlib's are compass (180 = south)."""
        irr = self.pvlib.irradiance
        z, az = sun_position(ts, lat, lon)
        if z >= 90:
            return 0.0
        cz = math.cos(math.radians(z))
        ghi = 1098.0 * cz * math.exp(-0.057 / max(cz, 0.01))
        doy = time.gmtime(ts).tm_yday
        split = irr.erbs(ghi, z, doy)
        res = irr.get_total_irradiance(
            surface_tilt=tilt, surface_azimuth=(azimuth + 180.0) % 360.0,
            solar_zenith=z, solar_azimuth=(az + 180.0) % 360.0,
            dni=float(split["dni"]), ghi=ghi, dhi=float(split["dhi"]),
            dni_extra=float(irr.get_extra_radiation(doy)),
            airmass=float(self.pvlib.atmosphere.get_relative_airmass(z)),
            model=self.transposition)
        v = float(res["poa_global"])
        return 0.0 if math.isnan(v) else max(0.0, v)

    def cell_c(self, poa_w_m2: float, ambient_c: float) -> float:
        t = self.pvlib.temperature
        if self.temperature_model == "sapm":
            p = t.TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_glass"]
            return float(t.sapm_cell(poa_w_m2, ambient_c, self.wind, **p))
        if self.temperature_model == "faiman":
            return float(t.faiman(poa_w_m2, ambient_c, self.wind))
        return float(t.pvsyst_cell(poa_w_m2, ambient_c, self.wind))


def from_config(cfg: dict | None) -> PvlibModel | None:
    """``pv:`` → a pvlib model, or None for the built-in one."""
    cfg = dict(cfg or {})
    bad = set(cfg) - set(KEYS)
    if bad:
        raise ValueError(f"pv: unknown keys {sorted(bad)}; known {list(KEYS)}")
    model = cfg.get("model", "builtin")
    if model not in MODELS:
        raise ValueError(f"pv.model must be one of {list(MODELS)}, not {model!r}")
    return PvlibModel(cfg) if model == "pvlib" else None
