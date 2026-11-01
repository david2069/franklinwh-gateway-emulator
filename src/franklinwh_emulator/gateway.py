"""Gateways and fleets: the in-process API.

::

    gw = Gateway(GatewayConfig(id="home", seed=7))
    host, port = gw.start()          # serves Modbus TCP on a background thread
    ...
    gw.stop()

    fleet = Fleet.from_yaml("fleet.yaml")
    fleet.start()                    # {"home": ("127.0.0.1", 5020), ...}
    fleet["home"].faults.session_held = True
    fleet.stop()

All gateways of a fleet share one event-loop thread. Each gateway has its own
:class:`Site`, so state never leaks between them.
"""

from __future__ import annotations

import asyncio
import random
import threading
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from .clock import Clock, ScaledClock, SystemClock
from .control import ControlApi
from .device import AGateDevice
from .profiles import DEFAULT_PROFILE, get_profile
from .server import Faults, ModbusServer
from .site import REFERENCE_COUNTERS, Site, SiteConfig

_HEX = "0123456789ABCDEF"


def load_yaml(path: str) -> Any:
    """YAML with the 1.1 boolean-key gotcha undone: an unquoted ``on:``/``off:`` key (common
    in load schedules and circuit switches) parses as True/False — map it back."""
    import yaml
    with open(path, encoding="utf-8") as f:
        return fix_bool_keys(yaml.safe_load(f))


def fix_bool_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {({True: "on", False: "off"}.get(k, k) if isinstance(k, bool) else k):
                fix_bool_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fix_bool_keys(v) for v in obj]
    return obj


def _iso_date(name: str, v: Any) -> str:
    import datetime as _dt
    try:
        return _dt.date.fromisoformat(str(v)).isoformat()
    except ValueError:
        raise ValueError(f"{name} must be YYYY-MM-DD, not {v!r}") from None


def mock_serial(seed: int) -> str:
    """20-char serial in the real IBG_SN shape (same scheme as the Direct Connect emulator's
    ``SyntheticSite``, so one seed means one serial: two gateways on one seed share it, which
    ``validate_fleet`` reports)."""
    rng = random.Random(seed)
    # SyntheticSite draws its site parameters first; mirror the draws so the serial
    # comes out identical: 4 × random() then randint(-11, 12).
    for _ in range(4):
        rng.random()
    rng.randint(-11, 12)
    return f"MOCK{seed % 10000:04d}" + "".join(rng.choice(_HEX) for _ in range(12))


SETUPS = ("factory", "commissioning", "in-service")
# The reference site read 1304 sno = 804 on 2026-10-07.
REFERENCE_COMMISSIONED = "2024-07-25"


@dataclass
class GatewayConfig:
    id: str = "agate"
    seed: int = 0
    serial: str | None = None
    host: str = "127.0.0.1"
    port: int = 0                       # 0 → ephemeral
    profile: str = DEFAULT_PROFILE
    quirks: dict[str, Any] = field(default_factory=dict)   # profile overrides
    time_scale: float = 1.0             # >1 fast-forwards the site clock
    # Direct Connect (TCP/9000) frontend on the same site; 0 = ephemeral. Every aGate profile
    # runs one (None → 0); the non-aGate ``spec`` profile only when a port is given.
    dc_port: int | None = None
    # How the aGate starts. "factory": as an installer finds it, Modbus TCP off until a Direct
    # Connect 1205 sunsMdEn write enables it (owner, 2026-10-30, #40). "in-service": already
    # set up, Modbus TCP on. "commissioning": as factory, with the installer part-way through
    # (#185). ``modbus_enabled`` overrides any of them.
    setup: str = "factory"
    modbus_enabled: bool | None = None
    # When it was commissioned (local date, YYYY-MM-DD): Direct Connect 1304 sno counts days
    # since. Default: the day it starts (factory), or the reference site's date (in-service).
    commissioned: str | None = None
    # The utility's permission to operate (local date, YYYY-MM-DD; the app's PTO Date, #185).
    # Recorded and shown only: no behaviour before or after it is documented.
    pto_date: str | None = None
    # In-service only: run the site model over this many days before start-up, so history,
    # today's totals and the lifetime counters are as if it had been running (max 105).
    history_days: float = 0
    # The gateway's event log (#140): off | normal | verbose, or {level, file}.
    events: Any = None
    site: SiteConfig = field(default_factory=SiteConfig)

    @classmethod
    def from_dict(cls, d: dict) -> "GatewayConfig":
        d = dict(d)
        from .utility import ALIASES, pick_block       # utility / dnsp / lines_company: one block
        block = pick_block(d)
        for k in ALIASES:
            d.pop(k, None)
        if block is not None:
            d["utility"] = block
        site_keys = {f.name for f in fields(SiteConfig)}
        site = {k: d.pop(k) for k in list(d) if k in site_keys and k != "seed"}
        if "modbus" in d:                 # YAML shorthand: modbus: {port: 5020, host: ...}
            d.update(d.pop("modbus") or {})
        if "directconnect" in d:          # YAML shorthand: directconnect: {port: 9000}
            dc = d.pop("directconnect") or {}
            d["dc_port"] = dc.get("port", 0)
        cfg = cls(**d)
        if cfg.setup not in SETUPS:
            raise ValueError(f"setup must be one of {list(SETUPS)}, not {cfg.setup!r}")
        if cfg.history_days and cfg.setup != "in-service":
            raise ValueError(f"history_days needs setup: in-service (a {cfg.setup} aGate has no past)")
        if not 0 <= float(cfg.history_days) <= 105:
            raise ValueError("history_days must be 0-105 (the aGate keeps about 105 days)")
        if cfg.commissioned is not None:
            import datetime as _dt
            try:
                _dt.date.fromisoformat(str(cfg.commissioned))
            except ValueError:
                raise ValueError(f"commissioned must be YYYY-MM-DD, not {cfg.commissioned!r}") from None
            cfg.commissioned = str(cfg.commissioned)
        if cfg.pto_date is not None:
            cfg.pto_date = _iso_date("pto_date", cfg.pto_date)
        cfg.site = SiteConfig(seed=cfg.seed, **site)
        from . import models                           # check the hardware models now (#180)
        from .gridcodes import default_grid_code, get_grid_code
        hz = get_grid_code(cfg.site.grid_code or default_grid_code(cfg.site.ac_type)).nominal_hz
        models.resolve(cfg.site.model, country=str((cfg.site.location or {}).get("country") or ""), hz=hz)
        from .events import EventLog
        EventLog.from_config(cfg.events)               # check it now, not at start-up
        return cfg


class _LoopThread:
    def __init__(self, name: str):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name=name, daemon=True)
        self.thread.start()

    def run(self, coro, timeout: float = 10.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()


class Gateway:
    def __init__(self, config: GatewayConfig | None = None, *, clock: Clock | None = None):
        self.config = cfg = config or GatewayConfig()
        if cfg.site.seed != cfg.seed:
            cfg.site.seed = cfg.seed
        self.profile = get_profile(cfg.profile, **cfg.quirks)
        self.serial = cfg.serial or mock_serial(cfg.seed)
        if clock is None:
            clock = ScaledClock(cfg.time_scale) if cfg.time_scale != 1.0 else SystemClock()
        if cfg.setup != "in-service":                   # fresh from the box: totals at 0
            for k in REFERENCE_COUNTERS:
                if getattr(cfg.site, k) is None:
                    setattr(cfg.site, k, 0.0)
        self.site = Site(cfg.site, clock)
        # Commissioning day, for Direct Connect sno (days since), and the components' install
        # date unless they set their own (#115).
        self.commissioned = cfg.commissioned or (
            self.site.local_date(clock.now()) if cfg.setup != "in-service" else REFERENCE_COMMISSIONED)
        self.site.components.default_installed = self.commissioned
        self.site._age(clock.now(), force=True)
        if cfg.history_days:
            self.site.backfill(float(cfg.history_days))
        self.lifecycle = cfg.setup                      # factory → commissioning → in-service (#185)
        self.pto_date = cfg.pto_date
        self.site.serial = self.serial
        from .events import EventLog
        site = self.site
        self.events = EventLog.from_config(
            cfg.events, site_time=lambda ts: site.local_datetime(ts).isoformat(timespec="seconds"),
            clock_now=lambda: site.clock.now())
        self.site.events = self.events                 # after backfill: its days aren't events
        self.device = AGateDevice(self.site, self.profile, serial=self.serial)
        self.faults = Faults()
        self.server = ModbusServer(self.device, cfg.host, cfg.port, self.faults)
        self.server.clients.notify = lambda what, peer: self.events.client("Modbus TCP", what, peer)
        self.dc_faults = Faults()
        self.dc = None
        is_agate = self.profile.name != "spec"          # every other profile is an aGate
        if cfg.dc_port is None and is_agate:
            cfg.dc_port = 0
        if cfg.dc_port is not None:
            from .directconnect import DirectConnectServer
            self.dc = DirectConnectServer(self, cfg.host, cfg.dc_port)
            self.dc.clients.notify = lambda what, peer: self.events.client("Direct Connect", what, peer)
        self._loop: _LoopThread | None = None
        self._own_loop = False
        self._event_loop: asyncio.AbstractEventLoop | None = None
        # Running reboots, episodes and Modbus on/off changes: cancelled on stop (#21).
        self._background: set[asyncio.Task] = set()
        # Direct Connect 1205 sunsMdEn. Writes are delayed-apply (~seconds, both
        # directions) on FW V12R02B30D06: the listener closes/reopens after a delay.
        if cfg.modbus_enabled is not None:
            self.modbus_enabled = bool(cfg.modbus_enabled)
        else:
            self.modbus_enabled = cfg.setup == "in-service" or not is_agate
        self.modbus_apply_delay_s = 3.0
        self.reboot_s = 20.0
        self.rebooting = False                   # True while reboot() has the listeners down
        self.episode_phase: str | None = None   # "down" | "offgrid" during episode()
        self.debug_mode = False                  # Debug Mode (DC 1821 enable); see set_debug_mode

    @property
    def address(self) -> tuple[str, int]:
        return self.server.host, self.server.port

    @property
    def dc_address(self) -> tuple[str, int] | None:
        return None if self.dc is None else (self.dc.host, self.dc.port)

    async def _start_all(self) -> tuple[str, int]:
        if self.modbus_enabled:
            await self.server.start()
        if self.dc is not None:
            await self.dc.start()
        return self.address

    async def _stop_all(self) -> None:
        if self.dc is not None:
            await self.dc.stop()
        await self.server.stop()

    def _spawn(self, loop: asyncio.AbstractEventLoop, make) -> None:
        """Run ``make()`` (a coroutine) on ``loop`` as a background task, from any thread."""
        def go():
            task = loop.create_task(make())
            self._background.add(task)
            task.add_done_callback(self._background.discard)
        loop.call_soon_threadsafe(go)

    async def _cancel_background(self) -> None:
        tasks = [t for t in self._background if t is not asyncio.current_task()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def start_async(self) -> tuple[str, int]:
        """Start on the caller's running event loop."""
        self._event_loop = asyncio.get_running_loop()
        return await self._start_all()

    # App labels → settings for the commission action (FranklinWH Commissioning Guide, MAC 1 &
    # aPower 2 & aPower S, System Parameters, pp. 18-24).
    ENERGY_EXPORT = {"solar": ("allowed", False), "solar-apower": ("allowed", True),
                     "none": ("prohibited", False)}

    def commission(self, body: dict) -> None:
        """The installer's commissioning (#185): ``start`` (factory → commissioning), the
        System Parameters by their app names, ``pto_date``, ``complete`` (→ in-service, and the
        commissioned date becomes today). Settings need commissioning started; they stay
        changeable in service. What the aGate reports per state is not documented, so only
        the state and the settings change."""
        site = self.site
        if body.get("start"):
            if self.lifecycle != "factory":
                raise ValueError(f"commission start: the gateway is {self.lifecycle}, not factory")
            self.lifecycle = "commissioning"
        settings = {k: body[k] for k in ("grid_import", "grid_import_limit_w", "energy_export",
                                         "grid_export_limit_w", "pcs_derate_w") if k in body}
        if settings and self.lifecycle == "factory":
            raise ValueError("commission: start commissioning first (start: true)")
        if settings:
            u = asdict(site.utility_rules) if site.utility_rules is not None else {}
            if "grid_import" in settings:
                if settings["grid_import"] not in ("allowed", "not-allowed"):
                    raise ValueError("commission grid_import: allowed or not-allowed")
                u["grid_charge"] = settings["grid_import"] == "allowed"
            if "grid_import_limit_w" in settings:
                u["import_limit_w"] = settings["grid_import_limit_w"]
            if "energy_export" in settings:
                if settings["energy_export"] not in self.ENERGY_EXPORT:
                    raise ValueError(f"commission energy_export: one of {list(self.ENERGY_EXPORT)}")
                u["export"], u["battery_export"] = self.ENERGY_EXPORT[settings["energy_export"]]
            if "grid_export_limit_w" in settings:
                u["export_limit_w"] = settings["grid_export_limit_w"]
            if u.get("export") != "prohibited":
                u["export"] = "limited" if u.get("export_limit_w") is not None else "allowed"
            site.set_utility(u)
            if "pcs_derate_w" in settings:
                site.set_pcs_derate(settings["pcs_derate_w"])
        if "pto_date" in body:
            self.pto_date = None if body["pto_date"] is None else _iso_date("pto_date", body["pto_date"])
        if body.get("complete"):
            if self.lifecycle != "commissioning":
                raise ValueError(f"commission complete: the gateway is {self.lifecycle}, not commissioning")
            self.lifecycle = "in-service"
            self.commissioned = site.local_date(site.clock.now())
        self.events.add("commission", f"commissioning: {self.lifecycle}")

    def set_debug_mode(self, on: bool) -> None:
        """Debug Mode, as Direct Connect 1821 ``enable``: on the reference aGate the battery
        stopped (house to the grid, 1301 run_status 2 -> 0) and resumed when it was turned
        off (2026-10-23; one site, a candidate)."""
        self.debug_mode = bool(on)
        self.events.add("debug", "Debug Mode on: battery stopped" if on else "Debug Mode off")
        with self.site.lock:
            self.site.step()
            self.site.battery_held = self.debug_mode

    def set_modbus_enabled(self, on: bool) -> None:
        """What a 1205 sunsMdEn write does: stop or start :502 after a short delay."""
        self.modbus_enabled = bool(on)
        loop = self._event_loop
        if loop is None:
            return

        async def later():
            await asyncio.sleep(self.modbus_apply_delay_s)
            await self._apply_modbus()
        self._spawn(loop, later)

    def reboot(self, down_s: float | None = None) -> None:
        """What a 1721 reboot does: both listeners drop for ``down_s`` (default
        ``reboot_s``), then come back on the same ports. Site state persists."""
        loop = self._event_loop
        if loop is None:
            return
        delay = self.reboot_s if down_s is None else down_s
        self.rebooting = True
        self.events.add("reboot", f"Rebooting: both listeners down for {delay:g} s")

        async def cycle():
            try:
                await asyncio.sleep(0.05)               # let the reply path unwind
                await self._stop_all()
                await asyncio.sleep(delay)
                if self.modbus_enabled:
                    await self.server.start()
                if self.dc is not None:
                    await self.dc.start()
            finally:
                self.rebooting = False
                self.events.add("reboot", "Back up after the reboot")
        self._spawn(loop, cycle)

    def set_firmware(self, name: str, down_s: float | None = None) -> None:
        """A firmware update (#130): the profile ``name`` (with the fleet file's quirks)
        replaces the running one. Its Modbus image, versions (Model 1 Vr, DC 1102/1834) and
        Direct Connect behaviour apply; the site (SoC, energy, settings) and the test write
        rules are kept, and SunSpec settings written by clients return to the new image's
        defaults (what a real update keeps is unmeasured). While serving, both listeners drop
        and come back as on a reboot. ``spec`` is not aGate firmware, so it can't be switched
        to or from."""
        profile = get_profile(name, **self.config.quirks)
        if (profile.name == "spec") != (self.profile.name == "spec"):
            raise ValueError("firmware: spec is not aGate firmware; choose an agate-* profile")
        old = self.profile.name
        with self.device.lock:
            device = AGateDevice(self.site, profile, serial=self.serial)
            device.write_rejects = self.device.write_rejects
            self.profile, self.device = profile, device
            self.server.device = device
            self.config.profile = profile.name
        self.events.add("firmware", f"Firmware update: {old} -> {profile.name}")
        self.reboot(down_s)

    def episode(self, down_s: float, offgrid_s: float) -> None:
        """Drop-and-return-islanded, as the reference aGate did twice on 2026-10-21 (fw
        V12R02B60D00): the utility fails, both listeners go away (open Modbus sessions are
        reset, new connects refused) for ``down_s`` wall seconds, then come back with the site
        islanded; ``offgrid_s`` later the utility returns and the relay recloses after the
        grid code's enter-service delay. Progress is in ``episode_phase``."""
        loop = self._event_loop
        if loop is None:
            return

        async def run():
            self.episode_phase = "down"
            self.events.add("episode", f"Episode: utility lost, listeners down for {down_s:g} s")
            self.site.set_grid(available=False)
            await self.server.stop(rst=True)
            if self.dc is not None:
                await self.dc.stop()
            await asyncio.sleep(down_s)
            if self.modbus_enabled:
                await self.server.start()
            if self.dc is not None:
                await self.dc.start()
            self.episode_phase = "offgrid"
            self.events.add("episode", f"Episode: listeners back, islanded for {offgrid_s:g} s")
            await asyncio.sleep(offgrid_s)
            self.site.restore_grid()
            self.episode_phase = None
        self._spawn(loop, run)

    ADDRESSING_KEYS = ("base_aliases", "write_bases")

    def set_addressing(self, **kw) -> None:
        """Change ``base_aliases`` / ``write_bases`` while serving (the V12 40000 mirror)."""
        from dataclasses import replace
        bad = set(kw) - set(self.ADDRESSING_KEYS)
        if bad:
            raise ValueError(f"unknown addressing keys {sorted(bad)}; known {list(self.ADDRESSING_KEYS)}")
        if "base_aliases" in kw:
            kw["base_aliases"] = tuple(int(b) for b in kw["base_aliases"])
        if kw.get("write_bases") is not None:
            kw["write_bases"] = tuple(int(b) for b in kw["write_bases"])
        self.profile = self.device.profile = replace(self.profile, **kw)

    async def _apply_modbus(self) -> None:
        running = self.server._server is not None
        if self.modbus_enabled and not running:
            await self.server.start()
            self.events.add("modbus", f"Modbus TCP Server opened on port {self.server.port}")
        elif not self.modbus_enabled and running:
            await self.server.stop()
            self.events.add("modbus", "Modbus TCP Server closed")

    async def stop_async(self) -> None:
        await self._cancel_background()
        await self._stop_all()

    def start(self, _loop: _LoopThread | None = None) -> tuple[str, int]:
        """Start serving on a background thread; returns (host, port)."""
        if self._loop is not None:
            return self.address
        self._own_loop = _loop is None
        self._loop = _loop or _LoopThread(f"franklinwh-emulator-{self.config.id}")
        self._event_loop = self._loop.loop
        return self._loop.run(self._start_all())

    def stop(self) -> None:
        if self._loop is None:
            return
        self._loop.run(self._cancel_background())
        self._loop.run(self._stop_all())
        if self._own_loop:
            self._loop.close()
        self._loop = None

    def __enter__(self) -> "Gateway":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


class Fleet:
    def __init__(self, configs: list[GatewayConfig], *, clock: Clock | None = None):
        ids = [c.id for c in configs]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate gateway ids: {ids}")
        self.gateways = {c.id: Gateway(c, clock=clock) for c in configs}
        self.control: ControlApi | None = None
        self._loop: _LoopThread | None = None

    @classmethod
    def from_dict(cls, d: dict, *, clock: Clock | None = None) -> "Fleet":
        defaults = d.get("defaults", {})
        return cls([GatewayConfig.from_dict({**defaults, **g}) for g in d["gateways"]],
                   clock=clock)

    @classmethod
    def from_yaml(cls, path: str) -> "Fleet":
        return cls.from_dict(load_yaml(path))

    def __getitem__(self, gw_id: str) -> Gateway:
        return self.gateways[gw_id]

    def start(self, control_port: int | None = None, control_host: str = "127.0.0.1",
              *, ui: bool = False, workspace: str = "emulator-workspace") -> dict[str, tuple[str, int]]:
        """Start every gateway; with ``control_port`` (0 = ephemeral) also the HTTP
        control API, reachable afterwards as ``fleet.control.port``. ``ui``: serve the web UI
        on that port too."""
        self._loop = self._loop or _LoopThread("franklinwh-emulator-fleet")
        addresses = {gid: gw.start(self._loop) for gid, gw in self.gateways.items()}
        if control_port is not None and self.control is None:
            if ui:
                from .ui.server import UiServer
                self.control = UiServer(self.gateways, control_host, control_port, workspace=workspace)
            else:
                self.control = ControlApi(self.gateways, control_host, control_port, workspace=workspace)
            self._loop.run(self.control.start())
        return addresses

    def stop(self) -> None:
        if self.control is not None and self._loop is not None:
            self._loop.run(self.control.stop())
            self.control = None
        for gw in self.gateways.values():
            gw.stop()
        if self._loop is not None:
            self._loop.close()
            self._loop = None

    def __enter__(self) -> "Fleet":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
