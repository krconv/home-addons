"""A fake Home Assistant core API around the house simulator.

The World owns the virtual clock, the HA state store, and the service router into the simulated
devices; FakeHa is the HaClient that HvacApp talks to. Thermostat state reaches HA only on a
5-minute poll with a per-device phase, and each thermostat has unavailable windows.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from .house import THERMOSTATS, House, round_half_up

TZ = ZoneInfo("America/New_York")
POLL_MIN = 5


class ServiceError(Exception):
    pass


@dataclass
class World:
    now: datetime
    house: House
    unavailable: dict = field(default_factory=dict)   # entity -> [(start, end)]
    poll_phase: dict = field(default_factory=dict)     # entity -> minute offset 0..4
    states: dict = field(default_factory=dict)         # entity -> {"state", "attributes", "last_updated"}
    commands: list = field(default_factory=list)       # (t, service, data, ok)
    logs: list = field(default_factory=list)

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
                # The Amazon thermostats reject this with HTTP 400 (seen live 2026-11-16).
                raise ServiceError("Set the HVAC mode and the target temperature in separate actions")
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


def _wire(body: dict) -> dict:
    return json.loads(json.dumps(body, default=str))


class FakeHa:
    """The HaClient HvacApp uses, answered from the World's state store and service router.

    Mirrors the core REST API: a missing entity reads as None, a refused service call raises, and
    whatever the app sends goes through JSON the way RestClient sends it.
    """

    def __init__(self, world: World):
        self._world = world

    async def get_state(self, entity_id: str) -> Optional[dict]:
        st = self._world.states.get(entity_id)
        return None if st is None else {**st, "entity_id": entity_id, "attributes": dict(st["attributes"])}

    async def call_service(self, service: str, data: dict) -> None:
        if "/" not in service:
            raise ValueError("service must be domain/service")
        self._world.service(service, _wire(data))

    async def set_state(self, entity_id: str, state: str, attributes: dict) -> None:
        self._world.set(entity_id, state, **_wire(attributes))


class LogCapture(logging.Handler):
    """Records the app's log lines into world.logs as (virtual time, level, message)."""

    def __init__(self, world: World):
        super().__init__(logging.DEBUG)
        self._world = world

    def emit(self, record: logging.LogRecord) -> None:
        self._world.logs.append((self._world.now, record.levelname, record.getMessage()))
