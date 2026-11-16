"""Plain data shared by the plan, the guards and the reconciler. No clock, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable, Literal, Mapping, Optional, Union

ZoneId = Literal["EAST", "WEST", "FIRST", "BASEMENT", "GARAGE"]
ZONES: tuple[ZoneId, ...] = ("EAST", "WEST", "FIRST", "BASEMENT", "GARAGE")
HEATED: tuple[ZoneId, ...] = ("EAST", "WEST", "FIRST", "BASEMENT")
RADIANT: tuple[ZoneId, ...] = ("WEST", "FIRST", "BASEMENT")
HP_BACKUP_ZONES: tuple[ZoneId, ...] = ("WEST", "FIRST")

Mode = Literal["heat", "off", "cool", "heat_cool"]
Fan = Literal["on", "auto"]
ErvMode = Literal["Off", "Minimum", "Medium", "Maximum"]
Flag = Literal["comfort_exempt", "allow_peak_hp", "owns_erv"]


@dataclass(frozen=True)
class ZoneCommand:
    mode: Mode
    setpoint: Optional[int]
    low: Optional[int] = None
    high: Optional[int] = None

    def __post_init__(self) -> None:
        for v in (self.setpoint, self.low, self.high):
            if v is not None and (not isinstance(v, int) or isinstance(v, bool)):
                raise TypeError(f"setpoints are whole degrees: {self}")
        if self.mode == "heat" and self.setpoint is None:
            raise ValueError("heat needs a setpoint")
        if self.mode == "heat_cool" and (self.low is None or self.high is None or self.high - self.low < 3):
            raise ValueError("heat_cool needs low/high at least 3 apart")

    @property
    def heat_target(self) -> Optional[int]:
        if self.mode == "heat":
            return self.setpoint
        if self.mode == "heat_cool":
            return self.low
        return None

    def to_json(self) -> dict:
        return {"mode": self.mode, "setpoint": self.setpoint, "low": self.low, "high": self.high}

    @staticmethod
    def from_json(d: dict) -> "ZoneCommand":
        return ZoneCommand(d["mode"], d.get("setpoint"), d.get("low"), d.get("high"))


def heat(sp: int) -> ZoneCommand:
    return ZoneCommand("heat", sp)


OFF = ZoneCommand("off", None)


@dataclass(frozen=True)
class Desired:
    zones: Mapping[ZoneId, ZoneCommand]
    east_fan: Fan
    erv: Optional[ErvMode]
    erv_automation: bool

    def with_zones(self, **zones: ZoneCommand) -> "Desired":
        return replace(self, zones={**self.zones, **zones})


@dataclass(frozen=True)
class DesiredPatch:
    """Declarative patch over the baseline. Unset fields keep the baseline value."""
    zones: Mapping[ZoneId, ZoneCommand] = field(default_factory=dict)
    east_fan: Optional[Fan] = None
    erv: Optional[ErvMode] = None

    def __call__(self, d: Desired) -> Desired:
        out = d.with_zones(**self.zones)
        if self.east_fan is not None:
            out = replace(out, east_fan=self.east_fan)
        if self.erv is not None:
            out = replace(out, erv=self.erv, erv_automation=False)
        return out


@dataclass(frozen=True)
class ZoneObs:
    available: bool
    mode: Optional[str] = None
    setpoint: Optional[int] = None
    low: Optional[int] = None
    high: Optional[int] = None
    temp: Optional[float] = None
    action: Optional[str] = None
    fan: Optional[str] = None

    def matches(self, cmd: ZoneCommand) -> bool:
        if self.mode != cmd.mode:
            return False
        if cmd.mode == "heat" or cmd.mode == "cool":
            return self.setpoint == cmd.setpoint
        if cmd.mode == "heat_cool":
            return self.low == cmd.low and self.high == cmd.high
        return True

    def as_command(self) -> Optional[ZoneCommand]:
        try:
            if self.mode == "off":
                return OFF
            if self.mode in ("heat", "cool") and self.setpoint is not None:
                return ZoneCommand(self.mode, int(self.setpoint))
            if self.mode == "heat_cool":
                return ZoneCommand("heat_cool", None, int(self.low), int(self.high))
        except (TypeError, ValueError):
            pass
        return None


@dataclass(frozen=True)
class Observation:
    now: datetime
    zones: Mapping[ZoneId, ZoneObs]
    enabled: bool
    compressor: Optional[bool]
    erv_mode: Optional[str]
    erv_automation: Optional[bool]
    co2: Optional[float]
    persons_home: tuple[Optional[bool], ...] = ()
    outdoor: Optional[float] = None


Predicate = Callable[[Observation], bool]
Patch = Union[Callable[[Desired], Desired], DesiredPatch]


@dataclass(frozen=True)
class Segment:
    start: datetime
    end: datetime
    test_id: str
    phase: str
    patch: Patch
    flags: frozenset = frozenset()
    goal: str = ""
    isolates: tuple[str, ...] = ()
    requires: Optional[Predicate] = None
    reactive: Optional[str] = None
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.test_id}@{self.start.isoformat(timespec='minutes')}"

    def contains(self, t: datetime) -> bool:
        return self.start <= t < self.end


@dataclass(frozen=True)
class Event:
    t: datetime
    kind: str
    detail: Mapping[str, object] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"t": self.t.isoformat(timespec="seconds"), "kind": self.kind, **self.detail}


# Actuator keys: one per independently commanded thing.
ZONE_KEYS: Mapping[ZoneId, str] = {z: f"zone:{z}" for z in ZONES}
FAN_KEY = "east_fan"
ERV_KEY = "erv"
AUTO_KEY = "erv_automation"


@dataclass(frozen=True)
class Command:
    key: str
    value: object  # ZoneCommand | str | bool

    def value_json(self) -> object:
        return self.value.to_json() if isinstance(self.value, ZoneCommand) else self.value


@dataclass(frozen=True)
class Config:
    floor: int = 64
    confirm_window_min: float = 7.0
    lockout_detect_min: float = 10.0
    lockout_auto_reset: bool = False
    lockout_reset_off_min: float = 2.0
    co2_force_max: float = 1200.0
    co2_release: float = 1000.0
    co2_test_abort: float = 1000.0
    peak_start_h: int = 15
    peak_end_h: int = 20
    peak_lead_min: int = 2
    peak_radiant_margin: int = 1
    floor_guard_trigger: int = 65
    floor_guard_setpoint: int = 65
    # Calibrated model: West's radiant alone cannot hold 64 on a cold night (real 11-14..16 data).
    hp_rescue_trigger: int = 64
    hp_rescue_east_sp: int = 68
    max_retries: int = 2
    # Comfort-hours setpoint of the radiant zones while the heat pump is primary at 68: radiant only
    # fires if a zone sags well below comfort.
    radiant_backup_sp: int = 66
    # The basement gets no heat pump air, so its slab radiant holds comfort itself.
    basement_backup_sp: int = 68
    # A coast segment that starts with outdoor below this runs a hold instead (test-schedule abort rule).
    coast_abort_outdoor_f: float = 15.0
    # Journal intended commands without calling services; reconcile against what was last intended.
    dry_run: bool = False
