"""Run hvac_controller (and optionally the live hvac app beside it) against the house simulator.

The sim owns its physics, its room truth and its checks. From the controller it imports only the
app class, to construct it the way AppManager does; no limit, setpoint or rule is borrowed.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Optional

from src.hvac_controller.app import ControllerApp
from tests.hvac.sim import engine as hvac_engine
from tests.hvac.sim import fake_ha
from tests.hvac.sim.house import HEATED, House, Lockout
from tests.hvac.sim.scenarios import CALIBRATED, Scenario, Weather

FLOOR = 64.0
SETPOINT_RANGE = (64, 75)
STATUS = "sensor.hvac_controller_status"
ENABLED = "input_boolean.hvac_controller_enabled"
# The weather fixture covers 11-16..11-23; the controller runs after the lab's test weeks ended,
# so the same weekdays two weeks later replay it.
WEATHER_SHIFT = timedelta(days=14)
START = datetime(2026, 11, 30, 12, 0)

# room -> (zone, true room minus zone air, eye-level helper, sensor bias). Main bedroom and dining
# run cold of their thermostats as measured (CONTEXT "Sensor offsets"); biases are template error.
ROOMS = {
    "Main bedroom": ("EAST", -2.5, "sensor.main_bedroom_eye_level_temperature", +0.3),
    "South bedroom": ("EAST", -1.0, "sensor.south_bedroom_eye_level_temperature", -0.2),
    "North bedroom": ("WEST", 0.0, "sensor.north_bedroom_eye_level_temperature", +0.4),
    "Living room": ("FIRST", -0.5, "sensor.living_room_eye_level_temperature", -0.3),
    "Nook": ("FIRST", +0.3, "sensor.nook_eye_level_temperature", +0.2),
    "Dining room": ("FIRST", -2.5, None, 0.0),
    "Basement": ("BASEMENT", 0.0, None, 0.0),
}
AIRTHINGS_ROOM = "Main bedroom"
# The tariff (CONTEXT "Energy prices"): what the meter charges, whether or not HA's helpers show it.
TARIFF = {"Off-peak": 0.18188, "Mid-peak": 0.18887, "On-peak": 0.60857}
RATE_HELPERS = {"Off-peak": "input_number.unitil_off_peak_rate", "Mid-peak": "input_number.unitil_mid_peak_rate",
                "On-peak": "input_number.unitil_on_peak_rate"}
PROPANE_GAL, PROPANE_BTU_GAL, BOILER_EFF, BOILER_KW = 3.83, 91_500.0, 0.92, 0.3
CH_COMMISSIONED = 110.3
BOILER_CH = "number.boiler_heating_setpoint"
LOGGER = logging.getLogger("sim.hvac_controller")


@dataclass
class CtlScenario:
    name: str
    mode: str = "live"                 # "live": controller alone, sending; "shadow": hvac live + controller dry
    temp_offset: float = 0.0
    start: datetime = START
    hours: float = 48.0
    seed: int = 1
    forks: int = 0                     # fork_and_freeze samples
    freeze_h: float = 12.0
    params: object = field(default_factory=lambda: replace(CALIBRATED, dhw_pauses_ch=True))
    tariff: dict = field(default_factory=lambda: dict(TARIFF))
    rate_helpers: bool = True          # False: the three input_number rate helpers are missing
    holidays: tuple = ()               # dates the TOU band reads Off-peak all day
    lockout: Optional[tuple] = None    # (hours in, hours in to a service repair or None): compressor locked out
    boiler_reboot_h: Optional[float] = None   # hours in: power cycle, CH setpoint boots to 99.5


@dataclass
class Result:
    scenario: CtlScenario
    rows: list
    posts: list                        # (t, path, body) from the controller
    violations: list                   # dry-run posts the spy refused
    world_commands: list
    journal: list
    forks: list = field(default_factory=list)   # (t, rows of the frozen run)


class FakeHttp:
    """The controller's HTTP boundary over the fake world. With `dry_run_spy`, any POST other than
    the controller's own status fails the tick and is recorded, whether or not something catches it."""

    def __init__(self, world: fake_ha.World, dry_run_spy: bool):
        self.world, self.spy = world, dry_run_spy
        self.posts, self.violations = [], []

    async def get(self, path: str):
        assert path == "/states", path
        return [{**st, "entity_id": e, "attributes": dict(st["attributes"])} for e, st in self.world.states.items()]

    async def post(self, path: str, body: dict):
        self.posts.append((self.world.now, path, json.loads(json.dumps(body, default=str))))
        if self.spy and path != f"/states/{STATUS}":
            self.violations.append((self.world.now, path, body))
            raise AssertionError(f"dry run posted {path}")
        if path.startswith("/states/"):
            self.world.set(path[len("/states/"):], body["state"], **fake_ha._wire(body["attributes"]))
        elif path.startswith("/services/"):
            self.world.service(path[len("/services/"):], fake_ha._wire(body))
        else:
            raise ValueError(path)


def tou_band(t: datetime, holidays: tuple = ()) -> str:
    if t.weekday() >= 5 or t.date() in holidays:
        return "Off-peak"
    return "Mid-peak" if 6 <= t.hour < 15 else ("On-peak" if 15 <= t.hour < 20 else "Off-peak")


def true_rooms(house: House) -> dict:
    return {r: house.air[z] + off for r, (z, off, _, _) in ROOMS.items()}


def zone_min(rooms: dict) -> dict:
    out = {}
    for r, v in rooms.items():
        z = ROOMS[r][0]
        out[z] = min(out.get(z, v), v)
    return out


def simulate(sc: CtlScenario, out_dir: str) -> Result:
    os.makedirs(out_dir, exist_ok=True)
    end = sc.start + timedelta(hours=sc.hours)
    base = Scenario(sc.name, params=sc.params, temp_offset=sc.temp_offset, seed=sc.seed, start=sc.start,
                    end=end, away=[], plots=False, dry_run=False)
    w = Weather(sc.temp_offset)

    def weather(t):
        return w.at(t - WEATHER_SHIFT)

    house = House(p=sc.params)
    house.dhw = base.dhw_draws()
    _warm_up(house, weather, sc.start)
    if sc.lockout:
        house.lockout = Lockout(sc.start + timedelta(hours=sc.lockout[0]), clears_after=None)
    repair = sc.start + timedelta(hours=sc.lockout[1]) if sc.lockout and sc.lockout[1] is not None else None
    reboot = sc.start + timedelta(hours=sc.boiler_reboot_h) if sc.boiler_reboot_h is not None else None
    world = fake_ha.World(now=sc.start - timedelta(hours=6), house=house, unavailable=base.unavailable_windows(),
                          poll_phase=base.poll_phases())
    world.set("input_select.airthings_location", AIRTHINGS_ROOM)
    world.now = sc.start
    for e, v in ((hvac_engine.ENABLED, "on"), (ENABLED, "on"), (hvac_engine.AUTO_ENTITY, "on"),
                 (hvac_engine.ERV_ENTITY, "Medium")):
        world.set(e, v)

    def sensors(t):
        to = weather(t)[0]
        world.set("sensor.weather_station_temperature", round(to, 1))
        for p in ("person.kodey_converse", "person.maddy_converse"):
            world.set(p, "home")
        world.set("binary_sensor.heat_pump_compressor", "on" if house.compressor else "off")
        world.set("sensor.dining_room_carbon_dioxide", 700)
        world.set("sensor.unitil_time_of_use_band", tou_band(t, sc.holidays))
        world.set("sensor.home_power_monitor_heat_pump_power", round(house.hp_kw * 1000 + 5, 1))
        world.set("switch.boiler_power", "on" if house.boiler_power else "off")
        world.set(BOILER_CH, "unavailable" if t == reboot else house.ch_setpoint)
        if sc.rate_helpers:
            for band, entity in RATE_HELPERS.items():
                world.set(entity, sc.tariff[band])
        truth = true_rooms(house)
        for r, (_, _, entity, bias) in ROOMS.items():
            if entity:
                world.set(entity, round(truth[r] + bias, 2))
        world.set("sensor.dining_room_temperature", round(truth[AIRTHINGS_ROOM], 1))

    sensors(sc.start)
    world.poll_thermostats(force=True)
    loop = asyncio.new_event_loop()
    http = FakeHttp(world, dry_run_spy=sc.mode == "shadow")
    ctl = ControllerApp(LOGGER, {"hvac_controller": {"dry_run": sc.mode == "shadow"}}, {"name": "hvac_controller"},
                        http=http, clock=lambda: world.now, state_dir=out_dir)
    ctl.load()
    hvac = None
    if sc.mode == "shadow":
        hvac = hvac_engine.make_app(world, fake_ha.FakeHa(world), os.path.join(out_dir, "hvac"), base)
    fork_at = _fork_times(sc)
    rows, forks = [], []
    t = sc.start
    while t < end:
        if t != sc.start:
            for half in (t - timedelta(seconds=30), t):
                house.step(half, 30, *weather(half))
        world.now = t
        if t == reboot:
            house.reboot_boiler()
        if t == repair:
            house.lockout.active, house.lockout.cleared = False, True
        sensors(t)
        world.poll_thermostats()
        if hvac is not None:
            loop.run_until_complete(hvac.tick())
        try:
            loop.run_until_complete(ctl.tick())
        except AssertionError:
            pass  # recorded by the spy
        rows.append(_row(t, world, house, tou_band(t, sc.holidays)))
        if t in fork_at:
            forks.append((t, freeze(copy.deepcopy(house), weather, t, sc.freeze_h)))
        t += timedelta(minutes=1)
    loop.close()
    journal = []
    if os.path.exists(ctl.log_path):
        with open(ctl.log_path) as f:
            journal = [json.loads(line) for line in f]
    return Result(sc, rows, http.posts, http.violations, world.commands, journal, forks)


def freeze(house: House, weather, t0: datetime, hours: float) -> list:
    """The app is gone at t0: the house runs on the thermostats' standing settings alone."""
    rows = []
    t = t0
    while t < t0 + timedelta(hours=hours):
        for half in (t + timedelta(seconds=30), t + timedelta(minutes=1)):
            house.step(half, 30, *weather(half))
        t += timedelta(minutes=1)
        rows.append({"t": t, "rooms": true_rooms(house),
                     "tstats": {z: (house.tstats[z].mode, house.tstats[z].setpoint) for z in HEATED}})
    return rows


def capacity_min(sc: CtlScenario) -> dict:
    """True room minimum per zone with every heated thermostat calling at the top of the range:
    what the plant can do at best. A zone under the floor here is a capacity limit, not a control fault.
    A scenario with a lockout gets the radiant-only plant from the start."""
    w = Weather(sc.temp_offset)

    def weather(t):
        return w.at(t - WEATHER_SHIFT)

    house = House(p=sc.params)
    _warm_up(house, weather, sc.start)
    if sc.lockout:
        house.lockout = Lockout(sc.start, clears_after=None)
    for z in HEATED:
        house.tstats[z].mode, house.tstats[z].setpoint = "heat", SETPOINT_RANGE[1]
    rows = freeze(house, weather, sc.start, sc.hours)
    return zone_min({r: min(x["rooms"][r] for x in rows[60:]) for r in ROOMS})


def _warm_up(house: House, weather, start: datetime, days: int = 3) -> None:
    t = start - timedelta(days=days)
    while t < start:
        for z in HEATED:
            house.tstats[z].setpoint = hvac_engine._baseline_sp(t)
        house.step(t, 30, *weather(t))
        t += timedelta(seconds=30)


def _fork_times(sc: CtlScenario) -> set:
    if not sc.forks:
        return set()
    step = sc.hours * 60 / (sc.forks + 1)
    # Off the slot grid, so some forks land mid-slot.
    return {sc.start + timedelta(minutes=int(step * (i + 1)) + 7 * i) for i in range(sc.forks)}


def _row(t: datetime, world: fake_ha.World, house: House, band: str) -> dict:
    st = world.states.get(STATUS, {})
    return {"t": t, "rooms": true_rooms(house),
            "tstats": {z: (house.tstats[z].mode, house.tstats[z].setpoint) for z in HEATED},
            "status": st.get("state"), "attrs": st.get("attributes", {}), "band": band,
            "hp_kw": house.hp_kw, "ch_out": house.ch_out, "ch_setpoint": house.ch_setpoint,
            "compressor": house.compressor}


# --- checks: the sim's own reading of what must hold -------------------------------------

def room_min(rows: list, settle_min: int = 60) -> dict:
    """True room minimum per zone after the first `settle_min` minutes."""
    return zone_min({r: min(x["rooms"][r] for x in rows[settle_min:]) for r in ROOMS})


def bill(res: Result) -> dict:
    """The sim's own meter, in dollars: heat pump kWh and boiler propane plus its electricity, at
    the tariff in force each minute."""
    hp = propane = 0.0
    for x in res.rows:
        rate = res.scenario.tariff[x["band"]]
        hp += x["hp_kw"] / 60 * rate
        if x["ch_out"] > 0:
            propane += x["ch_out"] / 60 / (PROPANE_BTU_GAL * BOILER_EFF) * PROPANE_GAL + BOILER_KW / 60 * rate
    return {"hp": round(hp, 2), "propane": round(propane, 2), "total": round(hp + propane, 2)}


def controller_service_calls(res: Result) -> list:
    return [(t, p, b) for t, p, b in res.posts if p.startswith("/services/")]


def bounded_problems(res: Result) -> list:
    """Every controller write is heat mode or an integer setpoint in range, and each thermostat only
    ever gets two distinct setpoints from it."""
    problems, seen = [], {}
    for t, path, body in controller_service_calls(res):
        if path == "/services/climate/set_hvac_mode" and body.get("hvac_mode") != "heat":
            problems.append(f"{t} {body}")
        elif path == "/services/climate/set_temperature":
            sp = body.get("temperature")
            if not (isinstance(sp, int) and SETPOINT_RANGE[0] <= sp <= SETPOINT_RANGE[1]) or set(body) - {"entity_id", "temperature"}:
                problems.append(f"{t} {body}")
            seen.setdefault(body["entity_id"], set()).add(sp)
        elif path == "/services/number/set_value":
            if body != {"entity_id": BOILER_CH, "value": CH_COMMISSIONED}:
                problems.append(f"{t} {body}")
        elif path not in ("/services/climate/set_hvac_mode",):
            problems.append(f"{t} {path}")
    problems += [f"{e} got {sorted(v)}" for e, v in seen.items() if len(v) > 2]
    if "climate.garage" in seen:
        problems.append("garage written")
    return problems


def standing_problems(rows: list) -> list:
    """After the controller's first hour, every heated thermostat stands in heat at a bounded setpoint."""
    out = []
    for x in rows[60:]:
        for z, (mode, sp) in x["tstats"].items():
            if mode != "heat" or sp is None or not SETPOINT_RANGE[0] <= sp <= SETPOINT_RANGE[1]:
                out.append(f"{x['t']} {z} {mode} {sp}")
    return out

