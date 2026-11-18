"""decide(): a reconciler from (now, plan, observation, mem) to the commands still needed."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta
from typing import Optional

from . import guards
from .mem import Mem
from .plan import COLD_HOLD, Plan, baseline, baseline_period, in_comfort, night_of
from .types import (AUTO_KEY, ERV_KEY, FAN_KEY, HEATED, ZONE_KEYS, Command, Config, Desired,
                    Event, Observation, Segment, ZoneCommand)

MAX_EVENTS = 20
COMMANDED_ZONES = HEATED  # the garage is never touched


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s)


def target(now: datetime, plan: Plan, obs: Observation, mem: Mem, cfg: Config) -> tuple[Desired, list[Event]]:
    """desired = guards(segment.patch(baseline(now))). Mutates mem's guard state machines."""
    seg = plan.at(now)
    d = baseline(now, cfg)
    events: list[Event] = []
    if seg is not None and (seg.requires is None or seg.requires(obs)):
        patch = seg.patch
        if seg.phase == "coast":
            if seg.key not in mem.coast_abort:
                cold = obs.outdoor is not None and obs.outdoor < cfg.coast_abort_outdoor_f
                mem.coast_abort[seg.key] = cold
                if cold:
                    events.append(Event(now, "coast_aborted", {"segment": seg.key, "outdoor": obs.outdoor}))
            if mem.coast_abort[seg.key]:
                patch = COLD_HOLD
        d = patch(d)
    if seg is not None and seg.reactive == "band_ride":
        mem.band, d = guards.band_ride_step(mem.band, obs, d)
    if seg is not None and seg.reactive and seg.reactive.startswith("hold_at_start:"):
        mem.holds, d = guards.hold_at_start_step(mem.holds, seg.reactive.split(":", 1)[1], obs, d)

    overlay = plan.overlay_at(now)
    mem.co2_decay, ev = guards.co2_decay_step(mem.co2_decay, now, obs, overlay, cfg)
    events += ev
    d = guards.apply_co2_decay(d, mem.co2_decay)

    mem.lockout, ev = guards.lockout_step(mem.lockout, now, obs, cfg, night_of(now))
    events += ev
    d = guards.apply_lockout(d, mem.lockout)

    peak = guards.peak_active(now, cfg) and not (seg is not None and "allow_peak_hp" in seg.flags)
    if peak:
        d = guards.hp_job_to_radiant(d, cfg.peak_radiant_margin if in_comfort(now) else 0)
    hp_unusable = mem.lockout["state"] in ("FALLBACK", "RESETTING")
    d, mem.floor_latched, ev = guards.floor_guard(d, obs, mem.floor_latched, cfg, hp_unusable, peak)
    events += ev
    d, mem.co2_forced, ev = guards.co2_guard(d, obs, mem.co2_forced, cfg)
    events += ev
    return d, events


def _desired_values(d: Desired) -> dict[str, object]:
    out: dict[str, object] = {ZONE_KEYS[z]: d.zones[z] for z in COMMANDED_ZONES}
    out[FAN_KEY] = d.east_fan
    out[AUTO_KEY] = d.erv_automation
    if d.erv is not None:
        out[ERV_KEY] = d.erv
    return out


def _zone_of(key: str) -> Optional[str]:
    if key.startswith("zone:"):
        return key[5:]
    if key == FAN_KEY:
        return "EAST"
    return None


def _observed(key: str, obs: Observation) -> tuple[bool, object]:
    """(available, observed value as json) for an actuator key."""
    z = _zone_of(key)
    if z is not None:
        o = obs.zones.get(z)
        if o is None or not o.available:
            return False, None
        if key == FAN_KEY:
            # The thermostat drops fan_mode when its integration stops reporting fan support (seen
            # after an HA restart on 2026-11-18); that is missing data, not someone changing the fan.
            return o.fan is not None, o.fan
        c = o.as_command()
        return True, c.to_json() if c else {"mode": o.mode}
    if key == ERV_KEY:
        return obs.erv_mode is not None, obs.erv_mode
    return obs.erv_automation is not None, obs.erv_automation


def _json(v: object) -> object:
    return v.to_json() if isinstance(v, ZoneCommand) else v


def _same(observed: object, value_json: object) -> bool:
    if isinstance(value_json, dict) and isinstance(observed, dict):
        if value_json["mode"] != observed.get("mode"):
            return False
        if value_json["mode"] == "off":
            return True
        return all(observed.get(k) == value_json.get(k) for k in ("setpoint", "low", "high"))
    return observed == value_json


def _grade(mem: Mem, now: datetime, reason: str, void: bool) -> None:
    g = mem.grades.setdefault(night_of(now), {"void": False, "reasons": []})
    g["void"] = g["void"] or void
    if reason not in g["reasons"]:
        g["reasons"].append(reason)


def _track(mem: Mem, now: datetime, obs: Observation, cfg: Config, events: list[Event]) -> None:
    """Confirm or expire pending commands, then detect manual overrides."""
    window = timedelta(minutes=cfg.confirm_window_min)
    for key, p in list(mem.pending.items()):
        avail, seen = _observed(key, obs)
        if not avail:
            p["deadline"] = _iso(max(_t(p["deadline"]), now + window))
            continue
        if _same(seen, p["value"]):
            del mem.pending[key]
            continue
        if now < _t(p["deadline"]):
            continue
        del mem.pending[key]
        if _same(seen, p["before"]) and p.get("retries", 0) < cfg.max_retries:
            events.append(Event(now, "not_applied", {"key": key, "retries": p.get("retries", 0)}))
            mem.last_cmd[key] = {"value": None, "t": _iso(now), "retries": p.get("retries", 0) + 1}
            continue
        _mark_override(mem, now, key, seen, p["value"], events)
    for key, last in list(mem.last_cmd.items()):
        z = _zone_of(key)
        if z is None or key in mem.pending or z in mem.overrides or last.get("value") is None:
            continue
        avail, seen = _observed(key, obs)
        if avail and not _same(seen, last["value"]):
            _mark_override(mem, now, key, seen, last["value"], events)


def _mark_override(mem: Mem, now: datetime, key: str, seen: object, expected: object, events: list[Event]) -> None:
    z = _zone_of(key)
    if z is None:
        mem.last_cmd.pop(key, None)  # ERV/automation drift: just reconcile again
        return
    mem.overrides[z] = mem.seg_key
    _grade(mem, now, f"override {z}", void=True)
    events.append(Event(now, "override", {"zone": z, "key": key, "seen": seen, "expected": expected}))


def _rate_ok(mem: Mem, key: str, now: datetime) -> bool:
    hour_ago = now - timedelta(hours=1)
    times = [s for s in mem.cmd_times.get(key, []) if _t(s) > hour_ago]
    mem.cmd_times[key] = times
    return len(times) < 4


def _emit(mem: Mem, now: datetime, key: str, value: object, before: object, cfg: Config,
          out: list[Command], events: list[Event]) -> None:
    if not _rate_ok(mem, key, now):
        events.append(Event(now, "rate_limited", {"key": key}))
        return
    vj = _json(value)
    retries = (mem.last_cmd.get(key) or {}).get("retries", 0)
    mem.pending[key] = {"value": vj, "sent": _iso(now), "before": before, "retries": retries,
                        "deadline": _iso(now + timedelta(minutes=cfg.confirm_window_min))}
    mem.last_cmd[key] = {"value": vj, "t": _iso(now)}
    mem.cmd_times[key].append(_iso(now))
    out.append(Command(key, value))


def _reconcile(mem: Mem, now: datetime, d: Desired, obs: Observation, cfg: Config,
               events: list[Event]) -> list[Command]:
    out: list[Command] = []
    for key, value in _desired_values(d).items():
        z = _zone_of(key)
        if z is not None and z in mem.overrides:
            continue
        avail, seen = _observed(key, obs)
        if not avail:
            continue
        vj = _json(value)
        if cfg.dry_run:
            # Nothing reaches the device: reconcile against what was last intended.
            last = (mem.last_cmd.get(key) or {}).get("value")
            if not _same(seen if last is None else last, vj):
                _emit(mem, now, key, value, seen, cfg, out, events)
                mem.pending.pop(key, None)
            continue
        p = mem.pending.get(key)
        if p is not None and p["value"] != vj:
            # The device most likely holds the unconfirmed value; HA's state is stale until the poll.
            _emit(mem, now, key, value, p["before"], cfg, out, events)
            continue
        if _same(seen, vj):
            mem.pending.pop(key, None)
            mem.last_cmd[key] = {"value": vj, "t": (mem.last_cmd.get(key) or {}).get("t") or _iso(now)}
            continue
        if p is not None:
            continue
        _emit(mem, now, key, value, seen, cfg, out, events)
    return out


def _boundary(mem: Mem, now: datetime, seg: Optional[Segment], events: list[Event]) -> None:
    key = seg.key if seg is not None else baseline_period(now)
    if key == mem.seg_key:
        return
    if mem.overrides:
        events.append(Event(now, "override_cleared", {"zones": sorted(mem.overrides)}))
    mem.seg_key = key
    for z in mem.overrides:  # Kodey's value is the new starting point, not a fresh override
        for k in (ZONE_KEYS[z], FAN_KEY) if z == "EAST" else (ZONE_KEYS[z],):
            mem.last_cmd.pop(k, None)
            mem.pending.pop(k, None)
    mem.overrides = {}
    mem.floor_latched = []
    mem.lockout = {"state": "NORMAL", "since": None, "reset_night": mem.lockout.get("reset_night")}
    mem.band = {"state": "UP"}
    events.append(Event(now, "segment", {"key": key}))


def kill_restore(now: datetime, cfg: Config = Config()) -> Desired:
    """The static state the kill switch leaves behind: the comfort baseline.

    The app goes hands-off afterwards, so a night setback restored at 02:00 would still be in
    force at 05:30.
    """
    return baseline(datetime.combine(now.date(), time(12, 0)), cfg)


def _kill(mem: Mem, now: datetime, obs: Observation, cfg: Config, events: list[Event]) -> list[Command]:
    """Disabled: restore the baseline once (deferring unavailable zones), then hands off."""
    if mem.killed:
        due = [k for k in mem.deferred_restore if _observed(k, obs)[0]]
        if not due:
            return []
        mem.deferred_restore = [k for k in mem.deferred_restore if k not in due]
        base = _desired_values(kill_restore(now, cfg))
        out = []
        for k in due:
            seen = _observed(k, obs)[1]
            if not _same(seen, _json(base[k])):
                out.append(Command(k, base[k]))
        return out
    mem.killed = True
    _grade(mem, now, "kill switch", void=True)
    events.append(Event(now, "kill"))
    base = kill_restore(now, cfg)
    mem.overrides = {}
    out = []
    for key, value in _desired_values(base).items():
        avail, seen = _observed(key, obs)
        if not avail:
            if _zone_of(key) is not None:
                mem.deferred_restore.append(key)
            continue
        if not _same(seen, _json(value)):
            out.append(Command(key, value))
    mem.pending = {}
    mem.last_cmd = {}
    return out


def decide(now: datetime, plan: Plan, obs: Observation, mem: Mem, cfg: Config = Config()
           ) -> tuple[list[Command], Mem, list[Event]]:
    mem = mem.copy()
    events: list[Event] = []
    seg = plan.at(now)
    if not obs.enabled:
        cmds = _kill(mem, now, obs, cfg, events)
        return cmds, _log(mem, events), events
    if mem.killed:
        mem.killed = False
        mem.deferred_restore = []
        mem.last_cmd = {}
        mem.pending = {}
        events.append(Event(now, "resume"))
    _boundary(mem, now, seg, events)
    if not cfg.dry_run:
        _track(mem, now, obs, cfg, events)
    d, ev = target(now, plan, obs, mem, cfg)
    events += ev
    for e in ev:
        if e.kind == "lockout":
            _grade(mem, now, "lockout", void=False)
        if e.kind == "co2_force_max":
            _grade(mem, now, "co2 forced ERV max", void=False)
        if e.kind == "floor_at_risk":
            _grade(mem, now, f"floor at risk {e.detail['zone']}", void=False)
        if e.kind == "floor_guard":
            _grade(mem, now, f"floor guard {e.detail['zone']}" + (" (heat pump)" if e.detail.get("rescue") else ""),
                   void=False)
    cmds = _reconcile(mem, now, d, obs, cfg, events)
    return cmds, _log(mem, events), events


def _log(mem: Mem, events: list[Event]) -> Mem:
    mem.events = (mem.events + [e.to_json() for e in events if e.kind != "segment"])[-MAX_EVENTS:]
    return mem


def desired_now(now: datetime, plan: Plan, obs: Observation, mem: Mem, cfg: Config = Config()) -> Desired:
    """The guarded target without side effects, for status and reports."""
    return target(now, plan, obs, mem.copy(), cfg)[0]


def status(now: datetime, plan: Plan, mem: Mem) -> tuple[str, dict]:
    seg = plan.at(now)
    if mem.killed:
        state = "disabled"
    elif seg is not None:
        state = f"{seg.test_id}/{seg.phase}"
    else:
        state = "baseline/" + ("day" if baseline_period(now).startswith("baseline:day") else "night")
    if mem.co2_decay.get("state") in ("OFF", "MEDIUM"):
        state += f"+co2_decay/{mem.co2_decay['state']}"
    night = night_of(now)
    attrs = {
        "night": night,
        "night_grade": "void" if mem.grades.get(night, {}).get("void") else
                       ("degraded" if mem.grades.get(night, {}).get("reasons") else "ok"),
        "voids": sorted(k for k, g in mem.grades.items() if g["void"]),
        "grades": mem.grades,
        "lockout": mem.lockout["state"],
        "overrides": sorted(mem.overrides),
        "floor_at_risk": sorted(k.split(":")[0] for k in mem.floor_latched if k.endswith(":alert")),
        "last_events": mem.events[-10:],
    }
    return state, attrs
