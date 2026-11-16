"""Lumped RC model of 70 Reserve Pl and its equipment, stepped every 30 s.

Nodes per zone: air and interior mass; radiant zones add a floor node fed by boiler water while
the zone valve is open. The west_wing thermostat drives both wing floors (EAST and WEST).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Optional

ZONES = ("EAST", "WEST", "FIRST", "BASEMENT", "GARAGE")
HEATED = ("EAST", "WEST", "FIRST", "BASEMENT")
THERMOSTATS = {"EAST": "climate.east_wing", "WEST": "climate.west_wing", "FIRST": "climate.first_floor",
               "BASEMENT": "climate.basement", "GARAGE": "climate.garage"}
# thermostat -> floors its valve feeds
VALVES = {"WEST": ("EAST", "WEST"), "FIRST": ("FIRST",), "BASEMENT": ("BASEMENT",), "GARAGE": ("GARAGE",)}
ERV_CFM = {"Off": 0, "Minimum": 40, "Medium": 65, "Maximum": 130, "Auto": 65}


@dataclass(frozen=True)
class Params:
    ua_house: float = 550.0          # BTU/h·°F, heated zones, envelope + infiltration at ref wind
    inf_share: float = 0.15          # share of ua_house that is infiltration at ref wind
    ref_wind: float = 8.0            # mph
    c_air_house: float = 1500.0      # BTU/°F
    c_mass_house: float = 15000.0
    h_air_mass_house: float = 4000.0  # BTU/h·°F
    wood_c_floor: float = 3000.0
    wood_ua_emitter: float = 600.0
    wood_h_floor_air: float = 600.0
    slab_c_floor: float = 25000.0
    slab_ua_emitter: float = 1500.0
    slab_h_floor_air: float = 600.0
    floor_lag_scale: float = 1.0     # multiplies C_floor (sweep knob)
    water_wood: float = 105.0       # effective mean water temp in staple-up loops, °F
    water_slab: float = 100.0
    hp_capacity: float = 32000.0     # BTU/h
    hp_cop: float = 3.3
    hp_split: tuple = (("EAST", 0.60), ("WEST", 0.25), ("FIRST", 0.15))
    hp_start_delay_s: float = 30.0
    hp_anti_short_cycle_s: float = 300.0
    fan_heat: float = 760.0
    fan_mix: float = 300.0           # extra conductance between each pair of EAST/WEST/FIRST with fan running
    erv_eff: float = 0.7
    ground_t: float = 50.0
    solar_air_share: float = 0.4
    solar_scale: float = 0.5
    internal_day: float = 1500.0
    internal_night: float = 800.0
    sensor_tau_min: float = 0.0       # thermostat sensor lag; DESIGN: calls on T_air itself
    # East thermostat reads above its zone air while the compressor runs (supply air reaching the
    # sensor): the reading relaxes toward +sensor_kick_f with time constant sensor_kick_tau_min.
    sensor_kick_f: float = 0.0
    sensor_kick_tau_min: float = 5.0
    hysteresis: float = 0.5
    min_run_min: float = 5.0
    casing_frac: float = 0.03        # boiler casing/standby loss share of firing output, to BASEMENT air
    ua_pipe: float = 40.0            # BTU/h·°F near-boiler piping + manifolds, to BASEMENT air
    dhw_rate: float = 60000.0        # BTU/h burner output during a DHW draw
    ua_share: tuple = (("EAST", 0.28), ("WEST", 0.28), ("FIRST", 0.32), ("BASEMENT", 0.12))
    aperture: tuple = (("EAST", 5.0), ("WEST", 5.0), ("FIRST", 8.0), ("BASEMENT", 1.0), ("GARAGE", 2.0))  # BTU/h per W/m²
    # multiplies a floor's emitter UA and its floor-to-air conductance
    emitter_scale: tuple = (("EAST", 1.0), ("WEST", 1.0), ("FIRST", 1.0), ("BASEMENT", 1.0), ("GARAGE", 1.0))
    coupling_scale: float = 1.0
    stack_scale: float = 1.0
    ground_ua_basement: float = 60.0
    garage_ua: float = 160.0


ZONE_SHARE = {"EAST": 0.25, "WEST": 0.25, "FIRST": 0.32, "BASEMENT": 0.18}
INTERNAL_SHARE = {"EAST": 0.3, "WEST": 0.2, "FIRST": 0.4, "BASEMENT": 0.1, "GARAGE": 0.0}
COUPLING = (("EAST", "WEST", 80.0), ("EAST", "FIRST", 120.0), ("WEST", "FIRST", 120.0),
            ("FIRST", "BASEMENT", 150.0), ("GARAGE", "FIRST", 40.0), ("GARAGE", "BASEMENT", 40.0))
STACK = (("BASEMENT", "FIRST", 100.0), ("FIRST", "EAST", 60.0), ("FIRST", "WEST", 60.0))
GARAGE = {"c_air": 300.0, "c_mass": 3000.0, "h_am": 800.0}
GROUND_UA_GARAGE = 50.0


@dataclass
class Thermostat:
    zone: str
    mode: str = "heat"
    setpoint: Optional[int] = 68
    low: Optional[int] = None
    high: Optional[int] = None
    fan_mode: str = "on"
    sensed: float = 68.0
    calling: bool = False
    call_start: Optional[datetime] = None
    mode_changes: int = 0

    def target(self) -> Optional[float]:
        if self.mode == "heat":
            return self.setpoint
        if self.mode == "heat_cool":
            return self.low
        return None

    def update(self, now: datetime, p: Params) -> None:
        tgt = self.target()
        if tgt is None:
            self.calling, self.call_start = False, None
            return
        if not self.calling and self.sensed <= tgt - p.hysteresis:
            self.calling, self.call_start = True, now
        elif self.calling and self.sensed >= tgt + p.hysteresis and now - self.call_start >= timedelta(minutes=p.min_run_min):
            self.calling, self.call_start = False, None


@dataclass
class Lockout:
    start: datetime
    clears_after: Optional[datetime]  # a mode toggle after this time clears it; None: any toggle
    active: bool = False
    cleared: bool = False


@dataclass
class House:
    p: Params = field(default_factory=Params)
    air: dict = field(default_factory=dict)
    mass: dict = field(default_factory=dict)
    floor: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    lockout: Optional[Lockout] = None
    dhw: list = field(default_factory=list)  # [(start, end)] burner firings with no zone call

    def __post_init__(self):
        self.tstats = {z: Thermostat(z) for z in ZONES}
        self.tstats["GARAGE"].setpoint = 45
        for z in ZONES:
            self.air[z] = 68.0 if z != "GARAGE" else 55.0
            self.mass[z] = self.air[z]
            self.floor[z] = self.air[z] + 3
        self.compressor = False
        self.kick = 0.0
        self.comp_ready_at: Optional[datetime] = None
        self.comp_off_at: Optional[datetime] = None
        self.call_since: Optional[datetime] = None
        self.erv_mode = "Medium"
        self.burner = False
        self.hp_kw = 0.0
        self.fan_running = True
        self.basement_incidental = 0.0
        self.dhw_active = False
        # replay hooks (sim/calibrate.py): observed actuators override the emulated ones
        self.forced_compressor: Optional[bool] = None
        self.forced_calls: dict = {}
        self.erv_cfm: Optional[float] = None

    # --- device interface -------------------------------------------------
    def set_mode(self, zone: str, mode: str, now: datetime) -> None:
        """An East off -> heat toggle clears a resettable lockout (or one past its repair time)."""
        t = self.tstats[zone]
        if mode != t.mode:
            t.mode_changes += 1
            lk = self.lockout
            if zone == "EAST" and t.mode == "off" and lk and lk.active and (
                    lk.clears_after is None or now >= lk.clears_after):
                lk.active, lk.cleared = False, True
        t.mode = mode

    # --- physics ------------------------------------------------------------
    def _c(self, z, kind):
        if z == "GARAGE":
            return GARAGE["c_air"] if kind == "air" else GARAGE["c_mass"]
        base = self.p.c_air_house if kind == "air" else self.p.c_mass_house
        return base * ZONE_SHARE[z]

    def _floor_params(self, z):
        p = self.p
        k = dict(p.emitter_scale).get(z, 1.0)
        if z in ("BASEMENT", "GARAGE"):
            return p.slab_c_floor * p.floor_lag_scale, p.slab_ua_emitter * k, p.slab_h_floor_air * k, p.water_slab
        return p.wood_c_floor * p.floor_lag_scale, p.wood_ua_emitter * k, p.wood_h_floor_air * k, p.water_wood

    def step(self, now: datetime, dt_s: float, t_out: float, wind: float, ghi: float) -> None:
        p = self.p
        h = dt_s / 3600.0
        k_tgt = p.sensor_kick_f if self.compressor else 0.0
        self.kick += (k_tgt - self.kick) * min(1.0, dt_s / (max(p.sensor_kick_tau_min, 0.1) * 60.0))
        for z, t in self.tstats.items():
            a = 1.0 if p.sensor_tau_min <= 0 else min(1.0, dt_s / (p.sensor_tau_min * 60.0))
            seen = self.air[z] + (self.kick if z == "EAST" else 0.0)
            t.sensed += (seen - t.sensed) * a
            t.update(now, p)
            if z in self.forced_calls:
                v = self.forced_calls[z]
                if v != t.calling:
                    t.calling, t.call_start = v, (now if v else None)
        if self.lockout and not self.lockout.active and not self.lockout.cleared and now >= self.lockout.start:
            self.lockout.active = True

        east = self.tstats["EAST"]
        if east.calling:
            self.call_since = self.call_since or now
            ready = (self.comp_off_at is None or (now - self.comp_off_at).total_seconds() >= p.hp_anti_short_cycle_s)
            delayed = (now - self.call_since).total_seconds() >= p.hp_start_delay_s
            locked = self.lockout is not None and self.lockout.active
            if not self.compressor and ready and delayed and not locked:
                self.compressor = True
            if self.compressor and locked:
                self.compressor = False
                self.comp_off_at = now
        else:
            self.call_since = None
            if self.compressor:
                self.compressor = False
                self.comp_off_at = now
        if self.forced_compressor is not None:
            self.compressor = self.forced_compressor
        self.fan_running = self.compressor or (east.mode != "off" and east.fan_mode == "on")
        self.hp_kw = 2.9 if self.compressor else (0.224 if self.fan_running else 0.0)

        valves = {f: self.tstats[tz].calling for tz, floors in VALVES.items() for f in floors}
        self.dhw_active = any(a <= now < b for a, b in self.dhw)

        q_air = {z: 0.0 for z in ZONES}
        q_mass = {z: 0.0 for z in ZONES}
        q_floor = {z: 0.0 for z in ZONES}
        day = 7 <= now.hour < 23
        k_inf = p.ua_house * p.inf_share / p.ref_wind
        for z, share in p.ua_share:
            ua = p.ua_house * (1 - p.inf_share) * share + k_inf * wind * share
            q_air[z] -= ua * (self.air[z] - t_out)
        q_air["GARAGE"] -= p.garage_ua * (self.air["GARAGE"] - t_out)
        for z, ua in (("BASEMENT", p.ground_ua_basement), ("GARAGE", GROUND_UA_GARAGE)):
            q_mass[z] -= ua * (self.mass[z] - p.ground_t)
            q_floor[z] -= 0.5 * ua * (self.floor[z] - p.ground_t)
        cfm = ERV_CFM.get(self.erv_mode, 0) if self.erv_cfm is None else self.erv_cfm
        for z in ("EAST", "WEST", "FIRST"):
            q_air[z] -= 1.08 * cfm * (self.air[z] - t_out) * (1 - p.erv_eff) / 3
        for z, ap in p.aperture:
            sol = ghi * ap * p.solar_scale
            q_air[z] += sol * p.solar_air_share
            q_mass[z] += sol * (1 - p.solar_air_share)
            q_air[z] += (p.internal_day if day else p.internal_night) * INTERNAL_SHARE[z]
        if self.compressor:
            for z, s in p.hp_split:
                q_air[z] += p.hp_capacity * s
        if self.fan_running:
            for z, s in p.hp_split:
                q_air[z] += p.fan_heat * s
        pairs = [(a, b, k * p.coupling_scale) for a, b, k in COUPLING]
        if self.fan_running:
            pairs += [("EAST", "WEST", p.fan_mix), ("EAST", "FIRST", p.fan_mix), ("WEST", "FIRST", p.fan_mix)]
        for a, b, k in pairs:
            q = k * (self.air[a] - self.air[b])
            q_air[a] -= q
            q_air[b] += q
        for lo, hi, k in STACK:
            if self.air[lo] > self.air[hi]:
                q = k * p.stack_scale * (self.air[lo] - self.air[hi])
                q_air[lo] -= q
                q_air[hi] += q
        loop_out = 0.0
        circulating = [z for z, v in valves.items() if v]
        for z in circulating:
            c_f, ua_e, h_fa, tw = self._floor_params(z)
            loop_out += ua_e * (tw - self.floor[z])
        pipe = 0.0
        if circulating:
            tw_supply = max(self._floor_params(z)[3] for z in circulating)
            pipe = p.ua_pipe * (tw_supply - self.air["BASEMENT"])
        firing_out = loop_out + pipe + (p.dhw_rate if self.dhw_active else 0.0)
        self.burner = firing_out > 0
        self.basement_incidental = p.casing_frac * firing_out + pipe
        q_air["BASEMENT"] += self.basement_incidental
        for z in ZONES:
            h_am = GARAGE["h_am"] if z == "GARAGE" else p.h_air_mass_house * ZONE_SHARE[z]
            q = h_am * (self.air[z] - self.mass[z])
            q_air[z] -= q
            q_mass[z] += q
            if z in valves or z == "GARAGE":
                c_f, ua_e, h_fa, tw = self._floor_params(z)
                if valves.get(z):
                    q_floor[z] += ua_e * (tw - self.floor[z])
                q = h_fa * (self.floor[z] - self.air[z])
                q_floor[z] -= q
                q_air[z] += q
        for z in ZONES:
            self.air[z] += q_air[z] * h / self._c(z, "air")
            self.mass[z] += q_mass[z] * h / self._c(z, "mass")
            if z in valves:
                self.floor[z] += q_floor[z] * h / self._floor_params(z)[0]

    def heat_out(self) -> float:
        return self.p.hp_capacity if self.compressor else 0.0


def round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))
