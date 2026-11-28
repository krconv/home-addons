"""The house as data: entity ids, which sensors speak for each zone, the two setpoints of each
thermostat, the comfort schedule. The only module that names Home Assistant entities."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Mapping, Optional
from zoneinfo import ZoneInfo

from .types import CIRCUITS, FLOOR_F, ZONES, Circuit, Zone

# A thermostat holds within its 0.5 deg F hysteresis of a whole-degree setpoint.
HOLD_MARGIN_F = 1.0
MAX_CALL_F = 75


@dataclass(frozen=True)
class Thermostat:
    """What "off" and "on" mean on one thermostat, both in heat mode, whole degrees.

    hold_f is the backstop the thermostat keeps by itself if the app stops; it must keep the
    coldest credible room the thermostat speaks for above the floor, so it is set in the
    thermostat's own reading: hold_f - coldest_gap_f - HOLD_MARGIN_F >= FLOOR_F.
    call_f is a cap: a thermostat left calling heats to call_f and stops.
    """
    entity: str
    hold_f: int
    call_f: int
    coldest_gap_f: float   # thermostat reading minus its coldest room, worst case (night)
    offset_f: float        # thermostat reading minus the zone's eye-level mean, typical

    def setpoint(self, on: bool) -> int:
        return self.call_f if on else self.hold_f


@dataclass(frozen=True)
class Room:
    name: str                       # as in input_select.airthings_location
    eye_level: Optional[str] = None  # template helper, when the room has one


@dataclass(frozen=True)
class ZoneSensors:
    rooms: tuple[Room, ...]
    thermostat: Circuit             # whose own reading is the fallback


@dataclass(frozen=True)
class Comfort:
    day_start: time = time(5, 30)
    day_end: time = time(21, 0)
    day_lo: float = 68.0
    day_hi: float = 70.0
    night_lo: float = 65.0          # floor + 1: the app acts before the thermostat hold does
    peak_start: time = time(15, 0)  # weekday On-peak start; only "is it soon" is read from it


@dataclass(frozen=True)
class Limits:
    floor_lead_f: float = 1.0       # floor guard engages at FLOOR_F + this (coldest room)
    floor_release_f: float = 1.0    # ... and releases this much higher
    hi_stop_f: float = 1.0          # a circuit stops when a zone it serves reaches band hi + this
    margin_f: float = 0.3           # targets sit this far inside the band
    radiant_swing_f: float = 0.5
    min_run_slots: int = 2          # 30 minutes
    slot_min: int = 15              # one policy decision per circuit per slot
    room_max_age_min: float = 60.0
    airthings_settle_min: float = 90.0
    plausible_f: tuple[float, float] = (40.0, 95.0)
    confirm_min: float = 7.0        # live: don't resend a setpoint the thermostat hasn't polled yet
    max_sends_per_hour: int = 4


@dataclass(frozen=True)
class Site:
    thermostats: Mapping[Circuit, Thermostat]
    zones: Mapping[Zone, ZoneSensors]
    enabled: str
    status: str
    tou_band: str
    airthings_temp: str
    airthings_location: str
    outdoor: str
    tz: ZoneInfo = ZoneInfo("America/New_York")
    comfort: Comfort = Comfort()
    limits: Limits = field(default_factory=Limits)

    def validate(self) -> None:
        if set(self.thermostats) != set(CIRCUITS) or set(self.zones) != set(ZONES):
            raise ValueError("every circuit needs a thermostat and every zone sensors")
        for c, t in self.thermostats.items():
            if t.hold_f - t.coldest_gap_f - HOLD_MARGIN_F < FLOOR_F:
                raise ValueError(f"{c} hold {t.hold_f} leaves its coldest room under {FLOOR_F}")
            if not t.hold_f + 2 <= t.call_f <= MAX_CALL_F:
                raise ValueError(f"{c} call {t.call_f} must be in {t.hold_f + 2}..{MAX_CALL_F}")
        if min(self.comfort.day_lo, self.comfort.night_lo) < FLOOR_F + 1:
            raise ValueError("comfort lows must be at least floor + 1")

    def read_list(self) -> frozenset[str]:
        rooms = {r.eye_level for z in self.zones.values() for r in z.rooms if r.eye_level}
        return frozenset({t.entity for t in self.thermostats.values()} | rooms | {
            self.enabled, self.tou_band, self.airthings_temp, self.airthings_location, self.outdoor})


SITE = Site(
    # Gaps from CONTEXT "Sensor offsets": main bedroom ~3 under East at night, dining ~3 under First.
    thermostats={
        "HP": Thermostat("climate.east_wing", hold_f=68, call_f=74, coldest_gap_f=3.0, offset_f=1.8),
        "RAD_WEST": Thermostat("climate.west_wing", hold_f=66, call_f=72, coldest_gap_f=1.0, offset_f=0.0),
        "RAD_FIRST": Thermostat("climate.first_floor", hold_f=68, call_f=72, coldest_gap_f=3.0, offset_f=0.6),
        # No eye-level reference yet: a comfort-oriented hold, as today's baseline.
        "RAD_BASEMENT": Thermostat("climate.basement", hold_f=68, call_f=72, coldest_gap_f=1.0, offset_f=0.0),
    },
    zones={
        "EAST": ZoneSensors((Room("Main bedroom", "sensor.main_bedroom_eye_level_temperature"),
                             Room("South bedroom", "sensor.south_bedroom_eye_level_temperature")), "HP"),
        "WEST": ZoneSensors((Room("North bedroom", "sensor.north_bedroom_eye_level_temperature"),), "RAD_WEST"),
        "FIRST": ZoneSensors((Room("Living room", "sensor.living_room_eye_level_temperature"),
                              Room("Nook", "sensor.nook_eye_level_temperature"),
                              Room("Dining room"), Room("Mudroom")), "RAD_FIRST"),
        "BASEMENT": ZoneSensors((Room("Basement"),), "RAD_BASEMENT"),
    },
    enabled="input_boolean.hvac_controller_enabled",
    status="sensor.hvac_controller_status",
    tou_band="sensor.unitil_time_of_use_band",
    airthings_temp="sensor.dining_room_temperature",
    airthings_location="input_select.airthings_location",
    outdoor="sensor.weather_station_temperature",
)
