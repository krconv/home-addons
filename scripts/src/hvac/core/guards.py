"""Guards and the small reactive state machines. Pure: (state, obs, now) in, (state', desired', events) out."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Optional

from .plan import both_away
from .types import (HP_BACKUP_ZONES, OFF, Config, Desired, Event, Observation, Segment,
                    ZoneCommand, heat)


def _t(s: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(s) if s else None


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def hp_job_to_radiant(d: Desired, margin: int = 0) -> Desired:
    """Heat pump off; WEST and FIRST radiant take East's heat target (+ margin for floor lag)."""
    target = d.zones["EAST"].heat_target
    target = None if target is None else target + margin
    zones = {"EAST": OFF}
    if target is not None:
        for z in HP_BACKUP_ZONES:
            c = d.zones[z]
            cur = c.heat_target if c.mode == "heat" else None
            zones[z] = heat(max(cur or target, target))
    return d.with_zones(**zones)


def peak_active(now: datetime, cfg: Config) -> bool:
    if now.weekday() >= 5:
        return False
    start = now.replace(hour=cfg.peak_start_h, minute=0, second=0, microsecond=0) - timedelta(minutes=cfg.peak_lead_min)
    end = now.replace(hour=cfg.peak_end_h, minute=0, second=0, microsecond=0)
    return start <= now < end


def east_called(obs: Observation) -> Optional[bool]:
    """Is the East thermostat asking for heat? None when unknown (unavailable)."""
    e = obs.zones.get("EAST")
    if e is None or not e.available or e.mode not in ("heat", "heat_cool"):
        return None if (e is None or not e.available) else False
    if e.action is not None:
        return e.action == "heating"
    target = e.setpoint if e.mode == "heat" else e.low
    if target is None or e.temp is None:
        return None
    return e.temp < target


def lockout_step(lk: dict, now: datetime, obs: Observation, cfg: Config, night: str) -> tuple[dict, list[Event]]:
    """NORMAL -> SUSPECT -> (RESETTING -> NORMAL) | FALLBACK. FALLBACK holds until the segment ends."""
    lk = dict(lk)
    st = lk["state"]
    called = east_called(obs)
    comp = obs.compressor
    events: list[Event] = []
    if st == "NORMAL":
        if called is True and comp is False:
            lk.update(state="SUSPECT", since=_iso(now))
    elif st == "SUSPECT":
        if called is None or comp is None:
            pass  # unknown: neither advance nor clear
        elif not called or comp:
            lk.update(state="NORMAL", since=None)
        elif now - _t(lk["since"]) >= timedelta(minutes=cfg.lockout_detect_min):
            can_reset = cfg.lockout_auto_reset and lk.get("reset_night") != night
            if can_reset:
                lk.update(state="RESETTING", since=_iso(now), reset_night=night)
                events.append(Event(now, "lockout", {"action": "reset"}))
            else:
                lk.update(state="FALLBACK", since=_iso(now))
                events.append(Event(now, "lockout", {"action": "fallback"}))
    elif st == "RESETTING":
        if now - _t(lk["since"]) >= timedelta(minutes=cfg.lockout_reset_off_min):
            lk.update(state="NORMAL", since=None)
            events.append(Event(now, "lockout_reset_done"))
    return lk, events


def apply_lockout(d: Desired, lk: dict) -> Desired:
    if lk["state"] == "FALLBACK":
        return hp_job_to_radiant(d)
    if lk["state"] == "RESETTING":
        return d.with_zones(EAST=OFF)
    return d


def floor_guard(d: Desired, obs: Observation, latched: list, cfg: Config, east_blocked: bool,
                peak: bool = False) -> tuple[Desired, list, list[Event]]:
    """A heated zone the plan leaves off is re-enabled at the floor setpoint once it reads <= trigger.

    A heat-pump-served radiant zone (WEST, FIRST) that reads <= hp_rescue_trigger while its radiant is
    on brings the heat pump back (East heat hp_rescue_east_sp) until the segment ends.

    Reaching the floor overrides on-peak: the heat pump may run then, and the event says so.
    East while the heat pump is locked out is held by the wing radiant instead: West's
    thermostat drives both wing floors.
    """
    latched = list(latched)
    events: list[Event] = []
    zones: dict[str, ZoneCommand] = {}
    for z, c in d.zones.items():
        if z == "GARAGE":
            continue
        o = obs.zones.get(z)
        seen = o.temp if o is not None and o.available else None
        if c.mode == "off":
            low = seen is not None and seen <= cfg.floor_guard_trigger
            if low and z not in latched:
                latched.append(z)
                events.append(Event(obs.now, "floor_guard", {"zone": z, "temp": seen, "during_peak": peak}))
            if z in latched:
                if z == "EAST" and east_blocked:
                    w = d.zones["WEST"]
                    zones["WEST"] = heat(max(w.heat_target or 0, cfg.floor_guard_setpoint))
                else:
                    zones[z] = heat(cfg.floor_guard_setpoint)
        elif z in HP_BACKUP_ZONES:
            # Radiant on but losing: the wing floors are too weak to hold the floor alone, so the
            # heat pump (East's thermostat) comes back as it would in normal heating.
            # On-peak only a floor reached during peak brings the heat pump in; an earlier rescue waits.
            key = f"{z}:hp_peak" if peak else f"{z}:hp"
            low = seen is not None and seen <= cfg.hp_rescue_trigger
            if low and key not in latched and not east_blocked:
                latched.append(key)
                events.append(Event(obs.now, "floor_guard", {"zone": z, "temp": seen, "rescue": "heat_pump", "during_peak": peak}))
            if low and east_blocked and f"{z}:alert" not in latched:
                # Nothing left to add (heat pump locked out): say so for the monitor.
                latched.append(f"{z}:alert")
                events.append(Event(obs.now, "floor_at_risk", {"zone": z, "temp": seen}))
            if key in latched and not east_blocked:
                e = d.zones["EAST"]
                if e.mode == "off" or (e.heat_target or 0) < cfg.hp_rescue_east_sp:
                    zones["EAST"] = heat(cfg.hp_rescue_east_sp)
    return d.with_zones(**zones), latched, events


def co2_guard(d: Desired, obs: Observation, forced: bool, cfg: Config) -> tuple[Desired, bool, list[Event]]:
    events: list[Event] = []
    if obs.co2 is not None:
        if not forced and obs.co2 > cfg.co2_force_max:
            forced = True
            events.append(Event(obs.now, "co2_force_max", {"co2": obs.co2}))
        elif forced and obs.co2 < cfg.co2_release:
            forced = False
            events.append(Event(obs.now, "co2_release", {"co2": obs.co2}))
    if forced and not d.erv_automation:
        d = replace(d, erv="Maximum")
    return d, forced, events


def band_ride_step(state: dict, obs: Observation, d: Desired) -> tuple[dict, Desired]:
    """East rides 68..70: setpoint 70 until it reads 70, then 68 until it reads 68."""
    e = obs.zones.get("EAST")
    st = state["state"]
    if e is not None and e.available and e.temp is not None:
        if st == "UP" and e.temp >= 70:
            st = "DOWN"
        elif st == "DOWN" and e.temp <= 68:
            st = "UP"
    return {"state": st}, d.with_zones(EAST=heat(70 if st == "UP" else 68))


HOLD_MIN_SP, HOLD_MAX_SP = 68, 71


def hold_at_start_step(holds: dict, group: str, obs: Observation, d: Desired) -> tuple[dict, Desired]:
    """East holds whatever it read when the group's first segment started, so the heat pump runs from
    the start instead of waiting for the house to drift down to a fixed setpoint."""
    holds = dict(holds)
    if group not in holds:
        e = obs.zones.get("EAST")
        if e is None or not e.available or e.temp is None:
            return holds, d
        holds[group] = min(HOLD_MAX_SP, max(HOLD_MIN_SP, round(e.temp)))
    return holds, d.with_zones(EAST=heat(holds[group]))


CO2_OFF_MIN = 60
CO2_MEDIUM_MIN = 60
CO2_AWAY_MIN = 30


def co2_decay_step(state: dict, now: datetime, obs: Observation, overlay: Optional[Segment],
                   cfg: Config) -> tuple[dict, list[Event]]:
    """IDLE -> OFF (60 min) -> MEDIUM (60 min) -> DONE; ABORTED on CO2 > abort or someone home. Once per week."""
    s = dict(state)
    events: list[Event] = []
    away = both_away(obs)
    s["away_since"] = (s.get("away_since") or _iso(now)) if away else None
    st = s["state"]
    if st in ("DONE", "ABORTED"):
        return s, events
    if st == "IDLE":
        if overlay is None or obs.co2 is None or not away:
            return s, events
        enough_time = overlay.end - now >= timedelta(minutes=CO2_OFF_MIN + CO2_MEDIUM_MIN)
        if enough_time and now - _t(s["away_since"]) >= timedelta(minutes=CO2_AWAY_MIN):
            s.update(state="OFF", since=_iso(now))
            events.append(Event(now, "co2_decay", {"phase": "OFF", "co2": obs.co2}))
        return s, events
    since = _t(s["since"])
    if (obs.co2 is not None and obs.co2 > cfg.co2_test_abort) or not away:
        s.update(state="ABORTED", since=_iso(now))
        events.append(Event(now, "co2_decay", {"phase": "ABORTED", "co2": obs.co2, "away": away}))
    elif st == "OFF" and now - since >= timedelta(minutes=CO2_OFF_MIN):
        s.update(state="MEDIUM", since=_iso(now))
        events.append(Event(now, "co2_decay", {"phase": "MEDIUM", "co2": obs.co2}))
    elif st == "MEDIUM" and now - since >= timedelta(minutes=CO2_MEDIUM_MIN):
        s.update(state="DONE", since=_iso(now))
        events.append(Event(now, "co2_decay", {"phase": "DONE", "co2": obs.co2}))
    return s, events


def apply_co2_decay(d: Desired, state: dict) -> Desired:
    mode = {"OFF": "Off", "MEDIUM": "Medium"}.get(state["state"])
    return replace(d, erv=mode, erv_automation=False) if mode else d
