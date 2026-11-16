"""HvacApp: the only Home Assistant boundary. Tick every 60 s: observe, decide, act, persist, report."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from .core.mem import Mem
from .core.plan import week_plan
from .core.policy import decide, status
from .core.types import (AUTO_KEY, ERV_KEY, FAN_KEY, ZONES, Command, Config, Observation,
                         ZoneCommand, ZoneObs)
from .ha import HaClient, RestClient

# The plan is a calendar of local times at the house, whatever the host's clock zone.
PLAN_TZ = ZoneInfo("America/New_York")
TICK_S = 60
STATE_DIR = "/data/hvac"

DEFAULT_ENTITIES = {
    "zones": {
        "EAST": "climate.east_wing",
        "WEST": "climate.west_wing",
        "FIRST": "climate.first_floor",
        "BASEMENT": "climate.basement",
        "GARAGE": "climate.garage",
    },
    "enabled": "input_boolean.hvac_lab_enabled",
    "compressor": "binary_sensor.heat_pump_compressor",
    "erv": "select.attic_erv_mode",
    "erv_automation": "automation.erv_control",
    "co2": "sensor.dining_room_carbon_dioxide",
    "outdoor": "sensor.weather_station_temperature",
    "persons": ["person.kodey_converse", "person.maddy_converse"],
    "status": "sensor.hvac_lab_status",
}
MISSING = ("unavailable", "unknown", None)


def _int(v) -> Optional[int]:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _state(full: Optional[dict]):
    return None if full is None else full.get("state")


def local_now() -> datetime:
    return datetime.now(PLAN_TZ).replace(tzinfo=None)


class HvacApp:
    def __init__(self, logger: logging.Logger, addon_config: dict, app_config: dict,
                 client: Optional[HaClient] = None, clock: Callable[[], datetime] = local_now,
                 state_dir: str = STATE_DIR):
        opts = addon_config.get("hvac") or {}
        self.logger = logger
        self.ent = DEFAULT_ENTITIES
        self.dry_run = bool(opts.get("dry_run", True))
        self.cfg = Config(lockout_auto_reset=bool(opts.get("lockout_auto_reset", False)), dry_run=self.dry_run)
        self.plan = week_plan()
        self.client = client if client is not None else RestClient.from_env()
        self.clock = clock
        self.state_dir = state_dir
        self.mem_path = os.path.join(state_dir, "mem.json")
        self.log_path = os.path.join(state_dir, "journal.jsonl")
        self.mem = Mem()
        self._task: Optional[asyncio.Task] = None

    async def initialize(self) -> None:
        self.load()
        self._task = asyncio.create_task(self._run(), name="hvac")
        self.logger.info(f"hvac up; dry_run={self.dry_run}; state={self.state_dir}")

    def load(self) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        self.mem = self._load()

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        due = loop.time()
        while True:
            try:
                await self.tick()
            except Exception:  # a failed tick never stops the loop or the other apps; the next tick reconciles
                self.logger.exception("hvac tick failed")
            due += TICK_S
            await asyncio.sleep(max(0.0, due - loop.time()))

    async def tick(self) -> None:
        now = self.clock()
        obs = await self.observe(now)
        cmds, mem, events = decide(now, self.plan, obs, self.mem, self.cfg)
        for c in cmds:
            await self.send(c)
        self.mem = mem
        self._save()
        self._journal(now, cmds, events)
        state, attrs = status(now, self.plan, mem)
        await self.client.set_state(self.ent["status"], state, attrs)

    async def observe(self, now: datetime) -> Observation:
        """One read of every entity. A transport error raises and skips the tick rather than
        reading as 'unavailable', which would trip the kill switch or the guards."""
        zone_ids = [self.ent["zones"][z] for z in ZONES]
        other = [self.ent[k] for k in ("enabled", "compressor", "erv_automation", "erv", "co2", "outdoor")]
        ids = zone_ids + other + list(self.ent["persons"])
        states = dict(zip(ids, await asyncio.gather(*(self.client.get_state(i) for i in ids))))

        def get(entity: str):
            return _state(states[entity])

        zones = {z: self._zone(states[self.ent["zones"][z]]) for z in ZONES}
        enabled_state = get(self.ent["enabled"])
        if enabled_state not in ("on", "off"):
            self.logger.warning(f"{self.ent['enabled']} is {enabled_state!r}; treating as disabled")
        comp = get(self.ent["compressor"])
        auto = get(self.ent["erv_automation"])
        erv = get(self.ent["erv"])
        return Observation(
            now=now,
            zones=zones,
            enabled=enabled_state == "on",
            compressor={"on": True, "off": False}.get(comp),
            erv_mode=None if erv in MISSING else erv,
            erv_automation={"on": True, "off": False}.get(auto),
            co2=_float(get(self.ent["co2"])),
            outdoor=_float(get(self.ent["outdoor"])),
            persons_home=tuple(
                None if (s := get(p)) in MISSING else s == "home" for p in self.ent["persons"]
            ),
        )

    @staticmethod
    def _zone(full: Optional[dict]) -> ZoneObs:
        if not full or full.get("state") in MISSING:
            return ZoneObs(available=False)
        at = full.get("attributes") or {}
        return ZoneObs(
            available=True,
            mode=full["state"],
            setpoint=_int(at.get("temperature")),
            low=_int(at.get("target_temp_low")),
            high=_int(at.get("target_temp_high")),
            temp=_float(at.get("current_temperature")),
            action=at.get("hvac_action"),
            fan=at.get("fan_mode"),
        )

    async def send(self, c: Command) -> None:
        call = self.service_call(c)
        self.logger.info(f"{'DRY ' if self.dry_run else ''}{call[0]} {call[1]}")
        if self.dry_run:
            return
        try:
            await self.client.call_service(call[0], call[1])
        except Exception as e:  # the reconciler resends once the confirmation window lapses
            self.logger.warning(f"service call failed: {call}: {e!r}")

    def service_call(self, c: Command) -> tuple[str, dict]:
        if c.key.startswith("zone:"):
            entity = self.ent["zones"][c.key[5:]]
            v: ZoneCommand = c.value  # type: ignore[assignment]
            if v.mode == "off":
                return "climate/set_hvac_mode", {"entity_id": entity, "hvac_mode": "off"}
            if v.mode == "heat_cool":
                return "climate/set_temperature", {"entity_id": entity, "hvac_mode": "heat_cool",
                                                   "target_temp_low": v.low, "target_temp_high": v.high}
            return "climate/set_temperature", {"entity_id": entity, "hvac_mode": v.mode, "temperature": v.setpoint}
        if c.key == FAN_KEY:
            return "climate/set_fan_mode", {"entity_id": self.ent["zones"]["EAST"], "fan_mode": c.value}
        if c.key == ERV_KEY:
            return "select/select_option", {"entity_id": self.ent["erv"], "option": c.value}
        if c.key == AUTO_KEY:
            return ("automation/turn_on" if c.value else "automation/turn_off"), {"entity_id": self.ent["erv_automation"]}
        raise ValueError(c.key)

    def _load(self) -> Mem:
        try:
            with open(self.mem_path) as f:
                return Mem.from_json(json.load(f))
        except FileNotFoundError:
            return Mem()
        except (ValueError, TypeError) as e:
            self.logger.warning(f"mem unreadable, starting fresh: {e!r}")
            return Mem()

    def _save(self) -> None:
        tmp = self.mem_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.mem.to_json(), f)
        os.replace(tmp, self.mem_path)

    def _journal(self, now: datetime, cmds: list[Command], events) -> None:
        if not cmds and not events:
            return
        with open(self.log_path, "a") as f:
            for c in cmds:
                f.write(json.dumps({"t": now.isoformat(timespec="seconds"), "kind": "command",
                                    "key": c.key, "value": c.value_json(), "dry_run": self.dry_run}) + "\n")
            for e in events:
                f.write(json.dumps(e.to_json(), default=str) + "\n")
