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
from dataclasses import dataclass, field, fields
from typing import Any

from .clock import Clock, ScaledClock, SystemClock
from .control import ControlApi
from .device import AGateDevice
from .profiles import DEFAULT_PROFILE, get_profile
from .server import Faults, ModbusServer
from .site import Site, SiteConfig

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


def mock_serial(seed: int) -> str:
    """20-char, collision-free serial in the real IBG_SN shape (same scheme as the
    Direct Connect emulator's ``SyntheticSite``, so one seed means one serial)."""
    rng = random.Random(seed)
    # SyntheticSite draws its site parameters first; mirror the draws so the serial
    # comes out identical: 4 × random() then randint(-11, 12).
    for _ in range(4):
        rng.random()
    rng.randint(-11, 12)
    return f"MOCK{seed % 10000:04d}" + "".join(rng.choice(_HEX) for _ in range(12))


@dataclass
class GatewayConfig:
    id: str = "agate"
    seed: int = 0
    serial: str | None = None
    host: str = "127.0.0.1"
    port: int = 0                       # 0 → ephemeral
    profile: str = DEFAULT_PROFILE
    quirks: dict[str, Any] = field(default_factory=dict)   # profile overrides
    span_unlock: bool = False           # extension 15507–15509 writes take effect
    time_scale: float = 1.0             # >1 fast-forwards the site clock
    # Direct Connect (TCP/9000) frontend on the same site; None = off, 0 = ephemeral.
    dc_port: int | None = None
    site: SiteConfig = field(default_factory=SiteConfig)

    @classmethod
    def from_dict(cls, d: dict) -> "GatewayConfig":
        d = dict(d)
        site_keys = {f.name for f in fields(SiteConfig)}
        site = {k: d.pop(k) for k in list(d) if k in site_keys and k != "seed"}
        if "modbus" in d:                 # YAML shorthand: modbus: {port: 5020, host: ...}
            d.update(d.pop("modbus") or {})
        if "directconnect" in d:          # YAML shorthand: directconnect: {port: 9000}
            dc = d.pop("directconnect") or {}
            d["dc_port"] = dc.get("port", 0)
        cfg = cls(**d)
        cfg.site = SiteConfig(seed=cfg.seed, **site)
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
        self.site = Site(cfg.site, clock)
        self.site.serial = self.serial
        self.device = AGateDevice(self.site, self.profile, serial=self.serial,
                                  span_unlock=cfg.span_unlock)
        self.faults = Faults()
        self.server = ModbusServer(self.device, cfg.host, cfg.port, self.faults)
        self.dc_faults = Faults()
        self.dc = None
        if cfg.dc_port is not None:
            from .directconnect import DirectConnectServer
            self.dc = DirectConnectServer(self, cfg.host, cfg.dc_port)
        self._loop: _LoopThread | None = None
        self._own_loop = False
        self._event_loop: asyncio.AbstractEventLoop | None = None
        # Direct Connect 1205 sunsMdEn. Writes are delayed-apply (~seconds, both
        # directions) on FW V12R02B30D06: the listener closes/reopens after a delay.
        self.modbus_enabled = True
        self.modbus_apply_delay_s = 3.0
        self.reboot_s = 20.0
        self.episode_phase: str | None = None   # "down" | "offgrid" during episode()

    @property
    def address(self) -> tuple[str, int]:
        return self.server.host, self.server.port

    @property
    def dc_address(self) -> tuple[str, int] | None:
        return None if self.dc is None else (self.dc.host, self.dc.port)

    async def _start_all(self) -> tuple[str, int]:
        addr = await self.server.start()
        if self.dc is not None:
            await self.dc.start()
        return addr

    async def _stop_all(self) -> None:
        if self.dc is not None:
            await self.dc.stop()
        await self.server.stop()

    async def start_async(self) -> tuple[str, int]:
        """Start on the caller's running event loop."""
        self._event_loop = asyncio.get_running_loop()
        return await self._start_all()

    def set_modbus_enabled(self, on: bool) -> None:
        """What a 1205 sunsMdEn write does: stop or start :502 after a short delay."""
        self.modbus_enabled = bool(on)
        loop = self._event_loop
        if loop is None:
            return

        def later():
            loop.call_later(self.modbus_apply_delay_s,
                            lambda: asyncio.ensure_future(self._apply_modbus()))
        loop.call_soon_threadsafe(later)

    def reboot(self, down_s: float | None = None) -> None:
        """What a 1721 reboot does: both listeners drop for ``down_s`` (default
        ``reboot_s``), then come back on the same ports. Site state persists."""
        loop = self._event_loop
        if loop is None:
            return
        delay = self.reboot_s if down_s is None else down_s

        async def cycle():
            await asyncio.sleep(0.05)                   # let the reply path unwind
            await self._stop_all()
            await asyncio.sleep(delay)
            if self.modbus_enabled:
                await self.server.start()
            if self.dc is not None:
                await self.dc.start()
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(cycle()))

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
            await asyncio.sleep(offgrid_s)
            self.site.restore_grid()
            self.episode_phase = None
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(run()))

    async def _apply_modbus(self) -> None:
        running = self.server._server is not None
        if self.modbus_enabled and not running:
            await self.server.start()
        elif not self.modbus_enabled and running:
            await self.server.stop()

    async def stop_async(self) -> None:
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

    def start(self, control_port: int | None = None,
              control_host: str = "127.0.0.1") -> dict[str, tuple[str, int]]:
        """Start every gateway; with ``control_port`` (0 = ephemeral) also the HTTP
        control API, reachable afterwards as ``fleet.control.port``."""
        self._loop = self._loop or _LoopThread("franklinwh-emulator-fleet")
        addresses = {gid: gw.start(self._loop) for gid, gw in self.gateways.items()}
        if control_port is not None and self.control is None:
            self.control = ControlApi(self.gateways, control_host, control_port)
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
