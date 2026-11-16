"""python -m tests.hvac.sim.run [all|week_nominal|week_cold|faults|sweep], from scripts/

Writes out/<scenario>/{timeline.csv, day-*.png, report.md}. Exits 1 if any invariant FAILs in a
fatal scenario (week_nominal, faults).
"""
from __future__ import annotations

import csv
import os
import sys
from collections import Counter
from dataclasses import replace
from datetime import timedelta

from src.hvac.core.plan import week_plan

from . import scenarios
from .checks import check_dry_run, comfort_violations, Ctx, _windows, run_checks, signal_quality
from .engine import simulate as _simulate
from .engine import simulate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OUT = os.path.join(ROOT, "out")
ZCOLORS = {"EAST": "tab:red", "WEST": "tab:orange", "FIRST": "tab:green", "BASEMENT": "tab:blue", "GARAGE": "tab:gray"}


def write_timeline(res, path):
    plan = week_plan()
    with open(path, "w", newline="") as f:
        w = None
        for r in res.rows:
            s = plan.at(r["t"])
            row = {**r, "t": r["t"].isoformat(timespec="minutes"),
                   "segment": s.test_id if s else "", "phase": s.phase if s else ""}
            if w is None:
                w = csv.DictWriter(f, fieldnames=list(row))
                w.writeheader()
            w.writerow(row)


def plot_days(res, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plan = week_plan()
    days = sorted({r["t"].date() for r in res.rows})
    for day in days:
        rows = [r for r in res.rows if r["t"].date() == day]
        ts = [r["t"] for r in rows]
        fig, ax = plt.subplots(figsize=(15, 6))
        for s in plan.segments:
            if s.end > ts[0] and s.start < ts[-1]:
                color = "khaki" if "comfort_exempt" in s.flags else ("lightblue" if "owns_erv" in s.flags else "lavender")
                ax.axvspan(max(s.start, ts[0]), min(s.end, ts[-1]), color=color, alpha=0.5)
                ax.text(max(s.start, ts[0]), 74.6, s.test_id, fontsize=7, va="top")
        for z, col in ZCOLORS.items():
            if z == "GARAGE":
                continue
            ax.plot(ts, [r[f"{z}_air"] for r in rows], color=col, label=z, lw=1.2)
            ax.step(ts, [r[f"{z}_sp"] if r[f"{z}_mode"] != "off" else float("nan") for r in rows], where="post",
                    color=col, ls="--", lw=0.8)
        ax.axhline(63.5, color="k", lw=0.6, ls=":")
        ax.axhline(67.5, color="k", lw=0.6, ls=":")
        ax.fill_between(ts, 59.4, [59.9 if r["compressor"] else 59.4 for r in rows], color="tab:purple", step="post", label="HP compressor")
        ax.fill_between(ts, 60.1, [60.6 if r["burner"] else 60.1 for r in rows], color="tab:brown", step="post", label="burner")
        ax.set_ylim(59, 75)
        ax.set_ylabel("°F (air, true)")
        ax2 = ax.twinx()
        ax2.plot(ts, [r["t_out"] for r in rows], color="k", lw=0.8, label="outdoor °F")
        ax2.plot(ts, [r["ghi"] / 10 for r in rows], color="gold", lw=0.8, label="GHI/10 W/m²")
        ax2.set_ylabel("outdoor °F, GHI/10")
        ax.legend(loc="upper left", fontsize=7, ncol=4)
        ax2.legend(loc="upper right", fontsize=7)
        ax.set_title(f"{res.scenario.name} {day:%a %Y-%m-%d}")
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, f"day-{day.isoformat()}.png"), dpi=90)
        plt.close(fig)


def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return "\n".join(out)


def report_md(res, checks, sq, out_dir):
    L = [f"# {res.scenario.name}", "", f"Simulated {res.rows[0]['t']:%Y-%m-%d %H:%M} → {res.rows[-1]['t']:%Y-%m-%d %H:%M}.", "",
         "## Invariants", "", _table(["#", "Rule", "Result", "Evidence"], [(c.n, c.name, c.status, c.evidence) for c in checks])]
    v = comfort_violations(Ctx(res, week_plan()))
    if v:
        L += ["", "### Comfort shortfall windows", ""]
        L += [f"- {z} {a:%a %d %H:%M}–{b:%H:%M}, min {lo:.2f} °F" for a, b, z, lo in _windows(v)]
    per = Counter()
    hours = Counter()
    for t, s, d, ok in res.commands:
        per[d.get("entity_id")] += 1
        hours[(d.get("entity_id"), t.replace(minute=0))] += 1
    span_h = max(1.0, (res.rows[-1]["t"] - res.rows[0]["t"]).total_seconds() / 3600)
    L += ["", "## Commands per entity", "",
          _table(["entity", "total", "per hour (mean)", "max in one clock hour"],
                 [(e, n, f"{n / span_h:.3f}", max(c for (x, _), c in hours.items() if x == e)) for e, n in per.most_common()])]
    L += ["", "## Signal quality", ""]
    if sq["coast"]:
        L += ["### Coast (decay before the thermostat first calls; need ≥ 2 °F)", "",
              _table(["test", "zone", "decay °F", "hours", "hit floor setpoint", "ok"],
                     [(t, z, m["decay"], m["hours"], m["floor_hit"], "yes" if m["ok"] else "NO")
                      for t, zs in sq["coast"].items() for z, m in zs.items()]), ""]
    if sq["hold"]:
        L += ["### Hold (hours with |dT/dt| < 0.2 °F/h; need ≥ 2)", "",
              _table(["test", "zone", "steady h", "ok"],
                     [(t, z, m["steady_h"], "yes" if m["ok"] else "NO") for t, zs in sq["hold"].items() for z, m in zs.items()]), ""]
        L += ["Source runtime during each hold (the source under test must run):", "",
              _table(["test", "runtime fractions"], [(t, ", ".join(f"{k} {v}" for k, v in m.items()))
                                                     for t, m in sq["hold_source"].items()]), ""]
    if sq["recovery"]:
        L += ["### Recovery (minutes from segment start to 68 °F true)", "",
              _table(["test", "zone", "start °F", "rise °F/h", "to 68 (min)", "68 by 05:30", "°F at 05:30"],
                     [(t, z, m["start"], m["rise_f_per_h"], m["to68_min"], "yes" if m["by_0530"] else "NO", m["at_0530"])
                      for t, zs in sq["recovery"].items() for z, m in zs.items()]), ""]
    if sq["freefloat"]:
        L += ["### Free-float (rise above the coast prediction, τ from the first coast)", "",
              _table(["zone", "τ h", "rise at end °F", "max rise °F"],
                     [(z, m["tau_h"], m["rise_end"], m["rise_max"]) for z, m in sq["freefloat"].items()]), ""]
    if sq["erv"]:
        L += ["### ERV (heat pump runtime fraction per half)", "",
              _table(["test", "HP runtime", "East mean °F", "outdoor mean °F", "min at Maximum (CO2 forced)"],
                     [(t, m["hp_runtime"], m["east_mean"], m["t_out_mean"], m["co2_forced_min"]) for t, m in sq["erv"].items()]), ""]
    if sq["prepeak"]:
        L += ["### Pre-peak coast", "",
              _table(["zone", "min °F", "held floor", "°F at 20:00"],
                     [(z, m["min"], m["held_floor"], m["at_end"]) for z, m in sq["prepeak"].items()]), ""]
    if sq["band"]:
        b = sq["band"]
        L += ["### Band ride", "", f"East setpoint changes {b['setpoint_changes']}, full 68→70 cycles {b['cycles']}, "
              f"compressor starts {b['compressor_starts']}, air {b['min']}–{b['max']} °F, usable {'yes' if b['ok'] else 'NO'}", ""]
    if sq["aborted"]:
        L += ["### Coasts replaced by a hold (outdoor < 15 °F at start)", "", ", ".join(sq["aborted"]), ""]
    if sq["co2_decay"]:
        L += ["### CO2 decay", ""] + [f"- {p}: {m['t']} CO2 {m['co2']}" for p, m in sq["co2_decay"].items()] + [""]
    if sq["basement"]:
        L += ["### Basement during coast/hold tests (boiler and manifolds live there)", "",
              "Calls while upstairs radiant runs mean the basement is being heated by the boiler room, not controlled by its own call.", "",
              _table(["test", "basement sp", "basement call min", "…while upstairs radiant runs", "upstairs radiant min",
                      "incidental BTU/h (mean)", "basement air max"],
                     [(t, m["basement_sp"], m["basement_call_min"], m["basement_call_while_upstairs_min"],
                       m["upstairs_radiant_min"], m["incidental_mean_btuh"], m["basement_air_max"]) for t, m in sq["basement"].items()]), ""]
    with open(os.path.join(out_dir, "report.md"), "w") as f:
        f.write("\n".join(L) + "\n")


def run_one(sc, twin=False):
    out_dir = os.path.join(OUT, sc.name)
    res = simulate(sc, out_dir)
    twin_res = None
    if twin and sc.faults.restarts:
        twin_res = simulate(sc, os.path.join(out_dir, "twin_no_restart"), with_restarts=False)
    plan = week_plan()
    checks = run_checks(res, plan, twin_res)
    sq = signal_quality(res, plan)
    write_timeline(res, os.path.join(out_dir, "timeline.csv"))
    if sc.plots:
        plot_days(res, out_dir)
    report_md(res, checks, sq, out_dir)
    return res, checks, sq


def _sq_fails(sq):
    f = []
    for t, zs in sq["coast"].items():
        f += [f"{t} {z} decay {m['decay']}" for z, m in zs.items() if not m["ok"]]
    for t, zs in sq["hold"].items():
        f += [f"{t} {z} steady {m['steady_h']}h" for z, m in zs.items() if not m["ok"]]
    for t, zs in sq["recovery"].items():
        f += [f"{t} {z} @05:30 {m['at_0530']}" for z, m in zs.items() if not m["by_0530"]]
    f += [f"prepeak {z} min {m['min']}" for z, m in sq["prepeak"].items() if not m["held_floor"]]
    if sq["band"] and not sq["band"]["ok"]:
        f.append(f"band_ride {sq['band']['setpoint_changes']} setpoint changes")
    f += [f"{t} source idle" for t, m in sq["hold_source"].items() if max(m.values()) < 0.05]
    return f


def run_sweep():
    out_dir = os.path.join(OUT, "sweep")
    os.makedirs(out_dir, exist_ok=True)
    lines = ["# sweep", "", "Each row is the week_nominal schedule with one prior moved (or all moved to a corner).", "",
             "| case | invariant FAILs | signal-quality misses |", "|---|---|---|"]
    for sc in scenarios.sweep():
        res, checks, sq = run_one(sc)
        fails = [f"{c.n} {c.name}: {c.evidence[:160]}" for c in checks if c.ok is False]
        sqf = _sq_fails(sq)
        lines.append(f"| {sc.label} | {'<br>'.join(fails) or 'none'} | {'<br>'.join(sqf[:8]) + (' …' if len(sqf) > 8 else '') or 'none'} |")
        print(f"  {sc.label:45s} inv_fail={len(fails)} sq_miss={len(sqf)}")
    with open(os.path.join(out_dir, "report.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


def run_dry():
    sc = replace(scenarios.week_nominal(), name="week_nominal_dry", dry_run=True, plots=False)
    res = _simulate(sc, os.path.join(OUT, sc.name))
    chk = check_dry_run(res, week_plan())
    with open(os.path.join(OUT, sc.name, "report.md"), "w") as f:
        f.write(f"# {sc.name}\n\n| # | Rule | Result | Evidence |\n|---|---|---|---|\n| {chk.n} | {chk.name} | {chk.status} | {chk.evidence} |\n")
    return chk


def main(argv):
    which = argv[1] if len(argv) > 1 else "all"
    names = ["week_nominal", "week_nominal_dry", "week_cold", "faults", "faults_autoreset", "sweep"] if which == "all" else [which]
    fatal = []
    for n in names:
        print(f"== {n}")
        if n == "sweep":
            run_sweep()
            continue
        if n == "week_nominal_dry":
            chk = run_dry()
            print(f"  {chk.n:2d} {chk.name:12s} {chk.status:4s} {chk.evidence[:150]}")
            if chk.ok is False:
                fatal.append(n)
            continue
        sc = scenarios.SCENARIOS[n]()
        res, checks, sq = run_one(sc, twin=True)
        for c in checks:
            print(f"  {c.n:2d} {c.name:12s} {c.status:4s} {c.evidence[:150]}")
        if sc.fatal and any(c.ok is False for c in checks):
            fatal.append(n)
    if fatal:
        print(f"FAIL: invariant failures in {', '.join(fatal)}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
