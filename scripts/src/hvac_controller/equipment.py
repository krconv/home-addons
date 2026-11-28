"""What the plant can do this tick: heat pump lockout, boiler availability, the CH setpoint. Pure.

The app never writes the boiler's power switch and never toggles East to reset a lockout; the
only plant write is reasserting the one commissioned CH setpoint after the board reboots.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .site import Site
from .types import CIRCUITS, RADIANT, Circuit, Mem, Snapshot

BOILER_DEBOUNCE_MIN = 2.0   # the power switch reads unavailable/off for seconds on every ESPHome reconnect
LOCKOUT_GAP_MIN = 3.0       # a suspect run with a hole in it (skipped ticks) starts over


def blocked(snap: Snapshot, mem: Mem, site: Site) -> tuple[dict[Circuit, str], list[dict]]:
    """Why each unusable circuit can't be used, after updating the lockout and boiler memory."""
    events = _lockout(snap, mem, site) + _boiler_down(snap, mem)
    out: dict[Circuit, str] = {}
    for c in CIRCUITS:
        if not snap.tstats[c].available:
            out[c] = "thermostat unavailable"
        elif c == "HP" and mem.lockout.get("state") == "locked":
            out[c] = f"heat pump locked out since {mem.lockout['since'][11:16]}"
        elif c in RADIANT and mem.boiler_down and _minutes(snap.now, mem.boiler_down) >= BOILER_DEBOUNCE_MIN:
            out[c] = "boiler off" if snap.boiler.power is False else "boiler unavailable"
    return out, events


def _boiler_down(snap: Snapshot, mem: Mem) -> list[dict]:
    if snap.boiler.power is True:
        was, mem.boiler_down = mem.boiler_down, None
        return [_event(snap.now, "boiler", "up")] if was and _minutes(snap.now, was) >= BOILER_DEBOUNCE_MIN else []
    if mem.boiler_down is None:
        mem.boiler_down = _iso(snap.now)
    return []


def _lockout(snap: Snapshot, mem: Mem, site: Site) -> list[dict]:
    """normal -> suspect -> locked. Evidence is a credible East call with the compressor freshly
    off and the heat pump under lockout_w, continuously for lockout_min. Anything unknown drops a
    suspect back to normal; only measured compressor recovery clears a lockout."""
    lim, now, hp, east = site.limits, snap.now, snap.hp, snap.tstats["HP"]
    lk = mem.lockout
    if lk.get("state") == "locked":
        if hp.compressor is True and hp.power_w is not None and hp.power_w >= lim.lockout_w:
            mem.lockout = {}
            return [_event(now, "lockout", "cleared", f"compressor on at {hp.power_w:.0f} W")]
        return []
    known = east.available and east.action is not None and hp.compressor is not None and hp.power_w is not None
    evidence = (known and east.mode == "heat" and east.action == "heating" and hp.compressor is False
                and hp.power_w < lim.lockout_w)
    if not evidence:
        mem.lockout = {}
        return []
    if lk.get("state") != "suspect" or _minutes(now, lk["seen"]) > LOCKOUT_GAP_MIN:
        mem.lockout = {"state": "suspect", "since": _iso(now), "seen": _iso(now)}
        return []
    if _minutes(now, lk["since"]) >= lim.lockout_min:
        mem.lockout = {"state": "locked", "since": _iso(now)}
        return [_event(now, "lockout", "locked", f"East calling, compressor off, {hp.power_w:.0f} W "
                                                  f"since {lk['since'][11:16]}")]
    mem.lockout = {**lk, "seen": _iso(now)}
    return []


def ch_setpoint(snap: Snapshot, mem: Mem, site: Site, dry_run: bool) -> Optional[float]:
    """The commissioned CH setpoint when a fresh readback with the boiler on shows something else
    (the 99.5 F boot value). Live: at most once per reassert_min. Dry: once per episode, since
    nothing it does will change the readback. Without a fresh readback, nothing."""
    b, ch = site.boiler, snap.boiler.ch_f
    if snap.boiler.power is not True or ch is None:
        return None
    if abs(ch - b.ch_f) <= b.tolerance_f:
        mem.ch_at = None
        return None
    if mem.ch_at is not None and (dry_run or _minutes(snap.now, mem.ch_at) < b.reassert_min):
        return None
    mem.ch_at = _iso(snap.now)
    return b.ch_f


def _event(now: datetime, kind: str, what: str, why: str = "") -> dict:
    return {"t": _iso(now), "kind": kind, "what": what, "why": why}


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def _minutes(now: datetime, iso: str) -> float:
    return (now - datetime.fromisoformat(iso)) / timedelta(minutes=1)
