"""Weather, faults and parameter sets for each scenario."""
from __future__ import annotations

import itertools
import json
import os
import random
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Optional

from .calibrate import calibrated_params
from .house import Params, THERMOSTATS

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "weather_2026-11-16_23.json")
START = datetime(2026, 11, 16, 12, 0)
END = datetime(2026, 11, 23, 12, 0)
# Default house: fitted to real history by sim/calibrate.py (data/calibration.json).
CALIBRATED = calibrated_params()
# The hand-set priors from DESIGN.md, kept as a named variant.
PRIORS = Params()


class Weather:
    def __init__(self, offset_f: float = 0.0, path: str = FIXTURE):
        h = json.load(open(path))["hourly"]
        self.t0 = datetime.fromisoformat(h["time"][0])
        self.temp = [v + offset_f for v in h["temperature_2m"]]
        self.wind = h["wind_speed_10m"]
        self.ghi = h["shortwave_radiation"]

    def at(self, t: datetime) -> tuple[float, float, float]:
        x = (t - self.t0).total_seconds() / 3600.0
        x = min(max(x, 0.0), len(self.temp) - 1.0001)
        i = int(x)
        f = x - i
        lerp = lambda s: s[i] + (s[i + 1] - s[i]) * f
        return lerp(self.temp), lerp(self.wind), max(0.0, lerp(self.ghi))


@dataclass
class Faults:
    lockout: Optional[tuple] = None          # (start, clears_after toggle time or None)
    overrides: list = field(default_factory=list)   # (t, zone, setpoint)
    restarts: list = field(default_factory=list)    # t
    kill: list = field(default_factory=list)        # (t, "on"/"off")
    co2_spikes: list = field(default_factory=list)  # (t, ppm)
    random_unavailable: bool = False


@dataclass
class Scenario:
    name: str
    params: Params = field(default_factory=lambda: CALIBRATED)
    temp_offset: float = 0.0
    faults: Faults = field(default_factory=Faults)
    seed: int = 1
    start: datetime = START
    end: datetime = END
    away: list = field(default_factory=lambda: [(datetime(2026, 11, 18, 10, 0), datetime(2026, 11, 18, 13, 30))])
    plots: bool = True
    fatal: bool = False
    lockout_auto_reset: bool = False
    dry_run: bool = False
    label: str = ""

    def unavailable_windows(self) -> dict:
        """One 5-minute window per thermostat per day; faults add random extra windows."""
        rng = random.Random(self.seed)
        out = {}
        for e in THERMOSTATS.values():
            wins = []
            d = self.start.replace(hour=0, minute=0)
            while d < self.end:
                s = d + timedelta(minutes=rng.randrange(0, 24 * 60))
                wins.append((s, s + timedelta(minutes=5)))
                if self.faults.random_unavailable:
                    for _ in range(3):
                        s = d + timedelta(minutes=rng.randrange(0, 24 * 60))
                        wins.append((s, s + timedelta(minutes=rng.randrange(5, 21))))
                d += timedelta(days=1)
            out[e] = wins
        return out

    def poll_phases(self) -> dict:
        rng = random.Random(self.seed + 7)
        return {e: rng.randrange(0, 5) for e in THERMOSTATS.values()}

    def dhw_draws(self) -> list:
        rng = random.Random(self.seed + 13)
        out = []
        d = (self.start - timedelta(days=3)).replace(hour=0, minute=0)
        while d < self.end:
            for _ in range(rng.randint(2, 4)):
                hour = rng.choice([6, 7, 8, 18, 19, 20, 21])
                s = d + timedelta(hours=hour, minutes=rng.randrange(60))
                out.append((s, s + timedelta(minutes=rng.randint(5, 15))))
            d += timedelta(days=1)
        return out


D = datetime


def week_nominal() -> Scenario:
    return Scenario("week_nominal", fatal=True)


def week_cold() -> Scenario:
    return Scenario("week_cold", temp_offset=-25.0)


def week_priors() -> Scenario:
    """week_nominal on the pre-calibration priors, for comparison."""
    return Scenario("week_priors", params=PRIORS)


def faults() -> Scenario:
    return Scenario(
        "faults",
        fatal=True,
        seed=2,
        faults=Faults(
            lockout=(D(2026, 11, 17, 21, 40), D(2026, 11, 18, 5, 0)),
            overrides=[(D(2026, 11, 18, 22, 30), "WEST", 70)],
            restarts=[D(2026, 11, 19, 23, 10)],
            kill=[(D(2026, 11, 21, 2, 0), "off"), (D(2026, 11, 21, 8, 0), "on")],
            co2_spikes=[(D(2026, 11, 19, 22, 15), 1350.0)],
            random_unavailable=True,
        ),
    )


def faults_autoreset() -> Scenario:
    """faults with cfg.lockout_auto_reset on: one reset, then fallback (the lockout is not resettable)."""
    sc = faults()
    return replace(sc, name="faults_autoreset", lockout_auto_reset=True, plots=False)


C = CALIBRATED
SWEEP_AXES = {
    "ua_house": (round(C.ua_house * 0.75), C.ua_house, round(C.ua_house * 1.35)),
    "floor_lag_scale": (0.5, 1.0, 2.0),
    "hp_capacity": (round(C.hp_capacity * 0.85), C.hp_capacity, 30000.0),
    "ua_pipe": (round(C.ua_pipe * 0.4), C.ua_pipe, round(C.ua_pipe * 2)),
    "casing_frac": (0.01, C.casing_frac, 0.06),
}


def sweep() -> list[Scenario]:
    """Calibrated center plus each axis at its low and high, the adverse/easy corners and the old priors."""
    base = CALIBRATED
    out = [Scenario("sweep/center", plots=False, label="center (calibrated)")]
    for k, (lo, mid, hi) in SWEEP_AXES.items():
        for tag, v in (("low", lo), ("high", hi)):
            out.append(Scenario(f"sweep/{k}={v:g}", params=replace(base, **{k: v}), plots=False, label=f"{k} {tag} ({v:g})"))
    lo = {k: v[0] for k, v in SWEEP_AXES.items()}
    hi = {k: v[2] for k, v in SWEEP_AXES.items()}
    hard = replace(base, ua_house=hi["ua_house"], floor_lag_scale=2.0, hp_capacity=lo["hp_capacity"],
                   ua_pipe=lo["ua_pipe"], casing_frac=0.01)
    easy = replace(base, ua_house=lo["ua_house"], floor_lag_scale=0.5, hp_capacity=hi["hp_capacity"],
                   ua_pipe=hi["ua_pipe"], casing_frac=0.06)
    out.append(Scenario("sweep/hard_corner", params=hard, plots=False, label="UA high, lag x2, HP low, pipe/casing low"))
    out.append(Scenario("sweep/easy_corner", params=easy, plots=False, label="UA low, lag x0.5, HP high, pipe/casing high"))
    out.append(Scenario("sweep/priors", params=PRIORS, plots=False, label="old DESIGN.md priors"))
    return out


SCENARIOS = {"week_nominal": week_nominal, "week_cold": week_cold, "faults": faults,
             "faults_autoreset": faults_autoreset, "week_priors": week_priors}
