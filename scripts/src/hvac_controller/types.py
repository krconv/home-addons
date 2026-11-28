"""The house as the controller sees it. Plain data: no clock, no I/O, no entity ids."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal, Mapping, Optional, Union

FLOOR_F = 64.0

# A zone is a set of rooms that must stay comfortable; a circuit is one thing the app can switch,
# through exactly one thermostat. They differ: the heat pump warms three zones, and the West
# thermostat's valve feeds both wing floors.
Zone = Literal["EAST", "WEST", "FIRST", "BASEMENT"]
ZONES: tuple[Zone, ...] = ("EAST", "WEST", "FIRST", "BASEMENT")

Circuit = Literal["HP", "RAD_WEST", "RAD_FIRST", "RAD_BASEMENT"]
CIRCUITS: tuple[Circuit, ...] = ("HP", "RAD_WEST", "RAD_FIRST", "RAD_BASEMENT")
RADIANT: tuple[Circuit, ...] = ("RAD_WEST", "RAD_FIRST", "RAD_BASEMENT")

SERVES: Mapping[Circuit, tuple[Zone, ...]] = {
    "HP": ("EAST", "WEST", "FIRST"),
    "RAD_WEST": ("EAST", "WEST"),
    "RAD_FIRST": ("FIRST",),
    "RAD_BASEMENT": ("BASEMENT",),
}

TouBand = Literal["Off-peak", "Mid-peak", "On-peak"]


class Tier(str, Enum):
    ROOM = "room"              # eye-level templates and/or a settled Airthings
    THERMOSTAT = "thermostat"  # the thermostat's own reading, offset-corrected


@dataclass(frozen=True)
class Reading:
    """A zone's eye-level temperature. `value` steers comfort; `coldest` is the coldest credible
    room and is what the floor guard watches."""
    value: float
    coldest: float
    tier: Tier
    rooms: tuple[str, ...]


@dataclass(frozen=True)
class Blind:
    """No trustworthy temperature. The only handling is to leave the zone to its thermostat's hold."""
    reason: str


ZoneTemp = Union[Reading, Blind]


@dataclass(frozen=True)
class TstatObs:
    available: bool
    mode: Optional[str] = None
    setpoint: Optional[int] = None
    reading: Optional[float] = None
    action: Optional[str] = None


@dataclass(frozen=True)
class HeatPumpObs:
    """Fresh compressor evidence; None where the reading is missing or stale."""
    compressor: Optional[bool]
    power_w: Optional[float]


@dataclass(frozen=True)
class BoilerObs:
    power: Optional[bool]     # switch.boiler_power, observed only; None when unavailable
    ch_f: Optional[float]     # CH setpoint readback, None unless fresh


@dataclass(frozen=True)
class Prices:
    """Electric rates by band (a band is absent when its helper is missing) and propane $/gal."""
    kwh: Mapping[TouBand, float]
    propane_gal: float
    propane_default: bool     # the propane helper is missing: propane_gal is the configured default


@dataclass(frozen=True)
class Snapshot:
    """Everything one decision needs, already validated by sense.read."""
    now: datetime
    enabled: bool
    temps: Mapping[Zone, ZoneTemp]
    tstats: Mapping[Circuit, TstatObs]
    tou: Optional[TouBand]
    hp: HeatPumpObs
    boiler: BoilerObs
    prices: Prices
    outdoor_f: Optional[float] = None


@dataclass(frozen=True)
class Band:
    lo: float
    hi: float

    def __post_init__(self) -> None:
        if self.lo < FLOOR_F + 1.0:
            raise ValueError(f"band lo {self.lo} is under {FLOOR_F + 1.0}")
        if self.hi < self.lo + 1.0:
            raise ValueError(f"band {self.lo}..{self.hi} is narrower than 1 deg F")


@dataclass(frozen=True)
class Policy:
    """The eight numbers of the control law. The defaults are RULES mode."""
    preheat_h: float = 1.0           # heat-pump zones ride at band hi this long before On-peak
    recover_h: float = 1.5           # the heat pump starts toward a band step-up this early
    hp_swing_f: float = 1.0          # once on, the heat pump runs to target + swing
    backup_drop_f: float = 2.0       # wing and first-floor radiant hold this far under the heat pump's target
    peak_primary: Literal["COST", "HP", "RADIANT"] = "COST"   # COST: the cheaper delivered MMBTU, with hysteresis
    radiant_lead_h: float = 3.0      # when radiant is primary, it starts toward a step-up this early
    basement_recover_h: float = 2.0
    basement_stop_h: float = 0.0


class Rule(str, Enum):
    """Stable names of the rule that won a circuit's decision; the journal and status carry them."""
    CALL = "policy.call"
    PREHEAT = "policy.preheat"
    MIN_RUN = "policy.min_run"
    SWING = "policy.swing"
    SATISFIED = "policy.satisfied"
    PEAK_YIELDS = "policy.peak_yields"
    BACKUP = "policy.radiant_backup"
    PRIMARY = "policy.radiant_primary"
    ONE_AT_A_TIME = "policy.one_radiant_at_a_time"
    UNUSABLE = "policy.unusable"
    FLOOR = "guard.floor"
    HI_STOP = "guard.hi_stop"
    BLIND = "guard.blind"
    KILL = "kill_switch"


@dataclass(frozen=True)
class Want:
    on: bool
    rule: Rule
    why: str


@dataclass(frozen=True)
class Switch:
    """The only heating command. The app maps it to heat mode plus the circuit's hold or call
    setpoint, so whenever the app stops each thermostat is in one of two known, bounded states."""
    source: Circuit
    on: bool


@dataclass
class Mem:
    """The controller's only state, JSON-plain. A lost file starts fresh: policy fields re-derive
    within a slot, a lockout is re-detected in its debounce, and an override is forgotten until our
    next confirmed setpoint (a live-write ledger is required before live)."""
    latch: dict = field(default_factory=dict)      # circuit -> {"on", "rule", "why", "slot", "slots_on"}
    floor: list = field(default_factory=list)      # zones held by the floor guard
    intent: dict = field(default_factory=dict)     # circuit -> {"on", "rule"}: last journaled intent
    sent: dict = field(default_factory=dict)       # circuit -> [iso times of sends in the last hour]
    killed: bool = False
    lockout: dict = field(default_factory=dict)    # {"state": suspect|locked, "since", "seen"}; empty is normal
    primary: dict = field(default_factory=lambda: {"source": "HP", "why": ""})  # last slot's primary source
    boiler_down: Optional[str] = None              # since when the boiler power switch isn't on
    ch_at: Optional[str] = None                    # last CH setpoint reassert (live) or intent (dry)
    confirmed: list = field(default_factory=list)  # circuits seen at our wanted setpoint since the last clear
    override: dict = field(default_factory=dict)   # circuit -> {"since", "mode", "setpoint"}: hands off
    foreign: dict = field(default_factory=dict)    # dry: circuit -> {"mode", "setpoint"} no known writer uses

    def to_json(self) -> dict:
        return dict(self.__dict__)

    @staticmethod
    def from_json(d: dict) -> "Mem":
        return Mem(**{k: v for k, v in d.items() if k in Mem.__dataclass_fields__})


@dataclass(frozen=True)
class Step:
    wants: Mapping[Circuit, Want]
    switches: tuple[Switch, ...]   # live: what to send; dry: the intents to journal
    mem: Mem
    status: tuple[str, Mapping[str, object]]
    ch: Optional[float] = None      # live: reassert the boiler CH setpoint; dry: the intent to journal
    events: tuple[dict, ...] = ()   # journal lines: lockout, override and foreign-setpoint transitions
