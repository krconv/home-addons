"""The test week as a table of Segments, plus the baseline they patch."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional, Sequence

from .types import (HEATED, OFF, RADIANT, Config, Desired, DesiredPatch, Observation,
                    Segment, heat)

COMFORT_START = time(5, 30)
COMFORT_END = time(21, 0)
COMFORT_SP = 68
SETBACK_SP = 64
GARAGE_SP = 45
# Nights without a recovery segment still have to be at comfort by 05:30.
RECOVERY_START = time(3, 30)


def in_comfort(t: datetime) -> bool:
    return COMFORT_START <= t.time() < COMFORT_END


def baseline(t: datetime, cfg: Config = Config()) -> Desired:
    """Comfort hours: the heat pump (East) is primary at 68 and radiant waits at the backup setpoint."""
    if in_comfort(t):
        zones = {z: heat(cfg.radiant_backup_sp if z in RADIANT else COMFORT_SP) for z in HEATED}
        zones["BASEMENT"] = heat(cfg.basement_backup_sp)
    elif RECOVERY_START <= t.time() < COMFORT_START:
        zones = {z: heat(COMFORT_SP) for z in HEATED}
    else:
        zones = {z: heat(SETBACK_SP) for z in HEATED}
    zones["GARAGE"] = heat(GARAGE_SP)
    return Desired(zones=zones, east_fan="on", erv=None, erv_automation=True)


def baseline_period(t: datetime) -> str:
    """Name of the baseline period containing t; its change is a segment boundary."""
    if in_comfort(t):
        return f"baseline:day:{t.date().isoformat()}"
    d = t.date() if t.time() >= COMFORT_END else t.date() - timedelta(days=1)
    return f"baseline:night:{d.isoformat()}"


def night_of(t: datetime) -> str:
    """Grading unit: nights run 21:00 -> 05:30 and are named by their start date; days by date."""
    if in_comfort(t):
        return f"day {t.date().isoformat()}"
    d = t.date() if t.time() >= COMFORT_END else t.date() - timedelta(days=1)
    return f"night {d.isoformat()}"


@dataclass(frozen=True)
class Plan:
    segments: tuple[Segment, ...]
    overlays: tuple[Segment, ...] = ()

    def __post_init__(self) -> None:
        validate(self.segments)
        for o in self.overlays:
            for s in self.segments:
                if "owns_erv" in s.flags and o.start < s.end and s.start < o.end:
                    raise ValueError(f"overlay {o.key} overlaps ERV owner {s.key}")

    def at(self, t: datetime) -> Optional[Segment]:
        for s in self.segments:
            if s.contains(t):
                return s
        return None

    def overlay_at(self, t: datetime) -> Optional[Segment]:
        for s in self.overlays:
            if s.contains(t):
                return s
        return None


def validate(segments: Sequence[Segment]) -> None:
    for a, b in zip(segments, segments[1:]):
        if b.start < a.end:
            raise ValueError(f"segments overlap or are unsorted: {a.key} / {b.key}")
    for s in segments:
        if s.end <= s.start:
            raise ValueError(f"empty segment {s.key}")
        d = s.patch(baseline(s.start))
        for z in HEATED:
            c = d.zones[z]
            tgt = c.heat_target
            if c.mode != "off" and (tgt is None or not isinstance(tgt, int) or tgt < SETBACK_SP):
                raise ValueError(f"{s.key}: {z} left on below the {SETBACK_SP} floor: {c}")


def _radiant(sp: int) -> dict:
    return {z: heat(sp) for z in RADIANT}


def both_away(obs: Observation) -> bool:
    return bool(obs.persons_home) and all(p is False for p in obs.persons_home)


D = datetime


def P(east=None, radiant: Optional[int] = None, fan=None, erv=None, **zones) -> DesiredPatch:
    z = dict(_radiant(radiant)) if radiant is not None else {}
    if east is not None:
        z["EAST"] = east
    z.update(zones)
    return DesiredPatch(zones=z, east_fan=fan, erv=erv)


def _seg(start, end, test_id, phase, patch, flags=(), goal="", isolates=(), note="", reactive=None):
    return Segment(start, end, test_id, phase, patch, frozenset(flags), goal, tuple(isolates),
                   reactive=reactive, note=note)


COAST = P(east=heat(64), radiant=64)
# The ERV energises the air handler fan whenever it runs (hard-wired), so a fan-off test owns the
# ERV and turns it off; Thursday's ERV night separates the ventilation share back out.
COAST_FAN_AUTO = P(east=heat(64), radiant=64, fan="auto", erv="Off")
RADIANT_RECOVERY = P(east=OFF, radiant=68)
# Holds run at 68 so the source under test actually runs (at 66 a 68 day left it idle for hours).
HP_HOLD = P(east=heat(68), radiant=64)
HOLD_SP = 68
# The ERV halves hold East at 70, not 68: East is still ~70 at 21:00 after a peak-handoff day, so
# at 68 the heat pump never ran in erv_off (calibrated model) and the halves were not comparable.
ERV_HOLD_SP = 70
BACKUP = Config().radiant_backup_sp
# What a coast becomes when outdoor is below cfg.coast_abort_outdoor_f at its start.
COLD_HOLD = HP_HOLD

# `note` records every change from the DESIGN.md table and why (sim evidence in out/*/report.md).
WEEK = (
    _seg(D(2026, 11, 16, 21, 0), D(2026, 11, 17, 3, 0), "coast_1", "coast", COAST,
         goal="mass and tau, fan on (1 of 2)", isolates=("tau",), note="ends 03:00 (was 03:30)"),
    _seg(D(2026, 11, 17, 3, 0), D(2026, 11, 17, 5, 30), "radiant_recovery", "recovery",
         RADIANT_RECOVERY, goal="radiant lag per zone", isolates=("radiant_lag",),
         note="starts 03:00 (was 03:30): radiant needs > 2 h from 64"),
    _seg(D(2026, 11, 17, 9, 0), D(2026, 11, 17, 15, 0), "solar_freefloat", "freefloat", COAST,
         flags=("comfort_exempt",), goal="solar aperture", isolates=("solar",)),
    _seg(D(2026, 11, 17, 21, 0), D(2026, 11, 18, 2, 0), "hp_hold", "hold", HP_HOLD, goal="UA",
         isolates=("UA",), note="East 68 (was 66) so the heat pump runs; ends 02:00 to make room for a step "
                                "(East takes ~2 h to settle from the day, so the hold needs 5 h)"),
    _seg(D(2026, 11, 18, 2, 0), D(2026, 11, 18, 3, 30), "hp_setback", "setback", COAST,
         goal="drop East below 68 so hp_recovery has a step",
         note="new: with the hold at 68 there is nothing to recover from"),
    _seg(D(2026, 11, 18, 3, 30), D(2026, 11, 18, 5, 30), "hp_recovery", "recovery",
         P(east=heat(68), radiant=64), goal="heat pump lag", isolates=("hp_lag",),
         note="basement radiant stays at 64: slab heat rises into the first floor and would mix into the "
              "heat pump's step; the slab keeps the basement near 67-68 for 2 h"),
    _seg(D(2026, 11, 18, 5, 30), D(2026, 11, 18, 21, 0), "radiant_lead_day", "lead",
         P(east=heat(66), radiant=68), goal="radiant daytime share", note="radiant 68 as designed (69 removed)"),
    _seg(D(2026, 11, 18, 21, 0), D(2026, 11, 19, 5, 30), "radiant_hold", "hold",
         P(east=OFF, radiant=HOLD_SP), goal="burner hours per degree-hour", isolates=("radiant_cost",),
         note="radiant 68 (was 66) all night; radiant_recovery_2 dropped: from a 68 hold there is no step"),
    _seg(D(2026, 11, 19, 5, 30), D(2026, 11, 19, 21, 0), "hp_lead_day", "lead",
         P(east=heat(68), radiant=BACKUP), goal="heat pump daytime share",
         note="identical to the new baseline; kept as a labelled day"),
    _seg(D(2026, 11, 19, 21, 0), D(2026, 11, 20, 0, 15), "erv_off", "erv_off",
         P(east=heat(ERV_HOLD_SP), radiant=64, erv="Off"), flags=("owns_erv",), goal="ventilation share",
         isolates=("erv",), note=f"East {ERV_HOLD_SP} (was 66); ends 00:15 (was 00:45) so both halves are 3.25 h"),
    _seg(D(2026, 11, 20, 0, 15), D(2026, 11, 20, 3, 30), "erv_max", "erv_max",
         P(east=heat(ERV_HOLD_SP), radiant=64, erv="Maximum"), flags=("owns_erv",), goal="ventilation share",
         isolates=("erv",), note=f"East {ERV_HOLD_SP} (was 66); 03:30 end (was 04:30) to leave time to recover"),
    _seg(D(2026, 11, 20, 12, 0), D(2026, 11, 20, 15, 0), "prepeak_charge", "charge",
         P(east=heat(70), radiant=68), goal="pre-charge before peak", note="radiant 68 as designed (69 removed)"),
    _seg(D(2026, 11, 20, 15, 0), D(2026, 11, 20, 19, 0), "prepeak_coast", "coast", COAST,
         flags=("comfort_exempt",), goal="coast through peak", isolates=("prepeak",),
         note="ends 19:00 (was 20:00) so radiant can recover before comfort applies again"),
    _seg(D(2026, 11, 20, 19, 0), D(2026, 11, 20, 20, 0), "prepeak_recover", "recovery",
         P(radiant=70), goal="radiant recovery while the heat pump is still blocked",
         note="new: zones were 66.6-67.4 at 20:00 after a coast to 20:00"),
    _seg(D(2026, 11, 20, 21, 0), D(2026, 11, 21, 0, 0), "coast_2", "coast", COAST,
         goal="tau repeat, fan on (2 of 2)", isolates=("tau",),
         note="fan on; ends 00:00 so the radiant recovery after it gets 5.5 h"),
    _seg(D(2026, 11, 21, 0, 0), D(2026, 11, 21, 5, 30), "radiant_recovery_3", "recovery",
         RADIANT_RECOVERY, goal="full radiant recovery curve", isolates=("radiant_lag",),
         note="starts 00:00: on 11-17 radiant from 03:00 had not changed upstairs cooling by 04:45"),
    _seg(D(2026, 11, 21, 12, 0), D(2026, 11, 21, 17, 0), "fan_auto_sunny", "fan_auto",
         P(fan="auto", erv="Off"), flags=("owns_erv",), goal="fan mixing of solar gain",
         note="ERV off: it runs the air handler fan whenever it is on"),
    _seg(D(2026, 11, 21, 21, 0), D(2026, 11, 22, 3, 0), "coast_3", "coast", COAST_FAN_AUTO,
         flags=("owns_erv",), goal="tau repeat, fan auto (1 of 2)", isolates=("tau",),
         note="fan auto and ERV off all night; ends 03:00 (was 03:30)"),
    _seg(D(2026, 11, 22, 3, 0), D(2026, 11, 22, 5, 30), "combined_recovery", "recovery",
         P(east=heat(68), radiant=68), goal="fastest recovery", isolates=("combined_lag",),
         note="starts 03:00 (was 03:30) after a coast"),
    _seg(D(2026, 11, 22, 9, 0), D(2026, 11, 22, 15, 0), "band_ride", "band",
         P(east=heat(70), WEST=heat(64), FIRST=heat(64)), goal="heat pump band riding", reactive="band_ride",
         note="West/First radiant at 64 (was 66) so the heat pump does the riding; basement stays at baseline"),
    _seg(D(2026, 11, 22, 21, 0), D(2026, 11, 23, 0, 0), "coast_4", "coast", COAST_FAN_AUTO,
         flags=("owns_erv",), goal="tau repeat, fan auto (2 of 2)", isolates=("tau",),
         note="second fan-auto night (Sun 22), ERV off; ends 00:00 for a 5.5 h radiant recovery"),
    _seg(D(2026, 11, 23, 0, 0), D(2026, 11, 23, 5, 30), "radiant_recovery_4", "recovery",
         RADIANT_RECOVERY, goal="full radiant recovery curve", isolates=("radiant_lag",),
         note="starts 00:00: on 11-17 radiant from 03:00 had not changed upstairs cooling by 04:45"),
)

CO2_DECAY_WINDOWS = tuple(
    _seg(D(2026, 11, d, 9, 0), D(2026, 11, d, 16, 0), "co2_decay", "co2_decay", DesiredPatch(),
         goal="ventilation rate from CO2 decay", isolates=("ach",), reactive="co2_decay")
    for d in (16, 17, 18, 19, 20)
)


def week_plan() -> Plan:
    return Plan(segments=WEEK, overlays=CO2_DECAY_WINDOWS)
