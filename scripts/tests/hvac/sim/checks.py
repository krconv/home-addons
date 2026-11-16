"""Invariants 1-10 as PASS/FAIL with evidence, and per-test signal-quality metrics."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from src.hvac.core.guards import hp_job_to_radiant, peak_active
from src.hvac.core.plan import COLD_HOLD, Plan, baseline, in_comfort, night_of
from src.hvac.core.policy import kill_restore
from src.hvac.core.types import HEATED, Config

from .house import THERMOSTATS

ENTITY_ZONE = {e: z for z, e in THERMOSTATS.items()}
FLOOR_TRUE = 63.5
COMFORT_TRUE = 67.5
GRACE = timedelta(minutes=7)
TICKS2 = timedelta(minutes=2)
SETTLE_MIN = 3  # rows right at a boundary still show the previous segment's call


@dataclass
class Check:
    n: int
    name: str
    ok: Optional[bool]  # None: not applicable
    evidence: str

    @property
    def status(self) -> str:
        return "N/A" if self.ok is None else ("PASS" if self.ok else "FAIL")


def _f(t: datetime) -> str:
    return t.strftime("%a %d %H:%M")


class Ctx:
    def __init__(self, res, plan: Plan, cfg: Config = Config()):
        self.res, self.plan, self.cfg = res, plan, cfg
        self.rows = res.rows
        self.by_t = {r["t"]: r for r in self.rows}
        self.t0, self.t1 = self.rows[0]["t"], self.rows[-1]["t"]
        self.events = [e for e in res.journal if e.get("kind") != "command"]
        self.cmds = [(t, s, d) for t, s, d, ok in res.commands]
        self.segments = [s for s in plan.segments if s.start >= self.t0 and s.start < self.t1]
        self.co2_owned = self._co2_owned()

    def aborted(self, s) -> bool:
        return any(e.get("segment") == s.key for _, e in self.ev("coast_aborted"))

    def ev(self, kind):
        return [(datetime.fromisoformat(e["t"]), e) for e in self.events if e["kind"] == kind]

    def _co2_owned(self):
        spans, start = [], None
        for t, e in self.ev("co2_decay"):
            if e["phase"] == "OFF":
                start = t
            elif e["phase"] in ("DONE", "ABORTED") and start:
                spans.append((start, t))
                start = None
        if start:
            spans.append((start, self.t1))
        return spans

    def row(self, t):
        return self.by_t.get(t.replace(second=0, microsecond=0))

    def seg(self, t):
        return self.plan.at(t)

    def unavailable(self, zone, a, b):
        e = THERMOSTATS[zone]
        return any(x < b and a < y for x, y in self.res.unavailable.get(e, ()))

    def next_boundary(self, t):
        s = self.seg(t)
        if s is not None:
            return s.end
        cands = [x.start for x in self.plan.segments if x.start > t]
        for h, m in ((5, 30), (21, 0)):
            c = t.replace(hour=h, minute=m, second=0)
            cands += [c if c > t else c + timedelta(days=1)]
        return min(cands)

    def killed(self, a, b):
        return any(not r["enabled"] for r in self.rows if a <= r["t"] <= b)


def check_floor(c: Ctx) -> Check:
    worst = min(((r[f"{z}_air"], r["t"], z) for r in c.rows for z in HEATED))
    bad = [(r["t"], z, r[f"{z}_air"]) for r in c.rows for z in HEATED if r[f"{z}_air"] < FLOOR_TRUE]
    ev = f"min {worst[0]:.2f} °F ({worst[2]} {_f(worst[1])})"
    if bad:
        ev = f"first {bad[0][1]} {bad[0][2]:.2f} at {_f(bad[0][0])}; {len(bad)} zone-minutes; " + ev
    return Check(1, "Floor", not bad, ev)


def comfort_violations(c: Ctx):
    out = []
    for r in c.rows:
        if not in_comfort(r["t"]):
            continue
        s = c.seg(r["t"])
        if s is not None and "comfort_exempt" in s.flags:
            continue
        for z in HEATED:
            if r[f"{z}_air"] < COMFORT_TRUE:
                out.append((r["t"], z, r[f"{z}_air"]))
    return out


def _windows(viol):
    """Group (t, zone, v) minutes into per-zone contiguous windows."""
    by = defaultdict(list)
    for t, z, v in viol:
        by[z].append((t, v))
    wins = []
    for z, xs in by.items():
        start, prev, low = xs[0][0], xs[0][0], xs[0][1]
        for t, v in xs[1:]:
            if t - prev > timedelta(minutes=1):
                wins.append((start, prev, z, low))
                start, low = t, v
            prev, low = t, min(low, v)
        wins.append((start, prev, z, low))
    return sorted(wins)


def check_comfort(c: Ctx) -> Check:
    v = comfort_violations(c)
    if not v:
        return Check(2, "Comfort", True, "all heated zones ≥ 67.5 °F 05:30–21:00 outside comfort_exempt")
    w = _windows(v)
    lows = ", ".join(f"{z} {_f(a)}–{b:%H:%M} min {lo:.2f}" for a, b, z, lo in w[:6])
    return Check(2, "Comfort", False, f"{len(v)} zone-minutes in {len(w)} windows: {lows}{' …' if len(w) > 6 else ''}")


def check_peak(c: Ctx) -> Check:
    """Compressor off weekdays 15:00–20:00 unless the segment allows it or a zone hit the floor that peak."""
    floor_days = {t.date() for t, e in c.ev("floor_guard") if e.get("during_peak")}
    bad, excused = [], 0
    for r in c.rows:
        t = r["t"]
        if t.weekday() < 5 and 15 <= t.hour < 20 and r["compressor"]:
            s = c.seg(t)
            if s and "allow_peak_hp" in s.flags:
                continue
            if t.date() in floor_days and any(ft <= t for ft, e in c.ev("floor_guard") if e.get("during_peak") and ft.date() == t.date()):
                excused += 1
                continue
            bad.append(t)
    note = f"; {excused} min after a floor event" if excused else ""
    return Check(3, "Peak", not bad, (f"compressor on at {_f(bad[0])}, {len(bad)} min" if bad else "compressor off every weekday 15:00–20:00") + note)


def _expected(c: Ctx, s, t):
    d = (COLD_HOLD if c.aborted(s) else s.patch)(baseline(s.start))
    if peak_active(t, c.cfg) and "allow_peak_hp" not in s.flags:
        d = hp_job_to_radiant(d, c.cfg.peak_radiant_margin if in_comfort(t) else 0)
    return d


def check_on_time(c: Ctx) -> Check:
    misses, checked, exempt = [], 0, 0
    overrides = c.ev("override")
    for s in c.segments:
        t = s.start + GRACE
        r = c.row(t)
        if r is None or c.killed(s.start, t):
            continue
        d = _expected(c, s, t)
        fb = [x for x, e in c.ev("lockout") if s.start <= x <= t]
        guarded = {("EAST" if e.get("rescue") else e["zone"]) for x, e in c.ev("floor_guard") if s.start <= x <= t}
        for z in HEATED:
            cmd = d.zones[z]
            if (c.unavailable(z, s.start, t) or z in guarded or (fb and z in ("EAST", "WEST", "FIRST"))
                    or any(e["zone"] == z and x <= t for x, e in overrides if x >= s.start - timedelta(hours=12))
                    or (z == "EAST" and s.reactive == "band_ride")):
                exempt += 1
                continue
            checked += 1
            mode, sp = r[f"{z}_mode"], r[f"{z}_sp"]
            if mode != cmd.mode or (cmd.mode == "heat" and sp != cmd.setpoint):
                if z == "WEST" and "EAST" in guarded and mode == "heat" and sp >= cmd.setpoint:
                    continue
                misses.append(f"{s.test_id} {z} {mode}/{sp} want {cmd.mode}/{cmd.setpoint}")
        if not c.unavailable("EAST", s.start, t):
            checked += 1
            if r["east_fan"] != d.east_fan:
                misses.append(f"{s.test_id} fan {r['east_fan']} want {d.east_fan}")
        checked += 1
        want_auto = d.erv_automation
        if r["erv_auto"] != want_auto:
            misses.append(f"{s.test_id} erv_automation {r['erv_auto']} want {want_auto}")
        if d.erv is not None and r["erv_mode"] != d.erv and not (r["co2"] > 1000 and r["erv_mode"] == "Maximum"):
            misses.append(f"{s.test_id} erv {r['erv_mode']} want {d.erv}")
    ev = f"{checked} actuator checks at start+7 min, {exempt} exempt (unavailable/override/guard/fallback/reactive)"
    return Check(4, "On time", not misses, ev + ("; " + "; ".join(misses[:5]) if misses else ""))


def _entity(data):
    return data.get("entity_id")


def check_idempotent(c: Ctx, twin=None) -> Check:
    problems = []
    per_hour = Counter((_entity(d), t.replace(minute=0)) for t, s, d in c.cmds)
    worst = max(per_hour.values(), default=0)
    if worst > 4:
        (e, h), n = per_hour.most_common(1)[0]
        problems.append(f"{n} commands to {e} in hour {_f(h)}")
    by_ent = defaultdict(list)
    for t, s, d in c.cmds:
        by_ent[_entity(d)].append((t, s, d))
    for t, s, d in c.cmds:
        rolling = sum(1 for x, _, _ in by_ent[_entity(d)] if t - timedelta(minutes=60) < x <= t)
        if rolling > 4:
            problems.append(f"{rolling} commands to {_entity(d)} in the 60 min to {_f(t)}")
            break
    last = {}
    for t, s, d in c.cmds:
        k = (s, tuple(sorted(d.items())))
        if k in last and t - last[k] < GRACE:
            problems.append(f"re-sent {s} {d} at {_f(t)} within 7 min")
            break
        last[k] = t
    restart_ev = ""
    if c.res.restart_ticks:
        n = sum(x for _, x in c.res.restart_ticks)
        restart_ev = f"; restart tick emitted {n} commands"
        if n:
            problems.append(f"restart tick emitted {n} commands")
        if twin is not None:
            keys = [k for k in c.rows[0] if k.endswith(("_mode", "_sp", "_air")) or k in ("east_fan", "erv_mode", "erv_auto", "compressor")]
            diff = next((r["t"] for r, q in zip(c.rows, twin.rows) if any(r[k] != q[k] for k in keys)), None)
            restart_ev += "; twin run without restart " + ("identical" if diff is None else f"diverges at {_f(diff)}")
            if diff is not None:
                problems.append(f"restart changed the outcome from {_f(diff)}")
    ev = f"max {worst} commands/entity/clock-hour, {len(c.cmds)} total{restart_ev}"
    return Check(5, "Idempotent", not problems, ev + ("; " + "; ".join(problems) if problems else ""))


def check_override(c: Ctx) -> Check:
    injected = c.res.scenario.faults.overrides
    detected = c.ev("override")
    problems = []
    for t, z, sp in injected:
        hit = [x for x, e in detected if e["zone"] == z and t <= x <= t + timedelta(minutes=12)]
        if not hit:
            problems.append(f"override of {z} at {_f(t)} not detected")
            continue
        end = c.next_boundary(t)
        cmds = [x for x, s, d in c.cmds if ENTITY_ZONE.get(_entity(d)) == z and t <= x < end]
        if cmds:
            problems.append(f"{z} commanded at {_f(cmds[0])} after override, before {_f(end)}")
        voids = c.res.final_states.get("sensor.hvac_lab_status", {}).get("attributes", {}).get("voids", [])
        if not any(v.split(" ")[-1] in (t.date().isoformat(), (t - timedelta(days=1)).date().isoformat()) for v in voids):
            problems.append(f"night of {_f(t)} not marked void (voids={voids})")
    false = [(x, e) for x, e in detected
             if not any(e["zone"] == z and t <= x <= t + timedelta(minutes=12) for t, z, _ in injected)]
    if false:
        x, e = false[0]
        problems.append(f"false override {e['zone']} at {_f(x)} seen={e.get('seen')} expected={e.get('expected')}")
    ev = f"{len(injected)} injected, {len(detected)} detected"
    return Check(6, "Override", not problems, ev + ("; " + "; ".join(problems) if problems else ""))


def check_kill(c: Ctx) -> Check:
    kills = c.res.scenario.faults.kill
    if not kills:
        return Check(7, "Kill switch", None, "no kill switch in scenario")
    problems = []
    for (k, st), nxt in zip(kills, kills[1:] + [(c.t1, "on")]):
        if st != "off":
            continue
        r = c.row(k + TICKS2)
        base = kill_restore(k)
        for z in HEATED:
            if c.unavailable(z, k, k + TICKS2):
                continue
            want = base.zones[z]
            if r[f"{z}_mode"] != want.mode or r[f"{z}_sp"] != want.setpoint:
                problems.append(f"{z} {r[f'{z}_mode']}/{r[f'{z}_sp']} at +2 ticks, want baseline {want.setpoint}")
        if not r["erv_auto"]:
            problems.append("erv automation still off at +2 ticks")
        late = [(x, d) for x, s, d in c.cmds if k + TICKS2 < x < nxt[0]
                and not (ENTITY_ZONE.get(_entity(d)) and c.unavailable(ENTITY_ZONE[_entity(d)], k, k + TICKS2))]
        if late:
            problems.append(f"command after kill at {_f(late[0][0])}: {late[0][1]}")
    return Check(7, "Kill switch", not problems, f"{len([1 for _, s in kills if s == 'off'])} kill(s)" + ("; " + "; ".join(problems) if problems else "; baseline restored, ERV automation on, then silent"))


def check_erv(c: Ctx) -> Check:
    problems = []
    owned = [(s.start, s.end) for s in c.plan.segments if "owns_erv" in s.flags] + c.co2_owned
    for r in c.rows:
        t = r["t"]
        if not r["erv_auto"] and not any(a <= t < b + GRACE for a, b in owned):
            problems.append(f"automation off outside an ERV test at {_f(t)}")
            break
    for r in c.rows:
        if r["co2"] > 1200:
            later = c.row(r["t"] + TICKS2)
            if later and later["erv_mode"] != "Maximum":
                problems.append(f"CO2 {r['co2']} at {_f(r['t'])} but ERV {later['erv_mode']} 2 min later")
                break
    spikes = sum(1 for r in c.rows if r["co2"] > 1200)
    return Check(8, "ERV", not problems, f"{spikes} min with CO2 > 1200" + ("; " + "; ".join(problems) if problems else "; automation off only inside ERV tests; CO2 rule held"))


def check_lockout(c: Ctx) -> Check:
    lk = c.res.scenario.faults.lockout
    events = c.ev("lockout")
    if not lk:
        return Check(9, "Lockout", not events, "no lockout injected; " + (f"false lockout at {_f(events[0][0])}" if events else "no false lockouts"))
    problems = []
    starved = next((r["t"] for r in c.rows if r["t"] >= lk[0] and r["EAST_call"] and r["lockout_active"]), None)
    if starved is None:
        return Check(9, "Lockout", None, "lockout never starved a call")
    if not events:
        return Check(9, "Lockout", False, f"call starved from {_f(starved)} but no lockout event")
    auto = c.res.scenario.lockout_auto_reset
    first = events[0][0]
    limit = c.cfg.lockout_detect_min + 5 + 2  # detect window + one poll + a tick
    if first - starved > timedelta(minutes=limit):
        problems.append(f"detected {(first - starved).seconds // 60} min after starvation (> {limit})")
    resets = [(t, e) for t, e in events if e.get("action") == "reset"]
    if auto:
        nights = Counter(night_of(t) for t, _ in resets)
        if not resets or events[0][1].get("action") != "reset":
            problems.append("auto-reset on but the first lockout did not reset")
        if any(n > 1 for n in nights.values()):
            problems.append(f"more than one reset in a night: {dict(nights)}")
        for t, _ in resets:
            modes = [d.get("hvac_mode") for x, sv, d in c.cmds if _entity(d) == THERMOSTATS["EAST"] and t <= x <= t + timedelta(minutes=4)]
            if modes[:2] != ["off", "heat"]:
                problems.append(f"reset at {_f(t)} sent {modes}, want off then heat")
    elif resets:
        problems.append("reset with auto-reset off")
    fallbacks = [(t, e) for t, e in events if e.get("action") == "fallback"]
    if not fallbacks:
        return Check(9, "Lockout", not problems, f"starved {_f(starved)}; no fallback; " + "; ".join(problems))
    t_ev = fallbacks[0][0]
    s = c.seg(t_ev)
    r = c.row(t_ev + TICKS2)
    tgt = _expected(c, s, t_ev).zones["EAST"].heat_target if s else None
    if r["EAST_mode"] != "off":
        problems.append("East not off 2 ticks after fallback")
    for z in ("WEST", "FIRST"):
        if tgt and (r[f"{z}_sp"] or 0) < tgt and not c.unavailable(z, t_ev, t_ev + TICKS2):
            problems.append(f"{z} {r[f'{z}_sp']} < East target {tgt}")
    end = s.end if s else c.next_boundary(t_ev)
    on_again = [x for x, sv, d in c.cmds if _entity(d) == THERMOSTATS["EAST"] and t_ev + TICKS2 < x < end
                and d.get("hvac_mode") not in (None, "off")]
    if on_again:
        problems.append(f"East re-enabled at {_f(on_again[0])} inside the fallback segment")
    acts = ", ".join(f"{_f(t)} {e.get('action')}" for t, e in events)
    return Check(9, "Lockout", not problems, f"starved {_f(starved)}; events: {acts}" + ("; " + "; ".join(problems) if problems else ""))


def check_unavailable(c: Ctx) -> Check:
    bad = []
    for t, s, d, ok in c.res.commands:
        z = ENTITY_ZONE.get(_entity(d))
        if z and c.unavailable(z, t, t + timedelta(seconds=1)):
            bad.append((t, z))
    n_win = sum(len(v) for v in c.res.unavailable.values())
    return Check(10, "Unavailable", not bad, f"{n_win} unavailable windows" + (f"; command to unavailable {bad[0][1]} at {_f(bad[0][0])}" if bad else "; no commands to unavailable zones (false override/lockout covered by 6/9)"))


def run_checks(res, plan, twin=None) -> list[Check]:
    c = Ctx(res, plan)
    return [check_floor(c), check_comfort(c), check_peak(c), check_on_time(c), check_idempotent(c, twin),
            check_override(c), check_kill(c), check_erv(c), check_lockout(c), check_unavailable(c)]


# ---------------------------------------------------------------- signal quality

def _rows_in(c: Ctx, a, b):
    return [r for r in c.rows if a <= r["t"] < b]


def _slope(rows, key, i, half=30):
    a, b = max(0, i - half), min(len(rows) - 1, i + half)
    if b == a:
        return 0.0
    return (rows[b][key] - rows[a][key]) / ((b - a) / 60.0)


def coast_metrics(c: Ctx, s):
    rows = _rows_in(c, s.start, s.end)
    out = {}
    for z in HEATED:
        first_call = next((i for i, r in enumerate(rows) if i >= SETTLE_MIN and r[f"{z}_call"]), len(rows))
        seg = rows[:max(first_call, 1)]
        decay = seg[0][f"{z}_air"] - min(r[f"{z}_air"] for r in seg)
        hit = first_call < len(rows)
        out[z] = {"decay": round(decay, 2), "ok": decay >= 2.0, "floor_hit": hit,
                  "hours": round(len(seg) / 60, 1)}
    return out


def tau_fit(c: Ctx, s):
    rows = _rows_in(c, s.start, s.end)
    out = {}
    for z in HEATED:
        first_call = next((i for i, r in enumerate(rows) if i >= SETTLE_MIN and r[f"{z}_call"]), len(rows))
        seg = rows[:first_call]
        if len(seg) < 120:
            continue
        to = sum(r["t_out"] for r in seg) / len(seg)
        a, b = seg[0][f"{z}_air"] - to, seg[-1][f"{z}_air"] - to
        if a > 0 and b > 0 and b < a:
            out[z] = (len(seg) / 60) / math.log(a / b)
    return out


def hold_metrics(c: Ctx, s, zones):
    rows = _rows_in(c, s.start, s.end)
    out = {}
    for z in zones:
        steady = sum(1 for i in range(len(rows)) if abs(_slope(rows, f"{z}_air", i)) < 0.2) / 60
        out[z] = {"steady_h": round(steady, 1), "ok": steady >= 2}
    return out


def source_runtime(c: Ctx, s):
    """Runtime of the source under test in a hold: compressor for hp_hold, radiant calls otherwise."""
    rows = _rows_in(c, s.start, s.end)
    if s.test_id == "hp_hold":
        return {"hp_runtime": round(sum(r["compressor"] for r in rows) / len(rows), 3)}
    return {f"{z}_call": round(sum(r[f"{z}_call"] for r in rows) / len(rows), 3) for z in ("WEST", "FIRST", "BASEMENT")}


def recovery_metrics(c: Ctx, s):
    rows = _rows_in(c, s.start, s.start + timedelta(hours=6))
    out = {}
    end = s.start.replace(hour=5, minute=30)
    end = end if end >= s.start else end + timedelta(days=1)
    if s.start.hour >= 12:
        end = s.end
    for z in HEATED:
        hit = next((r["t"] for r in rows if r[f"{z}_air"] >= 68.0), None)
        at_end = c.row(end)
        seg_rows = _rows_in(c, s.start, s.end)
        rise = (seg_rows[-1][f"{z}_air"] - seg_rows[0][f"{z}_air"]) / max(len(seg_rows) / 60, 1e-9)
        out[z] = {"start": round(rows[0][f"{z}_air"], 2), "rise_f_per_h": round(rise, 2),
                  "to68_min": None if hit is None else int((hit - s.start).total_seconds() // 60),
                  "by_0530": hit is not None and hit <= end,
                  "at_0530": round(at_end[f"{z}_air"], 2) if at_end else None}
    return out


def signal_quality(res, plan) -> dict:
    c = Ctx(res, plan)
    out = {"coast": {}, "hold": {}, "recovery": {}, "freefloat": {}, "erv": {}, "prepeak": {}, "band": {},
           "co2_decay": {}, "basement": {}, "aborted": [], "hold_source": {}}
    taus = {}
    for s in c.segments:
        if s.end > c.t1:
            continue
        if s.phase == "coast" and c.aborted(s):
            out["aborted"].append(s.test_id)
            continue
        if s.phase == "coast" and s.test_id != "prepeak_coast":
            out["coast"][s.test_id] = coast_metrics(c, s)
            for z, tau in tau_fit(c, s).items():
                taus.setdefault(z, tau)
        if s.phase == "hold":
            zones = ("EAST",) if s.test_id == "hp_hold" else ("WEST", "FIRST", "BASEMENT")
            out["hold"][s.test_id] = hold_metrics(c, s, zones)
            out["hold_source"][s.test_id] = source_runtime(c, s)
        if s.phase == "recovery":
            out["recovery"][s.test_id] = recovery_metrics(c, s)
        if s.phase in ("erv_off", "erv_max"):
            rows = _rows_in(c, s.start, s.end)
            out["erv"][s.test_id] = {"hp_runtime": round(sum(r["compressor"] for r in rows) / len(rows), 3),
                                     "east_mean": round(sum(r["EAST_air"] for r in rows) / len(rows), 2),
                                     "t_out_mean": round(sum(r["t_out"] for r in rows) / len(rows), 1),
                                     "co2_forced_min": sum(1 for r in rows if r["erv_mode"] == "Maximum" and s.phase == "erv_off")}
        if s.test_id == "prepeak_coast":
            rows = _rows_in(c, s.start, s.end)
            out["prepeak"] = {z: {"min": round(min(r[f"{z}_air"] for r in rows), 2),
                                  "held_floor": min(r[f"{z}_air"] for r in rows) >= FLOOR_TRUE,
                                  "at_end": round(rows[-1][f"{z}_air"], 2)} for z in HEATED}
        if s.reactive == "band_ride":
            rows = _rows_in(c, s.start, s.end)
            sps = [r["EAST_sp"] for r in rows]
            flips = sum(1 for a, b in zip(sps, sps[1:]) if a != b)
            starts = sum(1 for a, b in zip(rows, rows[1:]) if b["compressor"] and not a["compressor"])
            out["band"] = {"setpoint_changes": flips, "cycles": sum(1 for a, b in zip(sps, sps[1:]) if a == 68 and b == 70),
                           "compressor_starts": starts, "min": round(min(r["EAST_air"] for r in rows), 2),
                           "max": round(max(r["EAST_air"] for r in rows), 2),
                           "ok": flips >= 3}
        if s.phase in ("coast", "hold", "erv_off", "erv_max"):
            rows = _rows_in(c, s.start, s.end)
            upstairs = [r for r in rows if r["WEST_call"] or r["FIRST_call"]]
            out["basement"][s.test_id] = {
                "basement_call_min": sum(1 for r in rows if r["BASEMENT_call"]),
                "basement_call_while_upstairs_min": sum(1 for r in upstairs if r["BASEMENT_call"]),
                "upstairs_radiant_min": len(upstairs),
                "incidental_mean_btuh": round(sum(r["basement_incidental_btuh"] for r in rows) / len(rows)),
                "basement_air_max": round(max(r["BASEMENT_air"] for r in rows), 2),
                "basement_sp": rows[0]["BASEMENT_sp"],
            }
    ff = next((s for s in c.segments if s.phase == "freefloat"), None)
    if ff and taus:
        rows = _rows_in(c, ff.start, ff.end)
        res_ff = {}
        for z in HEATED:
            if z not in taus:
                continue
            pred = rows[0][f"{z}_air"]
            rise = []
            for r in rows:
                pred = r["t_out"] + (pred - r["t_out"]) * math.exp(-(1 / 60) / taus[z])
                rise.append(r[f"{z}_air"] - pred)
            res_ff[z] = {"rise_end": round(rise[-1], 2), "rise_max": round(max(rise), 2), "tau_h": round(taus[z], 1)}
        out["freefloat"] = res_ff
    for t, e in c.ev("co2_decay"):
        out["co2_decay"][e["phase"]] = {"t": _f(t), "co2": e.get("co2")}
    return out


def check_dry_run(res, plan: Plan, cfg: Config = Config()) -> Check:
    """dry_run acceptance: no service calls, and the journal holds every intended command.

    Replays the journal into an intended state per actuator and compares it every 30 minutes with
    what the plan wants then (segment patch or baseline, plus the peak guard).
    """
    c = Ctx(res, plan, cfg)
    problems = []
    if res.commands:
        problems.append(f"{len(res.commands)} service calls (first {res.commands[0][1]} at {_f(res.commands[0][0])})")
    jcmds = [e for e in res.journal if e.get("kind") == "command"]
    dry_logs = [m for _, _, m in res.logs if m.startswith("DRY ")]
    if not jcmds:
        problems.append("journal has no commands")
    if len(jcmds) != len(dry_logs):
        problems.append(f"{len(jcmds)} journal commands vs {len(dry_logs)} DRY log lines")
    if any(e.get("dry_run") is not True for e in jcmds):
        problems.append("journal command without dry_run flag")
    intended, k, checked = {}, 0, 0
    t = c.t0 + timedelta(minutes=2)
    while t <= c.t1:
        while k < len(jcmds) and datetime.fromisoformat(jcmds[k]["t"]) <= t:
            intended[jcmds[k]["key"]] = jcmds[k]["value"]
            k += 1
        s = c.seg(t)
        recent = s is not None and t - s.start < timedelta(minutes=2)
        if not recent and t.minute in (0, 30):
            if s is not None:
                d = _expected(c, s, t)
            else:
                d = baseline(t, cfg)
                if peak_active(t, cfg):
                    d = hp_job_to_radiant(d, cfg.peak_radiant_margin if in_comfort(t) else 0)
            for z in HEATED:
                if z == "EAST" and s is not None and s.reactive == "band_ride":
                    continue
                want = d.zones[z].to_json()
                got = intended.get(f"zone:{z}")
                checked += 1
                if got is not None and got != want:
                    problems.append(f"{z} intended {got} at {_f(t)}, plan wants {want}")
                    break
        if len(problems) > 3:
            break
        t += timedelta(minutes=1)
    ev = f"{len(res.commands)} service calls, {len(jcmds)} journalled commands, {checked} intent checks"
    return Check(11, "Dry run", not problems, ev + ("; " + "; ".join(problems[:4]) if problems else ""))
