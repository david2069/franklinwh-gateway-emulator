"""Direct Connect reply payloads, rendered from the gateway's live :class:`Site`.

Field names, types and units follow what real aGates return (franklinwh-direct-connect-api
docs/BACKLOG/test fixtures and franklinwh-local-bridge's parsers; see docs/DIRECT_CONNECT.md).
Static per-aPower shapes (serials, smart-circuit names, programme ids) come from the DC
library's ``SyntheticSite`` so a seed yields the same identities on both emulators.

Request semantics copied from the real device:
* 1725 answers only ``opt:1``; 1727 only ``opt:3`` (other opts: no reply).
* reads needing ``id`` (1703/1705/1833/1835) or ``date`` (1303) without it get the empty key
  set with ``result:1, reason:-1``.
* full-block writes (1205, 1405, 1721) missing keys get ``result:1, reason:-2``.
* other in-band cmdTypes this emulator does not implement get ``result:1, reason:4``;
  out-of-band codes (cloud relay codes such as 203/211) close the connection.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable

from franklinwh_local.synthetic import MODE_IDS, SyntheticSite

from .profiles import DC_FIRMWARE
from .site import MODE_BACKUP, MODE_SELF, MODE_TOU

if TYPE_CHECKING:
    from .gateway import Gateway

# Native mode (15507) ↔ Direct Connect programme id / scheduling_type (1726, cloud workMode).
PROGRAMMES = {
    MODE_SELF: (MODE_IDS["Self-Consumption"], "Self-Consumption", 2),
    MODE_TOU: (MODE_IDS["Time-of-Use"], "Time-of-Use", 1),
    MODE_BACKUP: (MODE_IDS["Emergency Backup"], "Emergency Backup", 3),
}
_ID_TO_MODE = {pid: mode for mode, (pid, _, _) in PROGRAMMES.items()}
ENERGY_CHANNELS = ("kwh_uti_in", "kwh_uti_out", "kwh_sun", "kwh_gen", "kwh_fhp_di",
                   "kwh_fhp_chg", "kwh_load")
# Codes the real aGate answers (with result:1 reason:4 when unsupported) vs. ones that
# make it drop the connection.
IN_BAND = range(1101, 1999)

DROP = object()                      # sentinel: close the connection


def _ok(**kw) -> dict:
    return {"opt": 0, "result": 0, "reason": 0, **kw}


# LFP open-circuit voltage per cell (mV) against SoC: flat, as the reference aPower shows
# (3350–3370 mV at 99.9 %, 2026-10-07 capture).
_LFP_OCV = ((0, 3000), (5, 3150), (10, 3200), (30, 3265), (60, 3300), (90, 3330), (100, 3365))


def _cell_mv(soc: float) -> float:
    for (s0, v0), (s1, v1) in zip(_LFP_OCV, _LFP_OCV[1:]):
        if soc <= s1:
            return v0 + (v1 - v0) * (max(soc, s0) - s0) / (s1 - s0)
    return float(_LFP_OCV[-1][1])


# 1304 sno counts days since commissioning (the gateway's ``commissioned`` date; the reference
# site read 804 on 2026-10-07, i.e. commissioned 2024-07-25).
def _epoch_day(date: str) -> int:
    return int(time.mktime(time.strptime(date, "%Y-%m-%d")) // 86400)


def _refused(reason: int, **kw) -> dict:
    return {"opt": 0, "result": 1, "reason": reason, **kw}


class DcPayloads:
    def __init__(self, gateway: "Gateway"):
        self.gw = gateway
        self.site = gateway.site
        self.synth = SyntheticSite(gateway.config.seed, gateway.serial)
        self.offgrid_soc = int(self.site.config.offgrid_floor_soc)
        self.flag_1207 = dict(self._UNCONFIRMED[1207])
        self.network = {"ethernet0NetSwitch": 1, "ethernet1NetSwitch": 0,
                        "wifiNetSwitch": 1, "4GNetSwitch": 1}
        self.soc_block = {"selfMinSoc": 0, "selfMaxSoc": 0, "touMinSoc": 0, "touMaxSoc": 0,
                          "backupMinSoc": 0, "backupMaxSoc": 0, "genStartSoc": 0,
                          "genStopSoc": 0, "BBBackupSoc": 20}
        self.handlers: dict[int, Callable[[dict], Any]] = {
            1101: self.login, 1105: self.device_check, 1831: self.battery_modules,
            1119: self.network_switches, 1201: self.time_location, 1205: self.der_comms,
            1251: self.compliance, 1301: self.power_flow, 1303: self.energy_history,
            1403: self.mode_config, 1405: self.soc_settings, 1409: self.smart_circuits,
            1411: self.smart_circuit_meter,
            1703: self.power_electronics, 1705: self.battery_cells,
            1707: self.ibg_run_status, 1709: self.relay_status, 1721: self.device_control,
            1723: self.offgrid, 1725: self.mode_list, 1727: self.mode_set,
            1833: self.device_firmware, 1835: self.device_states, 1903: self.solar_pv,
            1901: self.generator, 1829: self.device_alarms,
            **{c: (lambda req, c=c: self.unconfirmed(c, req)) for c in self._UNCONFIRMED},
        }

    def unhandled(self, cmd: int) -> Any:
        return _refused(4) if cmd in IN_BAND else DROP

    # -- helpers -----------------------------------------------------------------
    @property
    def units(self) -> int:
        return max(1, self.site.config.apower_units)

    def programme(self) -> tuple[int, str, int]:
        mode = self.site.native_mode
        return PROGRAMMES[mode]

    def legs(self, s) -> tuple[float, float, float]:
        """(line V, leg-1 V, leg-2 V) as the aGate X reports them. Its power stage is split
        phase internally: on a 230 V single-phase site both legs read half the line voltage
        (reference AU site: gridLineVol 2424, gridVol1 = gridVol2 = 121.2) and carry the same
        current."""
        v = s.v_ln
        if self.site.config.ac_type == 0:
            return v, v / 2, v / 2
        legs = [st.v_ln for st in s.phases.values()]
        v2 = legs[1] if len(legs) > 1 else 0.0
        return (legs[0] + v2 if self.site.config.ac_type == 1 else v), legs[0], v2

    def fhp_sn(self, dev_id: int) -> str:
        return self.synth.apower_serial(dev_id)

    @staticmethod
    def _dev_id(req: dict) -> int | None:
        v = req.get("id")
        return v if isinstance(v, int) and v >= 1 else None

    # -- login / identity ----------------------------------------------------------
    def dc_firmware(self) -> tuple[str, dict]:
        ibg = self.gw.profile.dc_firmware
        try:
            return ibg, DC_FIRMWARE[ibg]
        except KeyError:
            raise ValueError(f"no Direct Connect versions for aGate firmware {ibg!r}; "
                             f"known {sorted(DC_FIRMWARE)}") from None

    def login(self, req: dict) -> dict:
        """1102, in the key order the reference aGate sends (V12R02B60D00_260924)."""
        ibg, fw = self.dc_firmware()
        n, pa, extra = self.units, fw["per_apower"], fw["extra"]
        sns = [self.fhp_sn(i + 1) for i in range(n)]
        hw = {k: v * n for k, v in extra.items() if isinstance(v, list)}     # one per aPower
        return _ok(IBG_SN=self.gw.serial,
                   **({"deviceType": extra["deviceType"]} if "deviceType" in extra else {}),
                   protocolVer=fw["protocolVer"], IBG_VER=ibg, AWS_VER=fw["AWS_VER"],
                   APP_VER=fw["APP_VER"], SL_VER=fw["SL_VER"], FHP_NUM=n, FHP_SN=sns,
                   PE_SN=[sn + "P1" for sn in sns],
                   FPGA_VER=[pa["FPGA_VER"]] * n, DCDC_VER=[pa["DCDC_VER"]] * n,
                   INV_VER=[pa["INV_VER"]] * n, BMS_SN=[sn + "B1" for sn in sns],
                   BMS_VER=[pa["BMS_VER"]] * n, BL_VER=[pa["BL_VER"]] * n, TH_VER=[pa["TH_VER"]] * n,
                   SyHdVersion=fw["SyHdVersion"], METER_VER=fw["METER_VER"],
                   **({"threePhasePvPower": extra["threePhasePvPower"]}
                      if "threePhasePvPower" in extra else {}),
                   **hw)

    def device_check(self, req: dict) -> dict:
        return {**_ok(), "devNum": self.units, "isOver": 1,
                "devMap": [{"id": i + 1, "devSN": self.fhp_sn(i + 1), "checkResult": 1}
                           for i in range(self.units)]}

    def battery_modules(self, req: dict) -> dict:
        return {**_ok(), "devNum": self.units,
                "devMap": [{"id": i + 1, "devSN": self.fhp_sn(i + 1)} for i in range(self.units)]}

    def device_firmware(self, req: dict) -> dict:
        dev = self._dev_id(req)
        keys = ("fhp_sn", "ibg_sn", "ibg_ver", "ibg_iot", "ibg_local", "pe_sn", "pe_ver",
                "bms_sn", "bms_ver")
        if dev is None or dev > self.units:
            return _refused(-1, **{k: "" for k in keys})
        sn = self.fhp_sn(dev)
        ibg, fw = self.dc_firmware()
        return _ok(id=dev, fhp_sn=sn, ibg_sn=self.gw.serial,
                   ibg_ver=ibg, ibg_iot=fw["AWS_VER"],
                   ibg_local=fw["APP_VER"], pe_sn=sn + "P1", pe_ver="",
                   bms_sn=sn + "B1", bms_ver=fw["per_apower"]["BMS_VER"])

    # -- live power ----------------------------------------------------------------
    def power_flow(self, req: dict) -> dict:
        s = self.site.step()
        pid, name, _ = self.programme()
        kwh = self.site.today_kwh()
        n = self.units
        flat = [round(kwh[c], 2) for c in ENERGY_CHANNELS]
        zeros = [0] * len(ENERGY_CHANNELS)
        return _ok(
            # 8 = Debug Mode (cloud enum) only when injected (debug-mode action); not captured.
            # During an M704 force the name is empty; run_status still follows the battery (#91).
            mode=pid, name="" if self.site.remote.enabled else name, electricity_type=1,
            run_status=8 if self.gw.debug_mode else s.run_status,
            p_uti=round(s.grid_w, 1), p_sun=round(s.solar_w, 1), p_gen=round(s.generator_w, 1),
            p_fhp=round(s.battery_w, 1), p_load=round(s.load_w, 1),
            soc=round(s.soc, 1), t_amb=round(s.ambient_c, 1),
            **{k: round(v, 4) for k, v in kwh.items()},
            sharp=zeros, peak=zeros, flat=flat, valley=zeros,
            main_sw=[1 if s.grid_connected else 0, int(s.gen_relay_closed),
                     1 if self.site.pv_relays["proximal"] else 0],
            diStatus=[0, 0, 0, 0], doStatus=[0, 0, 0, 0],
            fhpSn=[self.fhp_sn(i + 1) for i in range(n)],
            fhpSoc=[round(s.soc, 1)] * n,
            fhpPower=[round(s.battery_w / n / 1000.0, 2)] * n,
            elecnet_state=0 if s.grid_connected else 1, slaver_stat=4, infi_status=[0] * n,
            pro_load=[0, 0, 0], genStat=s.gen_state, cd_alm=0, ent=0,
            offgridreason=0 if s.grid_connected else (2 if s.trip_reason == "requested" else 1),
        )

    def energy_history(self, req: dict) -> dict:
        date = req.get("date")
        empty = {"sno": 0, "pointMax": 96, "pointId": 0, "rec_time": [], "p_uti": [],
                 "p_gen": [], "p_fhp": [], "p_load": [],
                 **{c: 0 for c in ENERGY_CHANNELS},
                 "sharp": [0] * 7, "peak": [0] * 7, "flat": [0] * 7, "valley": [0] * 7}
        if not isinstance(date, str) or len(date) != 10 or date[4] != "-":
            return _refused(-1, **empty)
        self.site.step()
        slots = self.site.history.get(date)
        if not slots:
            return _ok(dayType=req.get("dayType", 0), date=date, **empty)
        w = lambda key: [int(round(h[key] / h["s"])) if h["s"] else 0 for h in slots]
        p_fhp = w("fhp")
        totals = {c: 0.0 for c in ENERGY_CHANNELS}
        for h in slots:
            hrs = h["s"] / 3600
            uti = h["uti"] / h["s"] if h["s"] else 0.0
            fhp = h["fhp"] / h["s"] if h["s"] else 0.0
            totals["kwh_uti_in"] += max(0.0, uti) * hrs / 1000
            totals["kwh_uti_out"] += max(0.0, -uti) * hrs / 1000
            totals["kwh_fhp_di"] += max(0.0, fhp) * hrs / 1000
            totals["kwh_fhp_chg"] += max(0.0, -fhp) * hrs / 1000
            totals["kwh_load"] += (h["load"] / h["s"] if h["s"] else 0.0) * hrs / 1000
            totals["kwh_gen"] += (h.get("gen", 0.0) / h["s"] if h["s"] else 0.0) * hrs / 1000
            totals["kwh_sun"] += max(0.0, ((h["load"] - h["uti"] - h["fhp"] - h.get("gen", 0.0)) / h["s"])
                                     if h["s"] else 0.0) * hrs / 1000
        epoch_day = int(time.mktime(time.strptime(date, "%Y-%m-%d")) // 86400)
        last = slots[-1]
        lh = last["s"] / 3600
        lu, lf, ll = ((last[k] / last["s"]) if last["s"] else 0.0 for k in ("uti", "fhp", "load"))
        lg = (last.get("gen", 0.0) / last["s"]) if last["s"] else 0.0
        # The latest quarter-hour's kWh per channel, in ENERGY_CHANNELS order.
        section = [max(0.0, lu) * lh / 1000, max(0.0, -lu) * lh / 1000,
                   max(0.0, ll - lu - lf - lg) * lh / 1000, lg * lh / 1000, max(0.0, lf) * lh / 1000,
                   max(0.0, -lf) * lh / 1000, ll * lh / 1000]
        return _ok(
            sno=max(0, epoch_day - _epoch_day(self.gw.commissioned)), dayType=req.get("dayType", 0),
            date=date, pointMax=96,
            pointId=len(slots),
            rec_time=[f"{(i + 1) * 15 // 60:02d}:{(i + 1) * 15 % 60:02d}" for i in range(len(slots))],
            p_uti=w("uti"), p_gen=w("gen"), p_fhp=p_fhp,
            # Real firmware returns p_fhp again as p_load (DEF-1303-PLOAD-DUP).
            p_load=list(p_fhp) if self.gw.profile.dc_pload_dup_bug else w("load"),
            **{c: round(v, 2) for c, v in totals.items()},
            sharp=[0] * 7, peak=[0] * 7, valley=[0] * 7,
            flat=[round(totals[c], 2) for c in ENERGY_CHANNELS],
            sectionEngy=[round(x, 4) for x in section],
        )

    def power_electronics(self, req: dict) -> dict:
        dev = self._dev_id(req)
        if dev is None or dev > self.units:
            return _refused(-1, id=0, batVol=0, DCDCStatus=0)
        s = self.site.step()
        per = s.battery_w / self.units
        v = s.v_ln
        dcdc = 6 if per < -50 else (7 if per > 50 else 4)
        packv = round(_cell_mv(s.soc) * 16.0 / 1000.0, 1)
        line, v1, v2 = self.legs(s)
        i_load = round(s.load_w / line, 1) if line > 1 else 0.0
        i_inv = round(abs(per) / line, 1) if line > 1 else 0.0
        return _ok(id=dev, batVol=packv, gridVol1=round(v1, 1), gridVol2=round(v2, 1),
                   inverterVolt1=round(v1, 1), inverterVolt2=round(v2, 1),
                   positiveBusVolt=200, negativeBusVolt=200, middleBusVolt=342.7,
                   gridFreq=round(s.hz, 2),
                   loadCurr1=i_load, loadCurr2=i_load if v2 else 0.0,
                   inverterCurr1=i_inv, inverterCurr2=i_inv if v2 else 0.0,
                   buckboostCurr=round(per / packv, 1),
                   runMode=8, inverterStatus=8, DCDCStatus=dcdc)

    def battery_cells(self, req: dict) -> dict:
        dev = self._dev_id(req)
        if dev is None or dev > self.units:
            return _refused(-1, id=0, batVolt=[], batTemp=[])
        s = self.site.step()
        base = _cell_mv(s.soc)
        cells = [int(round(base + ((c * 37 + dev * 11) % 7 - 3) * 3.0)) for c in range(16)]
        temps = [round(s.ambient_c + 2.5 + ((c * 13 + dev) % 5) * 0.3, 1) for c in range(16)]
        per = s.battery_w / self.units
        return _ok(id=dev, batVolt=cells, batTemp=temps,
                   batTotalVolt=round(sum(cells) / 1000.0, 1),
                   singleHighestVolt=max(cells), singleLowestVolt=min(cells),
                   singleHighestTemp=max(temps), singleLowestTemp=min(temps),
                   currGrp=round(per / (sum(cells) / 1000.0), 1), batSoc=round(s.soc, 1),
                   batSoh=94.8, alarmLevel=0)

    def device_states(self, req: dict) -> dict:
        dev = self._dev_id(req)
        if dev is None or dev > self.units:
            return _refused(-1, id=0, ibgDspState=0, ibgMainState=0, peState=0, bmsState=0)
        s = self.site.step()
        per = s.battery_w / self.units
        bms = 6 if per < -50 else (7 if per > 50 else 5)
        return _ok(id=dev, ibgDspState=4, ibgMainState=self.programme()[0],
                   peState=8, bmsState=bms)

    # -- modes ------------------------------------------------------------------------
    def mode_list(self, req: dict) -> dict | None:
        if req.get("opt") != 1:
            return None                              # real aGate: no reply to opt:0
        site = self.site
        reserve = {MODE_SELF: site.self_reserve, MODE_TOU: site.tou_reserve, MODE_BACKUP: 100}
        # The real 1726 has no "reason" (and opt echoes 1).
        return {"current_id": self.programme()[0], "result": 0, "opt": 1, "list": [
            {"id": pid, "name": name, "electricity_type": 1, "reserved_soc": int(reserve[mode]),
             "scheduling_type": sched}
            for mode, (pid, name, sched) in PROGRAMMES.items()]}

    def mode_set(self, req: dict) -> dict | None:
        if req.get("opt") != 3:
            return None
        mode = _ID_TO_MODE.get(req.get("current_id"))
        if mode is None:
            return {"opt": 3, "result": 1}
        self.site.set_native_mode(mode)              # applies immediately; reserve is cloud-owned
        return {"opt": 3, "result": 0}

    def mode_config(self, req: dict) -> dict:
        pid, name, sched = self.programme()
        # Reference capture: modeChoose 14 and an empty name while TOU ran (meaning unknown).
        return _ok(runingMode=pid, modeChoose=sched, name="", electricity_type=1)

    def ibg_run_status(self, req: dict) -> dict:
        s = self.site.step()
        pid, name, sched = self.programme()
        relays = self.site.pv_relays
        grid = 1 if s.grid_connected else 0
        circuits = self.site.circuits
        line, v1, v2 = self.legs(s)
        i_grid = round(abs(s.grid_w) / line, 1) if line > 1 else 0.0
        # No energyMode on V12R02B30D06 (the DC library documents it from the cloud payload).
        # 9 while an M704 force is on: measured on the reference aGate during a Modbus WSet force
        # (fwh-direct-connect-bridge docs/RUN_STATUS_AND_MODE.md §4, 2026-10-12; #91).
        return _ok(ibgRunStatus=9 if self.site.remote.enabled else pid, name="", electricity_type=1, dspRunstatus=4,
                   solarRelayStatus=int(relays["proximal"]), pvRelay2=int(relays["remote1"]),
                   loadRelay1Stat=0, loadRelay2Stat=0,
                   smartRelay1Status=int(circuits[1].on and not s.circuits_shed),
                   smartRelay2Status=int(circuits[2].on and not s.circuits_shed),
                   smartRelay3Status=int(circuits[3].on and not s.circuits_shed),
                   # The app's EMS page lists, in 1708 order: Mains relay, Genset relay,
                   # Photovoltaic relay, Load relay 1/2, EV relay, Mains relay 2 (AU quick
                   # install guide screenshot), so mainRelay2Status is the genset relay and
                   # gridRelay2 is "Mains relay 2". Reference AU site: 1 / 0 / … / 1.
                   mainRelay1Status=grid,
                   mainRelay2Status=int(s.gen_relay_closed), gridRelay2=grid,
                   blackStartRelay=1, gridLineVol=int(round(line * 10)),
                   dspSettingFreq=int(self.site.grid.nominal_hz), gridFreq=round(s.hz, 2),
                   gridVol1=round(v1, 1), gridVol2=round(v2, 1),
                   gridCurr1=i_grid, gridCurr2=i_grid if self.site.config.ac_type else 0.0,
                   loadCurr1=0.0, loadCurr2=0.0, genVoltage=2,
                   isTripPhsPvEn=0, isTripPhsGridEn=0)

    def soc_settings(self, req: dict) -> dict:
        if req.get("opt") == 1:
            if set(self.soc_block) - set(req):
                return _refused(-2)
            return _ok()                     # acknowledged, discarded: cloud-owned
        return _ok(**self.soc_block)

    def smart_circuits(self, req: dict) -> dict:
        """1410 from the site's circuits; a full-block write (opt:1) switches them, so the
        load bound to a circuit really goes away. Schedules are acknowledged and
        discarded, as on the real device."""
        site = self.site
        if req.get("opt") == 1:
            for n, circ in site.circuits.items():
                if f"Sw{n}Mode" in req:
                    site.set_circuit(n, bool(int(req[f"Sw{n}Mode"])))
                if f"Sw{n}SocLowSet" in req:
                    circ.soc_low_cutoff = int(req[f"Sw{n}SocLowSet"])
                if isinstance(req.get(f"Sw{n}Name"), str) and req[f"Sw{n}Name"]:
                    circ.name = req[f"Sw{n}Name"]
            return _ok()
        out = _ok(SwMerge=0, CarSwConsSupEnable=0, CarSwConsSupEnerge=0,
                  CarSwConsSupStartTime="", genStat=0, gridVoltCheck=3)
        for n, circ in site.circuits.items():
            out.update({f"Sw{n}Name": circ.name, f"Sw{n}Mode": int(circ.on),
                        f"Sw{n}ProLoad": int(not circ.on), f"Sw{n}SocLowSet": circ.soc_low_cutoff,
                        f"Sw{n}AtuoEn": 0, f"Sw{n}TimeEn": [0, 0, 0, 0],
                        f"Sw{n}TimeSet": [0, 0, 0, 0], f"Sw{n}Time": ["", "", "", ""],
                        f"Sw{n}MsgType": 0, f"Sw{n}Freq": 0})
        return out

    def smart_circuit_meter(self, req: dict) -> dict:
        """1411: live power/energy on smart circuit 1 (Sw1/SW1 casing as on the device) and
        the car switch (circuit 2 here)."""
        s = self.site.step()
        v = s.v_ln or self.site.grid.nominal_v_ln
        c1, c2 = self.site.circuits[1], self.site.circuits[2]
        p1, p2 = s.circuit_w.get(1, 0.0), s.circuit_w.get(2, 0.0)
        # Units follow the reference capture: volts and Hz ×10 as integers (it read
        # Sw1Volt 1014, freq 500). Energies are kWh here; the device's integer encoding
        # (CarSWExpEnergy 10150, CarSWImpEnergy 16384) is not decoded yet.
        return _ok(Sw1Volt=int(round(v * 10)), Sw2Volt=0,
                   SW1Curr=round(p1 / v, 2) if v else 0.0, SW2Curr=0,
                   SW1ExpPower=round(p1, 1), SW2ExpPower=0,
                   SW1ExpEnergy=round(c1.energy_wh / 1000.0, 3), SW2ExpEnergy=0,
                   CarSWCurr=round(p2 / v, 2) if v else 0.0, CarSWPower=round(p2, 1),
                   CarSWExpEnergy=round(c2.energy_wh / 1000.0, 3), CarSWImpEnergy=0.0,
                   CarSwConsSupExpEnerge=0, power=0, curr=0, volt=2,
                   genpowerGen=int(round(s.generator_w)),
                   freq=int(round(s.hz * 10)))

    # -- grid, relays, off-grid ----------------------------------------------------------
    def relay_status(self, req: dict) -> dict:
        s = self.site.step()
        # genRelayOpen reads 0 on the reference site with no generator; with one fitted it
        # is 1 while the generator is disconnected (interpretation).
        gen = self.site.generator
        return _ok(gridRelayAdhesion=0, gridRelayOpen=0 if s.grid_connected else 1,
                   genRelayAdhesion=0,
                   genRelayOpen=int(gen.usable and not s.gen_relay_closed))

    def offgrid(self, req: dict) -> dict:
        site = self.site
        if req.get("opt") == 1:
            if "offgridSoc" in req:
                self.offgrid_soc = int(req["offgridSoc"])
                site.config.offgrid_floor_soc = float(self.offgrid_soc)
            if "offgridSet" in req:
                site.request_offgrid(bool(req["offgridSet"]))
            return _ok()
        s = site.step()
        return _ok(offgridSet=int(site.offgrid_requested), offgridSoc=self.offgrid_soc,
                   offgridState=0 if s.grid_connected else 1)

    def solar_pv(self, req: dict) -> dict:
        s = self.site.step()
        shares = self.site.config.pv_inputs
        rated = int(round(self.site.solar_peak_w / 100))
        limit = s.export_limit_w if self.site.export_limit_enforced else None
        remote = any(shares.get(k, 0) > 0 for k in ("remote1", "remote2"))
        return {
            **_ok(),
            "remoteSolarEn": int(remote), "remoteSolarMode": 0,
            "solarRatedPower": rated if remote else 0, "installProximalsolar": 0,
            "installPV1port": int(shares.get("proximal", 0) > 0), "installPV2port": 0,
            "PV1RatedPower": rated, "PV2RatedPower": 0,
            "loadSolarAmount": 0, "loadSolar1RatedPower": 0, "loadSolar2RatedPower": 0,
            "mainsSolarRatedPower": 0, "protectTime": 20, "reSolarSoc": 95,
            "solarPower": int(round(s.solar_w * 10)),                  # deciwatts
            "solarRelayStat": int(self.site.pv_relays["proximal"]),
            "loadRelay1Stat": 0, "loadRelay2Stat": 0,
            "solarPowerGen": int(round(self.site.today_kwh()["kwh_sun"] * 10)),
            # -1 = not applied (the reference Ausgrid site); kW when enforced (unverified unit).
            "grid_feed_max": -1 if limit is None else round(limit / 1000.0, 1),
            "threePhPvEnb": int(len(self.site.phases) == 3),
        }

    def device_alarms(self, req: dict) -> dict:
        """1830 deviceAlarms (the app's name; voidstarr/franklinwh_local's recovered enum): the
        reference aGate answers {"num": 0}. The emulator counts active alarm bits (701.Alrm
        and 714.PrtAlrms). The per-alarm list shape when num > 0 is unknown, so it is not
        invented."""
        s = self.site.step()
        return _ok(num=bin(s.alarms).count("1") + bin(s.dc_alarms).count("1"))

    # -- unconfirmed cmdTypes (franklinwh-direct-connect-api#2) -----------------------------------
    # Read replies as captured on the reference aGate (2026-10-07). 1821/1823/1825 are the app's
    # debugMode / powerOn / chargeDevice (cloud cmdType 333): maintenance commands, so dangerous.
    # Their writes are NOT emulated: they get the unsupported reply. 1207 writes are stored
    # (see unknown_1207).
    _UNCONFIRMED = {1207: {"enable": 111, "mode": 222}, 1209: {"enable": 1},
                    1821: {"enable": 0}, 1823: {"powerOn": 0, "powerOff": 0}, 1825: {"power": 0}}

    def unconfirmed(self, cmd: int, req: dict) -> dict:
        if cmd == 1207:
            return self.unknown_1207(req)
        if req.get("opt", 0) != 0:
            return _refused(4)
        return _ok(**self._UNCONFIRMED[cmd])

    def unknown_1207(self, req: dict) -> dict:
        """``opt:1`` writes to 1207 are stored and read back, with no other effect."""
        if req.get("opt", 0) == 1:
            for k in ("enable", "mode"):
                if k in req:
                    self.flag_1207[k] = int(req[k])
            return {**_ok(**self.flag_1207), "opt": 1}
        return _ok(**self.flag_1207)

    # -- generator (1901) ------------------------------------------------------------------------
    def generator(self, req: dict) -> dict:
        """1902: the generator block plus live state. A write (opt:1) must carry the full
        block (as franklinwh_local.set_generator's read-modify-write does); partial frames are
        refused with reason -2, like the other full-block writes (unverified for 1901).
        The trailing power/curr/volt/freq are gateway-level values shared with 1412, not
        generator output; only genpowerGen is generator power."""
        gen = self.site.generator
        if req.get("opt") == 1:
            if set(gen.block) - set(req):
                return _refused(-2)
            for k in gen.block:
                if k != "mode":                      # raw "mode" (2 on the reference) is read-only here
                    gen.block[k] = req[k]
            return _ok()
        s = self.site.step()
        b = gen.block
        out = _ok(genEn=b["genEn"], gridVoltCheck=b["gridVoltCheck"], mode=b["mode"],
                  genModel=b["genModel"], genRatedPower=b["genRatedPower"],
                  genOptiPPoint=b["genOptiPPoint"], startDelTime=b["startDelTime"],
                  genStartElec=b["genStartElec"], genCloseElec=b["genCloseElec"],
                  genStat=s.gen_state, manuSw=b["manuSw"], power=0, curr=0, volt=2,
                  freq=int(round(s.hz * 10)), genpowerGen=int(round(s.generator_w)))
        for n in (1, 2, 3):
            for f in ("En", "StartTime", "EndTime"):
                out[f"charge{n}{f}"] = b[f"charge{n}{f}"]
        for k in ("oilmanoEn", "manoFre", "manoDate", "manoStartTime", "manoTime", "manoManExit"):
            out[k] = b[k]
        return out

    # -- DER comms (Modbus + CSIP-AUS) ---------------------------------------------------------
    _CSIP_STATUS = {"disabled": 0, "registering": 1, "registered": 2, "comms-lost": 3}

    def _der_block(self) -> dict:
        self.site.step()                             # advances 2030.5 registration
        csip, ts = self.site.csip, self.site.clock.now()
        return {"sunsMdEn": int(self.gw.modbus_enabled), "ip": self.gw.config.host,
                "port": self.gw.server.port, "enable": int(csip.enabled), "uri": csip.uri,
                "dcap2030_5": csip.uri, "lfdi": csip.lfdi if csip.registered else "",
                "sfdi": str(csip.sfdi) if csip.registered else "", "pin": csip.pin or "111115",
                "status2030_5": self._CSIP_STATUS[csip.status(ts)]}

    def der_comms(self, req: dict) -> dict:
        if req.get("opt") == 1:
            block = self._der_block()
            if set(block) - set(req):
                return _refused(-2)                  # partial frames are refused
            if int(req["sunsMdEn"]) != block["sunsMdEn"]:
                self.gw.set_modbus_enabled(bool(req["sunsMdEn"]))
            self.site.configure_csip(enabled=bool(req["enable"]), uri=req.get("uri"),
                                     pin=req.get("pin"))
            return _ok()
        return _ok(**self._der_block())

    def compliance(self, req: dict) -> dict:
        code = self.site.grid.name
        return _ok(ComplianceRuleType={"ausgrid": 1, "as4777-au-a": 1}.get(code, 0),
                   IEEE_2030_5_EVAL=1, SUNSPEC_MODBUS_EVAL=1, UI_CAPABILITY_ER=1)

    # -- system ---------------------------------------------------------------------------
    def time_location(self, req: dict) -> dict:
        """1202: local wall time, offset and DST from the site's location/timezone.

        Real aGates send an int ``timezone`` (hours) and usually an empty ``timezoneStr``;
        half-hour zones (Adelaide, Darwin) are sent as a float here. ``laitude`` [sic]."""
        site = self.site
        ts = site.clock.now()
        off = site.utc_offset_hours(ts)
        dst = site.is_dst(ts)
        loc = site.location
        # Reference capture (Sydney, AEDT): timezone 10 with DST 1, i.e. the *standard*
        # offset plus a flag; timezoneStr empty; latitude/longitude as strings.
        std = off - (1 if dst else 0)
        faithful = self.gw.profile.name != "spec"
        return _ok(time=site.local_datetime(ts).strftime("%Y-%m-%d %H:%M:%S"),
                   timezone=int(std) if std == int(std) else std,
                   timezoneStr="" if faithful else site.timezone_name, DST=int(dst),
                   laitude=str(loc.latitude) if loc.latitude is not None else "",
                   longitude=str(loc.longitude) if loc.longitude is not None else "",
                   postcode=loc.postcode)

    def network_switches(self, req: dict) -> dict:
        if req.get("opt") == 1:
            self.network.update({k: int(v) for k, v in req.items() if k in self.network})
            return _ok()
        return _ok(**self.network)

    def device_control(self, req: dict) -> Any:
        block = {"reboot": 0, "reset": 0, "update": 0}
        if req.get("opt") == 1:
            if set(block) - set(req):
                return _refused(-2)
            if req.get("reboot"):
                self.gw.reboot()
                return DROP                          # the real aGate drops the socket
            return _ok()
        return _ok(**block)
