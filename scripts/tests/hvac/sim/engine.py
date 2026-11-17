"""Run the unmodified HvacApp against the fake Home Assistant and the house, minute by minute."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.hvac.app import HvacApp

from . import fake_ha
from .house import HEATED, THERMOSTATS, ZONES, House, Lockout, round_half_up
from .scenarios import Scenario, Weather

CO2_OUT = 420.0
HOUSE_FT3 = 30000.0
INFIL_CFM = 75.0
PERSON_CO2_CFM = 0.0106
ERV_ENTITY = "select.attic_erv_mode"
AUTO_ENTITY = "automation.erv_control"
ENABLED = "input_boolean.hvac_lab_enabled"


LOGGER = logging.getLogger("sim.hvac")


def make_app(world, client, out_dir: str, sc: Scenario) -> HvacApp:
    """What AppManager does at startup, with the virtual clock and the fake client swapped in."""
    options = {"hvac": {"dry_run": sc.dry_run, "lockout_auto_reset": sc.lockout_auto_reset}}
    app = HvacApp(LOGGER, options, {"name": "hvac", "enabled": True}, client=client,
                  clock=lambda: world.now, state_dir=out_dir)
    app.load()
    return app


@dataclass
class Result:
    scenario: Scenario
    rows: list
    commands: list
    journal: list
    logs: list
    restart_ticks: list = field(default_factory=list)  # (t, n_commands from the first post-restart tick)
    final_states: dict = field(default_factory=dict)
    unavailable: dict = field(default_factory=dict)


def _erv_automation_mode(now: datetime, co2: float) -> str:
    if co2 > 1200:
        return "Maximum"
    if now.weekday() < 5 and 15 <= now.hour < 20:
        return "Minimum"
    return "Medium"


def _baseline_sp(t: datetime) -> int:
    return 68 if (t.hour, t.minute) >= (5, 30) and t.hour < 21 else 64


def warm_up(house: House, weather: Weather, start: datetime, days: int = 3) -> None:
    t = start - timedelta(days=days)
    while t < start:
        for z in HEATED:
            house.tstats[z].setpoint = _baseline_sp(t)
        to, wind, ghi = weather.at(t + timedelta(days=days))
        house.step(t, 30, to, wind, ghi)
        t += timedelta(seconds=30)


def simulate(sc: Scenario, out_dir: str, with_restarts: bool = True) -> Result:
    os.makedirs(out_dir, exist_ok=True)
    for name in ("mem-dry.json", "mem-live.json", "journal.jsonl"):
        if os.path.exists(p := os.path.join(out_dir, name)):
            os.remove(p)
    weather = Weather(sc.temp_offset)
    house = House(p=sc.params)
    house.dhw = sc.dhw_draws()
    warm_up(house, weather, sc.start)
    if sc.faults.lockout:
        house.lockout = Lockout(*sc.faults.lockout)
    world = fake_ha.World(now=sc.start, house=house, unavailable=sc.unavailable_windows(),
                          poll_phase=sc.poll_phases())
    co2 = 600.0
    world.set(ENABLED, "on")
    world.set(AUTO_ENTITY, "on")
    world.set(ERV_ENTITY, "Medium")
    house.erv_mode = "Medium"

    def sensors(t: datetime) -> None:
        world.set("sensor.weather_station_temperature", round(weather.at(t)[0], 1))
        away = any(a <= t < b for a, b in sc.away)
        for p in ("person.kodey_converse", "person.maddy_converse"):
            world.set(p, "not_home" if away else "home")
        world.set("binary_sensor.heat_pump_compressor", "on" if house.compressor else "off")
        world.set("binary_sensor.hvac_boiler_firing", "on" if house.burner else "off")
        world.set("sensor.dining_room_carbon_dioxide", round(co2))

    sensors(sc.start)
    world.poll_thermostats(force=True)
    client = fake_ha.FakeHa(world)
    capture = fake_ha.LogCapture(world)
    LOGGER.addHandler(capture)
    LOGGER.setLevel(logging.DEBUG)
    LOGGER.propagate = False
    loop = asyncio.new_event_loop()
    app = make_app(world, client, out_dir, sc)

    kills = dict(sc.faults.kill)
    overrides = {t: (z, sp) for t, z, sp in sc.faults.overrides}
    spikes = dict(sc.faults.co2_spikes)
    restarts = set(sc.faults.restarts) if with_restarts else set()
    restart_ticks = []
    rows = []
    t = sc.start
    while t < sc.end:
        if t != sc.start:
            for half in (t - timedelta(seconds=30), t):
                to, wind, ghi = weather.at(half)
                house.step(half, 30, to, wind, ghi)
        world.now = t
        to, wind, ghi = weather.at(t)
        if t in kills:
            world.set(ENABLED, kills[t])
        if t in overrides:
            z, sp = overrides[t]
            house.tstats[z].setpoint = sp
            house.set_mode(z, "heat", t)
        if t in spikes:
            co2 = spikes[t]
        n_home = 0 if any(a <= t < b for a, b in sc.away) else 2
        q = INFIL_CFM + {"Off": 0, "Minimum": 40, "Medium": 65, "Maximum": 130}.get(house.erv_mode, 65)
        co2 += (n_home * PERSON_CO2_CFM * 1e6 - (co2 - CO2_OUT) * q) / HOUSE_FT3
        if world.states[AUTO_ENTITY]["state"] == "on":
            mode = _erv_automation_mode(t, co2)
            if mode != house.erv_mode:
                house.erv_mode = mode
                world.set(ERV_ENTITY, mode)
        sensors(t)
        world.poll_thermostats()
        loop.run_until_complete(app.tick())
        if t in restarts:
            n_before = len(world.commands)
            app = make_app(world, client, out_dir, sc)
            loop.run_until_complete(app.tick())
            restart_ticks.append((t, len(world.commands) - n_before))
        rows.append(_row(t, world, house, to, wind, ghi, co2))
        t += timedelta(minutes=1)

    loop.close()
    LOGGER.removeHandler(capture)
    journal = []
    if os.path.exists(app.log_path):
        with open(app.log_path) as f:
            journal = [json.loads(line) for line in f]
    return Result(sc, rows, world.commands, journal, world.logs, restart_ticks, world.states, world.unavailable)


def _row(t, world, house, to, wind, ghi, co2) -> dict:
    r = {"t": t, "t_out": round(to, 2), "wind": round(wind, 1), "ghi": round(ghi, 1)}
    for z in ZONES:
        ts = house.tstats[z]
        ha = world.states.get(THERMOSTATS[z], {})
        avail = ha.get("state") not in (None, "unavailable")
        r[f"{z}_air"] = round(house.air[z], 3)
        r[f"{z}_floor"] = round(house.floor[z], 2)
        r[f"{z}_seen"] = ha.get("attributes", {}).get("current_temperature") if avail else None
        r[f"{z}_avail"] = avail
        r[f"{z}_mode"] = ts.mode
        r[f"{z}_sp"] = ts.setpoint if ts.mode == "heat" else (ts.low if ts.mode == "heat_cool" else None)
        r[f"{z}_call"] = ts.calling
    r["east_fan"] = house.tstats["EAST"].fan_mode
    r["compressor"] = house.compressor
    r["hp_kw"] = house.hp_kw
    r["burner"] = house.burner
    r["dhw"] = house.dhw_active
    r["basement_incidental_btuh"] = round(house.basement_incidental)
    r["erv_mode"] = house.erv_mode
    r["erv_auto"] = world.states[AUTO_ENTITY]["state"] == "on"
    r["co2"] = round(co2)
    r["enabled"] = world.states[ENABLED]["state"] == "on"
    st = world.states.get("sensor.hvac_lab_status", {})
    r["status"] = st.get("state")
    r["lockout_active"] = bool(house.lockout and house.lockout.active)
    return r
