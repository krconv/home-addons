"""One tick of the controller as a pure function: Snapshot + Mem -> wants, switches, Mem, status.

The policy decides once per circuit per slot; guards run every tick on measured temperatures and
outrank it, so wrong policy numbers cost money but cannot leave a zone cold.
"""
from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Mapping

from .site import Site
from .types import (CIRCUITS, FLOOR_F, RADIANT, SERVES, Band, Blind, Circuit, Mem, Policy, Reading, Rule,
                    Snapshot, Step, Switch, Want)

Wants = Mapping[Circuit, Want]


def step(snap: Snapshot, mem: Mem, site: Site, dry_run: bool, policy: Policy = Policy(),
         honor_kill: bool = False) -> Step:
    mem = Mem.from_json(copy.deepcopy(mem.to_json()))
    usable = {c: snap.tstats[c].available for c in CIRCUITS}
    if honor_kill and not snap.enabled:
        want = {c: Want(False, Rule.KILL, "kill switch off: every thermostat to its hold") for c in CIRCUITS}
        switches = () if mem.killed else tuple(Switch(c, False) for c in CIRCUITS if usable[c])
        mem.killed, mem.latch = True, {}
        return Step(want, switches, mem, status(snap, want, mem, site, dry_run, policy, "disabled"))
    mem.killed = False
    slot = slot_key(snap.now, site)
    if any(mem.latch.get(c, {}).get("slot") != slot for c in CIRCUITS):
        latched = {c: (mem.latch.get(c, {}).get("on", False), mem.latch.get(c, {}).get("slots_on", 0))
                   for c in CIRCUITS}
        fresh = wants(policy, snap, latched, usable, site)
        mem.latch = {c: {"on": w.on, "rule": w.rule.value, "why": w.why, "slot": slot,
                         "slots_on": latched[c][1] + 1 if w.on and latched[c][0] else int(w.on)}
                     for c, w in fresh.items()}
    policy_wants = {c: Want(l["on"], Rule(l["rule"]), l["why"]) for c, l in mem.latch.items()}
    want = guards(policy_wants, snap, mem, usable, site)
    switches = reconcile(want, snap, mem, site, dry_run)
    return Step(want, switches, mem, status(snap, want, mem, site, dry_run, policy, "rules"))


def band_at(t: datetime, site: Site) -> Band:
    c = site.comfort
    return Band(c.day_lo if c.day_start <= t.time() < c.day_end else c.night_lo, c.day_hi)


def slot_key(t: datetime, site: Site) -> str:
    m = site.limits.slot_min
    return t.replace(minute=t.minute - t.minute % m, second=0, microsecond=0).isoformat(timespec="minutes")


def wants(policy: Policy, snap: Snapshot, latch: Mapping[Circuit, tuple[bool, int]],
          usable: Mapping[Circuit, bool], site: Site) -> dict[Circuit, Want]:
    """The control law for this slot. Zones without a Reading are skipped; the guards own them."""
    lim, now = site.limits, snap.now
    temps = {z: t.value for z, t in snap.temps.items() if isinstance(t, Reading)}
    band = band_at(now, site)

    def lo_ahead(h: float) -> float:
        return max(band.lo, band_at(now + timedelta(hours=h), site).lo)

    on_peak = snap.tou == "On-peak"
    peak_start = datetime.combine(now.date(), site.comfort.peak_start)
    # Mid-peak only happens on non-holiday weekdays before On-peak, so the sensor alone says "soon".
    soon_peak = snap.tou == "Mid-peak" and now >= peak_start - timedelta(hours=policy.preheat_h)
    radiant_primary = not usable["HP"] or (on_peak and policy.peak_primary == "RADIANT")
    hp_target = band.hi - lim.margin_f if soon_peak else lo_ahead(policy.recover_h) + lim.margin_f

    out: dict[Circuit, Want] = {"HP": _hp(policy, temps, hp_target, soon_peak, on_peak, usable["HP"],
                                          latch["HP"], lim.min_run_slots)}

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
            out[c] = Want(False, Rule.UNUSABLE, "thermostat unavailable")
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


def _hp(policy: Policy, temps: Mapping[str, float], target: float, soon_peak: bool, on_peak: bool,
        usable: bool, latch: tuple[bool, int], min_run: int) -> Want:
    if not usable:
        return Want(False, Rule.UNUSABLE, "east thermostat unavailable: radiant is primary")
    if on_peak and policy.peak_primary == "RADIANT":
        return Want(False, Rule.PEAK_YIELDS, "On-peak: radiant holds the band")
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


def guards(want: Wants, snap: Snapshot, mem: Mem, usable: Mapping[Circuit, bool], site: Site
           ) -> dict[Circuit, Want]:
    """Strongest first: blind (leave it to the thermostat), floor (coldest room near 64 turns on
    every usable circuit that serves it, on-peak or not), hi stop."""
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
        elif cold and usable[c]:
            out[c] = Want(True, Rule.FLOOR, f"{cold[0]} coldest room {readings[cold[0]].coldest:.1f}")
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
        if not t.available or (t.mode == "heat" and t.setpoint == site.thermostats[c].setpoint(w.on)):
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


def status(snap: Snapshot, want: Wants, mem: Mem, site: Site, dry_run: bool, policy: Policy, mode: str
           ) -> tuple[str, dict]:
    band = band_at(snap.now, site)
    zones = {z: ({"temp": round(t.value, 1), "coldest": round(t.coldest, 1), "tier": t.tier.value}
                 if isinstance(t, Reading) else {"blind": t.reason}) for z, t in snap.temps.items()}
    circuits = {c: {"on": w.on, "setpoint": site.thermostats[c].setpoint(w.on), "rule": w.rule.value, "why": w.why}
                for c, w in want.items()}
    return (f"{mode} (dry)" if dry_run else mode), {
        "band": {"lo": band.lo, "hi": band.hi}, "tou": snap.tou, "zones": zones, "circuits": circuits,
        "floor_guard": list(mem.floor), "policy": asdict(policy), "outdoor": snap.outdoor_f}
