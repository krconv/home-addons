"""A fake AppDaemon + Home Assistant around the house simulator.

`install(world)` puts `appdaemon.plugins.hass.hassapi` into sys.modules so the unmodified app
imports it. The World owns the virtual clock, the scheduler, the HA state store, and the service
router into the simulated devices. Thermostat state reaches HA only on a 5-minute poll with a
per-device phase, and each thermostat has unavailable windows.
"""
from __future__ import annotations

import itertools
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from .house import THERMOSTATS, House, round_half_up

TZ = ZoneInfo("America/New_York")
POLL_MIN = 5


class ServiceError(Exception):
    pass


@dataclass
class Timer:
    handle: str
    owner: object
    next: datetime
    interval: timedelta
    cb: Callable
    kwargs: dict


@dataclass
class World:
    now: datetime
    house: House
    unavailable: dict = field(default_factory=dict)   # entity -> [(start, end)]
    poll_phase: dict = field(default_factory=dict)     # entity -> minute offset 0..4
    states: dict = field(default_factory=dict)         # entity -> {"state", "attributes", "last_updated"}
    commands: list = field(default_factory=list)       # (t, service, data, ok)
    logs: list = field(default_factory=list)
    timers: list = field(default_factory=list)
    _ids: itertools.count = field(default_factory=itertools.count)

    # --- HA state ------------------------------------------------------------
    def set(self, entity: str, state, **attributes) -> None:
        prev = self.states.get(entity)
        if prev and prev["state"] == state and prev["attributes"] == attributes:
            return
        self.states[entity] = {"state": state, "attributes": dict(attributes),
                               "last_updated": self.now.replace(tzinfo=TZ).isoformat()}

    def is_unavailable(self, entity: str, t: Optional[datetime] = None) -> bool:
        t = t or self.now
        return any(a <= t < b for a, b in self.unavailable.get(entity, ()))

    def poll_thermostats(self, force: bool = False) -> None:
        minute = int(self.now.timestamp() // 60)
        for zone, entity in THERMOSTATS.items():
            if self.is_unavailable(entity):
                self.set(entity, "unavailable")
                continue
            was_unavailable = self.states.get(entity, {}).get("state") == "unavailable"
            if not (force or was_unavailable or minute % POLL_MIN == self.poll_phase.get(entity, 0)):
                continue
            t = self.house.tstats[zone]
            attrs = {"current_temperature": round_half_up(t.sensed),
                     "hvac_action": "off" if t.mode == "off" else ("heating" if t.calling else "idle"),
                     "min_temp": 40, "max_temp": 90, "target_temp_step": 1}
            if t.mode == "heat_cool":
                attrs.update(target_temp_low=t.low, target_temp_high=t.high, temperature=None)
            else:
                attrs.update(temperature=t.setpoint if t.mode != "off" else None)
            if zone == "EAST":
                attrs["fan_mode"] = t.fan_mode
            self.set(entity, t.mode, **attrs)

    # --- services ------------------------------------------------------------
    def service(self, service: str, data: dict) -> None:
        entity = data.get("entity_id")
        ok = True
        try:
            if entity in THERMOSTATS.values() and self.is_unavailable(entity):
                raise ServiceError(f"{entity} unavailable")
            self._apply(service, entity, data)
        except ServiceError:
            ok = False
            raise
        finally:
            self.commands.append((self.now, service, dict(data), ok))

    def _apply(self, service: str, entity: str, data: dict) -> None:
        zone = next((z for z, e in THERMOSTATS.items() if e == entity), None)
        h = self.house
        if service == "climate/set_hvac_mode":
            h.set_mode(zone, data["hvac_mode"], self.now)
        elif service == "climate/set_temperature":
            if "hvac_mode" in data:
                h.set_mode(zone, data["hvac_mode"], self.now)
            t = h.tstats[zone]
            for k in ("temperature", "target_temp_low", "target_temp_high"):
                if k in data and data[k] is not None and float(data[k]) != int(data[k]):
                    raise ServiceError(f"non-integer setpoint {data}")
            if "temperature" in data:
                t.setpoint = int(data["temperature"])
            if "target_temp_low" in data:
                t.low, t.high = int(data["target_temp_low"]), int(data["target_temp_high"])
        elif service == "climate/set_fan_mode":
            h.tstats[zone].fan_mode = data["fan_mode"]
        elif service == "select/select_option":
            h.erv_mode = data["option"]
            self.set(entity, data["option"])
        elif service in ("automation/turn_on", "automation/turn_off"):
            self.set(entity, "on" if service.endswith("on") else "off")
        else:
            raise ServiceError(f"unknown service {service}")

    # --- scheduler -----------------------------------------------------------
    def add_timer(self, owner, first: datetime, interval: timedelta, cb, kwargs) -> str:
        handle = f"timer-{next(self._ids)}"
        self.timers.append(Timer(handle, owner, first, interval, cb, kwargs))
        return handle

    def cancel_owner(self, owner) -> None:
        self.timers = [t for t in self.timers if t.owner is not owner]

    def run_due(self) -> None:
        for t in sorted(list(self.timers), key=lambda t: t.next):
            if t in self.timers and t.next <= self.now:
                t.next += t.interval
                t.cb(**t.kwargs)


class Hass:
    """The slice of appdaemon.plugins.hass.hassapi.Hass that HvacLab uses."""

    def __init__(self, world: World, name: str = "hvac_lab", args: Optional[dict] = None):
        self._world = world
        self.name = name
        self.args = args or {}

    def run_every(self, callback, start=None, interval=0, *args, **kwargs) -> str:
        w = self._world
        iv = interval if isinstance(interval, timedelta) else timedelta(seconds=float(interval))
        if start in (None, "now"):
            first = w.now + iv
        elif start == "immediate":
            first = w.now
        elif isinstance(start, datetime):
            first = start
        else:
            raise NotImplementedError(f"start={start!r}")
        return w.add_timer(self, first, iv, callback, kwargs)

    def get_state(self, entity_id=None, attribute=None, default=None, namespace=None, copy=True, **kwargs):
        st = self._world.states.get(entity_id)
        if st is None:
            return default
        if attribute is None:
            return st["state"]
        if attribute == "all":
            return {**st, "attributes": dict(st["attributes"])}
        return st["attributes"].get(attribute, default)

    def call_service(self, service: str, namespace=None, timeout=None, callback=None, **kwargs):
        if "/" not in service:
            raise ValueError("service must be domain/service")
        self._world.service(service, kwargs)

    def set_state(self, entity_id: str, state=None, namespace=None, attributes=None, replace=False,
                  check_existence=True, **kwargs):
        self._world.set(entity_id, state, **(attributes or {}), **kwargs)
        return self._world.states[entity_id]

    def log(self, msg: str, *args, level="INFO", **kwargs) -> None:
        self._world.logs.append((self._world.now, level, msg))

    def get_now(self, aware: bool = True) -> datetime:
        return self._world.now.replace(tzinfo=TZ) if aware else self._world.now

    def datetime(self, aware: bool = False) -> datetime:
        return self.get_now(aware)


def install() -> None:
    """Make `import appdaemon.plugins.hass.hassapi as hass` resolve to this fake."""
    names = ["appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass", "appdaemon.plugins.hass.hassapi"]
    mods = []
    for n in names:
        m = types.ModuleType(n)
        m.__path__ = []  # mark as package
        sys.modules[n] = m
        mods.append(m)
    for parent, child, attr in zip(mods, mods[1:], ["plugins", "hass", "hassapi"]):
        setattr(parent, attr, child)
    mods[-1].Hass = Hass
