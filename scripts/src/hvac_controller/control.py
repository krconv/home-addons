"""One tick of the controller as a pure function: Snapshot + Mem -> wants, switches, Mem, status.

The policy decides once per circuit per slot; guards run every tick on measured temperatures and
outrank it, so wrong policy numbers cost money but cannot leave a zone cold.
"""
from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Mapping

from . import cost, equipment
from .site import Site
from .types import (CIRCUITS, FLOOR_F, RADIANT, SERVES, Band, Blind, Circuit, Mem, Policy, Reading, Rule,
                    Snapshot, Step, Switch, Want)

Wants = Mapping[Circuit, Want]


def step(snap: Snapshot, mem: Mem, site: Site, dry_run: bool, policy: Policy = Policy(),
         honor_kill: bool = False) -> Step:
    mem = Mem.from_json(copy.deepcopy(mem.to_json()))
    blocked, events = equipment.blocked(snap, mem, site)
    if honor_kill and not snap.enabled:
        want = {c: Want(False, Rule.KILL, "kill switch off: every thermostat to its hold") for c in CIRCUITS}
        switches = () if mem.killed else tuple(Switch(c, False) for c in CIRCUITS if snap.tstats[c].available)
        mem.killed, mem.latch = True, {}
        return Step(want, switches, mem, status(snap, want, mem, site, dry_run, policy, "disabled", blocked),
                    events=tuple(events))
    mem.killed = False
    slot = slot_key(snap.now, site)
    if any(mem.latch.get(c, {}).get("slot") != slot for c in CIRCUITS):
        latched = {c: (mem.latch.get(c, {}).get("on", False), mem.latch.get(c, {}).get("slots_on", 0))
                   for c in CIRCUITS}
        mem.primary = primary(policy, snap, mem.primary.get("source", "HP"), blocked, site)
        fresh = wants(policy, snap, latched, blocked, mem.primary["source"], site)
        mem.latch = {c: {"on": w.on, "rule": w.rule.value, "why": w.why, "slot": slot,
                         "slots_on": latched[c][1] + 1 if w.on and latched[c][0] else int(w.on)}
                     for c, w in fresh.items()}
    policy_wants = {c: Want(l["on"], Rule(l["rule"]), l["why"]) for c, l in mem.latch.items()}
    want = guards(policy_wants, snap, mem, blocked, site)
    events += overrides(want, snap, mem, site, dry_run)
    switches = reconcile(want, snap, mem, site, dry_run)
    ch = equipment.ch_setpoint(snap, mem, site)
    return Step(want, switches, mem, status(snap, want, mem, site, dry_run, policy, "rules", blocked), ch,
                tuple(events))


def primary(policy: Policy, snap: Snapshot, running: str, blocked: Mapping[Circuit, str], site: Site) -> dict:
    """Which source carries the house this slot. Availability first; price only on-peak, the one
    band where the two are close; the running source keeps a near-tie."""
    if "HP" in blocked:
        return {"source": "RADIANT", "why": blocked["HP"]}
    if "RAD_WEST" in blocked and "RAD_FIRST" in blocked:
        return {"source": "HP", "why": f"radiant unusable: {blocked['RAD_WEST']}"}
    if snap.tou != "On-peak":
        return {"source": "HP", "why": f"{snap.tou or 'band unknown'}: the heat pump is cheapest"}
    if policy.peak_primary != "COST":
        return {"source": policy.peak_primary, "why": f"On-peak: policy says {policy.peak_primary}"}
    src, why = cost.choose(cost.table(snap.prices, site.economics).get("On-peak"),
                           "RADIANT" if running == "RADIANT" else "HP", site.economics.switch_margin)
    return {"source": src, "why": f"On-peak: {why}"}


def band_at(t: datetime, site: Site) -> Band:
    c = site.comfort
    return Band(c.day_lo if c.day_start <= t.time() < c.day_end else c.night_lo, c.day_hi)


def slot_key(t: datetime, site: Site) -> str:
    m = site.limits.slot_min
    return t.replace(minute=t.minute - t.minute % m, second=0, microsecond=0).isoformat(timespec="minutes")


def wants(policy: Policy, snap: Snapshot, latch: Mapping[Circuit, tuple[bool, int]],
          blocked: Mapping[Circuit, str], primary: str, site: Site) -> dict[Circuit, Want]:
    """The control law for this slot. Zones without a Reading are skipped; the guards own them."""
    lim, now = site.limits, snap.now
    usable = {c: c not in blocked for c in CIRCUITS}
    temps = {z: t.value for z, t in snap.temps.items() if isinstance(t, Reading)}
    band = band_at(now, site)

    def lo_ahead(h: float) -> float:
        return max(band.lo, band_at(now + timedelta(hours=h), site).lo)

    on_peak = snap.tou == "On-peak"
    peak_start = datetime.combine(now.date(), site.comfort.peak_start)
    # Mid-peak only happens on non-holiday weekdays before On-peak, so the sensor alone says "soon".
    soon_peak = snap.tou == "Mid-peak" and now >= peak_start - timedelta(hours=policy.preheat_h)
    radiant_primary = primary == "RADIANT"
    hp_target = band.hi - lim.margin_f if soon_peak else lo_ahead(policy.recover_h) + lim.margin_f

    out: dict[Circuit, Want] = {"HP": _hp(policy, temps, hp_target, soon_peak, radiant_primary,
                                          blocked.get("HP"), latch["HP"], lim.min_run_slots)}

    rad_target = (lo_ahead(policy.radiant_lead_h) + lim.margin_f if radiant_primary
                  else hp_target - policy.backup_drop_f)
    stop_lo = band_at(now + timedelta(hours=policy.basement_stop_h), site).lo
    basement_lo = stop_lo if stop_lo < band.lo else lo_ahead(policy.basement_recover_h)
    targets = {"RAD_WEST": rad_target, "RAD_FIRST": rad_target, "RAD_BASEMENT": basement_lo + lim.margin_f}
    on_rule = {"RAD_WEST": Rule.PRIMARY if radiant_primary else Rule.BACKUP,
               "RAD_FIRST": Rule.PRIMARY if radiant_primary else Rule.BACKUP, "RAD_BASEMENT": Rule.CALL}
    short: dict[Circuit, tuple[float, str]] = {}
    for c in RADIANT:
        gaps = [(targets[c] - temps[z], z) for z in SERVES[c] if z in temps]
        if gaps:
            short[c] = max(gaps)

    def keeps_running(c: Circuit) -> bool:
        on, n = latch[c]
        return on and usable[c] and c in short and (n < lim.min_run_slots or short[c][0] > -lim.radiant_swing_f)

    running = [c for c in RADIANT if keeps_running(c)]
    needy = sorted((c for c in RADIANT if usable[c] and c in short and short[c][0] > 0),
                   key=lambda c: -short[c][0])
    chosen = running[0] if running else (needy[0] if needy else None)
    for c in RADIANT:
        gap, z = short.get(c, (None, ""))
        if not usable[c]:
            out[c] = Want(False, Rule.UNUSABLE, blocked[c])
        elif gap is None:
            out[c] = Want(False, Rule.SATISFIED, "no zone reading")
        elif c == chosen and c in running and gap <= 0:
            rule = Rule.MIN_RUN if latch[c][1] < lim.min_run_slots else Rule.SWING
            out[c] = Want(True, rule, f"{z} {targets[c] - gap:.1f} toward {targets[c] + lim.radiant_swing_f:.1f}")
        elif c == chosen:
            out[c] = Want(True, on_rule[c], f"{z} {targets[c] - gap:.1f} under {targets[c]:.1f}")
        elif gap > 0:
            out[c] = Want(False, Rule.ONE_AT_A_TIME, f"{z} {targets[c] - gap:.1f} under {targets[c]:.1f}, waits for {chosen}")
        else:
            out[c] = Want(False, Rule.SATISFIED, f"{z} at or over {targets[c]:.1f}")
    return out


def _hp(policy: Policy, temps: Mapping[str, float], target: float, soon_peak: bool, radiant_primary: bool,
        blocked: str | None, latch: tuple[bool, int], min_run: int) -> Want:
    if blocked:
        return Want(False, Rule.UNUSABLE, f"{blocked}: radiant is primary")
    if radiant_primary:
        return Want(False, Rule.PEAK_YIELDS, "On-peak: radiant is cheaper and holds the band")
    zs = {z: temps[z] for z in SERVES["HP"] if z in temps}
    if not zs:
        return Want(False, Rule.SATISFIED, "no zone reading")
    z = min(zs, key=zs.get)
    if zs[z] < target:
        return Want(True, Rule.PREHEAT if soon_peak else Rule.CALL, f"{z} {zs[z]:.1f} under {target:.1f}")
    was_on, n = latch
    if was_on and n < min_run:
        return Want(True, Rule.MIN_RUN, f"run {n} of {min_run} slots")
    if was_on and zs[z] < target + policy.hp_swing_f:
        return Want(True, Rule.SWING, f"{z} {zs[z]:.1f} toward {target + policy.hp_swing_f:.1f}")
    return Want(False, Rule.SATISFIED, f"{z} {zs[z]:.1f} at or over {target:.1f}")


def guards(want: Wants, snap: Snapshot, mem: Mem, blocked: Mapping[Circuit, str], site: Site
           ) -> dict[Circuit, Want]:
    """Strongest first: blind (leave it to the thermostat), floor (coldest room near 64 turns on
    every usable circuit that serves it, on-peak or not), unusable mid-slot, hi stop."""
    lim = site.limits
    engage, release = FLOOR_F + lim.floor_lead_f, FLOOR_F + lim.floor_lead_f + lim.floor_release_f
    floor = set(mem.floor)
    for z, t in snap.temps.items():
        if isinstance(t, Blind) or t.coldest >= release:
            floor.discard(z)
        elif t.coldest <= engage:
            floor.add(z)
    mem.floor = sorted(floor)
    hi = band_at(snap.now, site).hi + lim.hi_stop_f
    out = dict(want)
    for c in CIRCUITS:
        temps = {z: snap.temps[z] for z in SERVES[c]}
        readings = {z: t for z, t in temps.items() if isinstance(t, Reading)}
        cold = [z for z in temps if z in floor]
        hot = [z for z, t in readings.items() if t.value >= hi]
        if not readings:
            out[c] = Want(False, Rule.BLIND, f"{'/'.join(temps)} blind: thermostat hold keeps the floor")
        elif cold and c not in blocked:
            out[c] = Want(True, Rule.FLOOR, f"{cold[0]} coldest room {readings[cold[0]].coldest:.1f}")
        elif c in blocked and out[c].on:
            out[c] = Want(False, Rule.UNUSABLE, blocked[c])
        elif hot and out[c].on:
            out[c] = Want(False, Rule.HI_STOP, f"{hot[0]} {readings[hot[0]].value:.1f} at or over {hi:.1f}")
    return out


def reconcile(want: Wants, snap: Snapshot, mem: Mem, site: Site, dry_run: bool) -> tuple[Switch, ...]:
    """Dry run: one intent each time a circuit's (on, rule) changes. Live: a Switch where the
    thermostat doesn't show the wanted setpoint, not before it could have polled the last one,
    and at most max_sends_per_hour per thermostat unless the floor guard asks."""
    lim, now, out = site.limits, snap.now, []
    for c in CIRCUITS:
        w = want[c]
        cur = {"on": w.on, "rule": w.rule.value}
        if dry_run:
            if mem.intent.get(c) != cur:
                out.append(Switch(c, w.on))
            mem.intent[c] = cur
            continue
        t = snap.tstats[c]
        if not t.available or c in mem.override or (t.mode == "heat" and t.setpoint == site.thermostats[c].setpoint(w.on)):
            continue
        sent = [s for s in mem.sent.get(c, []) if now - datetime.fromisoformat(s) < timedelta(hours=1)]
        recent = sent and now - datetime.fromisoformat(sent[-1]) < timedelta(minutes=lim.confirm_min)
        capped = len(sent) >= lim.max_sends_per_hour and w.rule is not Rule.FLOOR
        if not recent and not capped:
            out.append(Switch(c, w.on))
            sent.append(now.isoformat(timespec="seconds"))
        mem.sent[c] = sent
        mem.intent[c] = cur
    return tuple(out)


def overrides(want: Wants, snap: Snapshot, mem: Mem, site: Site, dry_run: bool) -> list[dict]:
    """Live: once a thermostat has shown our wanted setpoint, a mode or setpoint outside both of
    ours is a person's override; reconcile leaves that circuit alone until it shows one of ours
    again or override_clear passes. Dry run can't tell its own writes from the live baseline's, so
    it records only values no known writer uses."""
    now, out = snap.now, []
    for c in CIRCUITS:
        t, spec = snap.tstats[c], site.thermostats[c]
        if not t.available or (t.mode == "heat" and t.setpoint is None):
            continue  # unknown, e.g. attributes dropped across an HA restart
        seen = {"mode": t.mode, "setpoint": t.setpoint}
        ours = t.mode == "heat" and t.setpoint in (spec.hold_f, spec.call_f)
        if dry_run:
            known = ours or (t.mode == "heat" and t.setpoint in site.baseline_setpoints)
            if not known and mem.foreign.get(c) != seen:
                out.append(_event(now, "foreign", c, f"{spec.entity} {t.mode} {t.setpoint}: no known writer"))
                mem.foreign[c] = seen
            elif known and c in mem.foreign:
                del mem.foreign[c]
            continue
        o = mem.override.get(c)
        if o and (ours or datetime.fromisoformat(o["since"]) < _last(site.limits.override_clear, now)):
            del mem.override[c]
            mem.confirmed = [x for x in mem.confirmed if x != c]
            out.append(_event(now, "override", c, "ended: back to ours" if ours else "ended: daily clear"))
        elif o or (not ours and c not in mem.confirmed):
            continue
        elif not ours:
            mem.override[c] = {"since": now.isoformat(timespec="seconds"), **seen}
            mem.confirmed = [x for x in mem.confirmed if x != c]
            out.append(_event(now, "override", c, f"{spec.entity} {t.mode} {t.setpoint}: hands off"))
        elif t.setpoint == spec.setpoint(want[c].on) and c not in mem.confirmed:
            mem.confirmed = sorted({*mem.confirmed, c})
    return out


def _last(t, now: datetime) -> datetime:
    at = datetime.combine(now.date(), t)
    return at if at <= now else at - timedelta(days=1)


def _event(now: datetime, kind: str, circuit: str, what: str) -> dict:
    return {"t": now.isoformat(timespec="seconds"), "kind": kind, "circuit": circuit, "what": what}


def status(snap: Snapshot, want: Wants, mem: Mem, site: Site, dry_run: bool, policy: Policy, mode: str,
           blocked: Mapping[Circuit, str]) -> tuple[str, dict]:
    band = band_at(snap.now, site)
    zones = {z: ({"temp": round(t.value, 1), "coldest": round(t.coldest, 1), "tier": t.tier.value}
                 if isinstance(t, Reading) else {"blind": t.reason}) for z, t in snap.temps.items()}
    circuits = {c: {"on": w.on, "setpoint": site.thermostats[c].setpoint(w.on), "rule": w.rule.value, "why": w.why}
                | ({"override": mem.override[c]} if c in mem.override else {})
                | ({"foreign": mem.foreign[c]} if c in mem.foreign else {})
                for c, w in want.items()}
    p, econ, b = snap.prices, site.economics, snap.boiler
    return (f"{mode} (dry)" if dry_run else mode), {
        "band": {"lo": band.lo, "hi": band.hi}, "tou": snap.tou, "zones": zones, "circuits": circuits,
        "primary": mem.primary, "unusable": dict(blocked),
        "cost": {"usd_per_mmbtu": cost.table(p, econ), "cop": econ.cop, "boiler_eff": econ.boiler_eff,
                 "propane_gal": p.propane_gal,
                 "propane_source": f"default: {site.propane_price} missing" if p.propane_default else site.propane_price,
                 "missing_rates": sorted(set(site.rates) - set(p.kwh))},
        "boiler": {"power": {True: "on", False: "off", None: "unavailable"}[b.power], "ch_f": b.ch_f,
                   "ch_plan_f": site.boiler.ch_f, "ch_reasserted": mem.ch_at},
        "heat_pump": {"compressor": snap.hp.compressor, "power_w": snap.hp.power_w,
                      "lockout": mem.lockout or {"state": "normal"}},
        "floor_guard": list(mem.floor), "policy": asdict(policy), "outdoor": snap.outdoor_f}
