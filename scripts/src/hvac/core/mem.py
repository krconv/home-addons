"""Mem: the app's only state. JSON-plain so a restart reloads it and converges."""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field, fields
from typing import Optional

LOCKOUT_STATES = ("NORMAL", "SUSPECT", "RESETTING", "FALLBACK")
BAND_STATES = ("UP", "DOWN")
CO2_STATES = ("IDLE", "OFF", "MEDIUM", "DONE", "ABORTED")


@dataclass
class Mem:
    seg_key: Optional[str] = None
    last_cmd: dict = field(default_factory=dict)     # key -> {"value": json, "t": iso}
    pending: dict = field(default_factory=dict)      # key -> {"value", "sent", "deadline", "before", "retries"}
    cmd_times: dict = field(default_factory=dict)    # key -> [iso, ...] within the last hour
    overrides: dict = field(default_factory=dict)    # zone -> segment key it was seen in
    floor_latched: list = field(default_factory=list)
    lockout: dict = field(default_factory=lambda: {"state": "NORMAL", "since": None, "reset_night": None})
    band: dict = field(default_factory=lambda: {"state": "UP"})
    co2_decay: dict = field(default_factory=lambda: {"state": "IDLE", "since": None, "away_since": None})
    co2_forced: bool = False
    grades: dict = field(default_factory=dict)       # night id -> {"void": bool, "reasons": [str]}
    coast_abort: dict = field(default_factory=dict)  # coast segment key -> ran a hold instead (bool)
    killed: bool = False
    deferred_restore: list = field(default_factory=list)
    events: list = field(default_factory=list)       # last N events (json)

    def copy(self) -> "Mem":
        return copy.deepcopy(self)

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "Mem":
        names = {f.name for f in fields(Mem)}
        return Mem(**{k: v for k, v in d.items() if k in names})
