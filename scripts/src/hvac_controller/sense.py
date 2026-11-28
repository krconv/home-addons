"""Raw Home Assistant states in, one validated Snapshot out. Pure.

The validation boundary: after `read`, nothing parses a state string or checks "unavailable".
"""
from __future__ import annotations

from datetime import datetime
from statistics import mean
from typing import Mapping, Optional

from .site import Site
from .types import CIRCUITS, ZONES, Blind, Reading, Snapshot, Tier, TstatObs, Zone, ZoneTemp

RawStates = Mapping[str, dict]   # entity_id -> REST state object
MISSING = ("unavailable", "unknown", "", None)
TOU = ("Off-peak", "Mid-peak", "On-peak")


def read(states: RawStates, now: datetime, site: Site) -> Snapshot:
    tstats = {c: thermostat(states.get(site.thermostats[c].entity)) for c in CIRCUITS}
    tou = _state(states.get(site.tou_band))
    return Snapshot(
        now=now,
        enabled=_state(states.get(site.enabled)) == "on",
        temps={z: fuse_zone(z, states, now, site, tstats) for z in ZONES},
        tstats=tstats,
        tou=tou if tou in TOU else None,
        outdoor_f=_float(_state(states.get(site.outdoor))),
    )


def fuse_zone(zone: Zone, states: RawStates, now: datetime, site: Site,
              tstats: Mapping[str, TstatObs]) -> ZoneTemp:
    """Eye-level rooms first; a settled Airthings replaces its room's template (never both).
    With no fresh room, the thermostat's reading minus its offsets; with neither, Blind."""
    lim = site.limits
    cfg = site.zones[zone]
    airthings_room = _settled_airthings_room(states, now, site)
    rooms: dict[str, float] = {}
    for room in cfg.rooms:
        entity = site.airthings_temp if room.name == airthings_room else room.eye_level
        v = numeric(states.get(entity), now, site) if entity else None
        if v is not None:
            rooms[room.name] = v
    if rooms:
        return Reading(round(mean(rooms.values()), 2), min(rooms.values()), Tier.ROOM, tuple(rooms))
    t = tstats[cfg.thermostat]
    spec = site.thermostats[cfg.thermostat]
    if t.reading is not None and lim.plausible_f[0] <= t.reading <= lim.plausible_f[1]:
        return Reading(t.reading - spec.offset_f, t.reading - spec.coldest_gap_f, Tier.THERMOSTAT, ())
    return Blind("no fresh room sensor and no thermostat reading")


def _settled_airthings_room(states: RawStates, now: datetime, site: Site) -> Optional[str]:
    loc = states.get(site.airthings_location)
    room = _state(loc)
    since = _time(loc.get("last_changed"), site) if loc else None
    if room in MISSING or since is None:
        return None
    return room if (now - since).total_seconds() >= site.limits.airthings_settle_min * 60 else None


def numeric(st: Optional[dict], now: datetime, site: Site) -> Optional[float]:
    """A plausible, fresh float, or None. Freshness uses last_reported when present, because
    last_updated does not move while a value is constant."""
    v = _float(_state(st))
    if v is None or not site.limits.plausible_f[0] <= v <= site.limits.plausible_f[1]:
        return None
    seen = _time(st.get("last_reported") or st.get("last_updated"), site)
    if seen is None or (now - seen).total_seconds() > site.limits.room_max_age_min * 60:
        return None
    return v


def thermostat(st: Optional[dict]) -> TstatObs:
    if _state(st) in MISSING:
        return TstatObs(available=False)
    at = st.get("attributes") or {}
    sp = _float(at.get("temperature"))
    return TstatObs(available=True, mode=st["state"], setpoint=None if sp is None else int(round(sp)),
                    reading=_float(at.get("current_temperature")))


def _state(st: Optional[dict]):
    return None if st is None else st.get("state")


def _float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _time(v, site: Site) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(v)
    except (TypeError, ValueError):
        return None
    return t.astimezone(site.tz).replace(tzinfo=None) if t.tzinfo else t
