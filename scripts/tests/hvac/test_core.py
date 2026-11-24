from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from src.hvac.core.mem import Mem
from src.hvac.core.plan import Plan, baseline, week_plan
from src.hvac.core.policy import decide
from src.hvac.core.types import (OFF, Config, DesiredPatch, Observation, Segment, ZoneCommand, ZoneObs,
                                 heat)

PLAN = week_plan()
CFG = Config()


def zobs(mode="heat", sp=64, temp=66.0, fan="on", available=True, action="idle"):
    if not available:
        return ZoneObs(available=False)
    return ZoneObs(True, mode, sp, None, None, temp, action, fan)


def obs(now, enabled=True, comp=False, erv="Medium", auto=True, co2=600.0, persons=(True, True), **zones):
    z = {"EAST": zobs(), "WEST": zobs(), "FIRST": zobs(), "BASEMENT": zobs(), "GARAGE": zobs(sp=45, temp=55)}
    z.update(zones)
    return Observation(now, z, enabled, comp, erv, auto, co2, persons)


def matching(now, plan=PLAN, mem=None, **kw):
    """Observation in which every zone already shows what decide wants."""
    o = obs(now, **kw)
    cmds, m, _ = decide(now, plan, o, mem or Mem(), CFG)
    zones = dict(o.zones)
    for c in cmds:
        if c.key.startswith("zone:"):
            z = c.key[5:]
            v = c.value
            zones[z] = ZoneObs(True, v.mode, v.setpoint, v.low, v.high, zones[z].temp, "idle", zones[z].fan)
    return Observation(now, zones, o.enabled, o.compressor, o.erv_mode, o.erv_automation, o.co2, o.persons_home), m, cmds


def test_plan_validates_and_rejects_bad_rows():
    assert len(PLAN.segments) == 29
    s = PLAN.segments[0]
    with pytest.raises(ValueError):
        Plan(segments=(s, s))
    bad = Segment(s.start, s.end, "x", "x", DesiredPatch(zones={"WEST": heat(60)}))
    with pytest.raises(ValueError):
        Plan(segments=(bad,))
    with pytest.raises(TypeError):
        ZoneCommand("heat", 64.5)


def test_baseline():
    day = baseline(datetime(2026, 11, 16, 12))
    night = baseline(datetime(2026, 11, 16, 23))
    assert day.zones["WEST"] == heat(CFG.radiant_backup_sp) == heat(66) and day.zones["EAST"] == heat(68)
    assert night.zones["WEST"] == heat(64)
    assert baseline(datetime(2026, 11, 16, 12), Config(radiant_backup_sp=65)).zones["FIRST"] == heat(65)
    assert baseline(datetime(2026, 11, 16, 12)).zones["BASEMENT"] == heat(68)
    assert baseline(datetime(2026, 11, 16, 4)).zones["FIRST"] == heat(68)
    assert day.zones["GARAGE"] == heat(45) and day.erv is None and day.erv_automation


def test_idempotent_and_pending():
    now = datetime(2026, 11, 17, 3, 30)  # radiant_recovery: radiant 68, East off
    o = obs(now)
    cmds, mem, _ = decide(now, PLAN, o, Mem(), CFG)
    keys = {c.key for c in cmds}
    assert keys == {"zone:EAST", "zone:WEST", "zone:FIRST", "zone:BASEMENT"}
    for i in range(1, 7):  # stale HA state for 6 minutes: nothing re-sent, no override
        cmds2, mem, ev = decide(now + timedelta(minutes=i), PLAN, o, mem, CFG)
        assert cmds2 == [] and not [e for e in ev if e.kind == "override"]


def test_confirmed_then_override():
    now = datetime(2026, 11, 17, 3, 30)
    o, mem, _ = matching(now)
    cmds, mem, _ = decide(now + timedelta(minutes=5), PLAN, o, mem, CFG)
    assert cmds == [] and not mem.pending
    zones = dict(o.zones)
    zones["WEST"] = zobs(sp=70)
    o2 = Observation(o.now, zones, True, False, "Medium", True, 600.0, (True, True))
    cmds, mem, ev = decide(now + timedelta(minutes=20), PLAN, o2, mem, CFG)
    assert [e.kind for e in ev] == ["override"] and "WEST" in mem.overrides
    assert not [c for c in cmds if c.key == "zone:WEST"]
    assert mem.grades["night 2026-11-16"]["void"]
    cmds, mem, ev = decide(now + timedelta(minutes=40), PLAN, o2, mem, CFG)
    assert cmds == []


def test_lost_command_is_retried_not_override():
    now = datetime(2026, 11, 17, 3, 30)
    o = obs(now)
    _, mem, _ = decide(now, PLAN, o, Mem(), CFG)
    cmds, mem, ev = decide(now + timedelta(minutes=8), PLAN, o, mem, CFG)
    assert "override" not in [e.kind for e in ev]
    assert {c.key for c in cmds} >= {"zone:WEST"}


def test_unavailable_gets_nothing_and_extends_window():
    now = datetime(2026, 11, 17, 3, 30)
    o = obs(now, WEST=zobs(available=False))
    cmds, mem, _ = decide(now, PLAN, o, Mem(), CFG)
    assert "zone:WEST" not in {c.key for c in cmds}
    o_all = obs(now)
    _, mem, _ = decide(now, PLAN, o_all, Mem(), CFG)
    gone = obs(now, WEST=zobs(available=False))
    _, mem, _ = decide(now + timedelta(minutes=6), PLAN, gone, mem, CFG)
    back = obs(now, WEST=zobs(sp=70))  # still unconfirmed right after coming back: not yet an override
    _, mem, ev = decide(now + timedelta(minutes=9), PLAN, back, mem, CFG)
    assert "override" not in [e.kind for e in ev]


def test_lockout_falls_back():
    now = datetime(2026, 11, 17, 22, 0)  # hp_hold: East 68
    o, mem, _ = matching(now)
    zones = dict(o.zones)
    zones["EAST"] = ZoneObs(True, "heat", 68, None, None, 66.0, "heating", "on")
    called = Observation(now, zones, True, False, "Medium", True, 600.0, (True, True))
    ev_all, sent = [], {}
    for i in range(0, 12):
        cmds, mem, ev = decide(now + timedelta(minutes=i), PLAN, called, mem, CFG)
        ev_all += ev
        sent.update({c.key: c.value for c in cmds})
    assert [e.detail.get("action") for e in ev_all if e.kind == "lockout"] == ["fallback"]
    assert sent["zone:EAST"] == OFF and sent["zone:WEST"] == heat(68) and sent["zone:FIRST"] == heat(68)


def test_kill_switch_restores_once():
    now = datetime(2026, 11, 17, 22, 0)
    o, mem, _ = matching(now)
    off = Observation(now, o.zones, False, False, "Off", False, 600.0, (True, True))
    cmds, mem, _ = decide(now, PLAN, off, mem, CFG)
    assert {c.key for c in cmds} >= {"zone:WEST", "erv_automation"}
    cmds, mem, _ = decide(now + timedelta(minutes=1), PLAN, off, mem, CFG)
    assert cmds == []


def test_peak_guard_moves_hp_job_to_radiant():
    now = datetime(2026, 11, 16, 15, 30)  # Monday, baseline day
    cmds, _, _ = decide(now, PLAN, obs(now), Mem(), CFG)
    sent = {c.key: c.value for c in cmds}
    assert sent["zone:EAST"] == OFF and sent["zone:WEST"] == heat(69)


def test_band_ride_state_machine():
    now = datetime(2026, 11, 22, 9, 0)
    o, mem, cmds = matching(now, EAST=zobs(sp=64, temp=68))
    assert {c.key: c.value for c in cmds}["zone:EAST"] == heat(70)
    zones = dict(o.zones)
    zones["EAST"] = ZoneObs(True, "heat", 70, None, None, 70.0, "idle", "on")
    hot = Observation(now, zones, True, False, "Medium", True, 600.0, (True, True))
    cmds, mem, _ = decide(now + timedelta(minutes=30), PLAN, hot, mem, CFG)
    assert {c.key: c.value for c in cmds}["zone:EAST"] == heat(68)


def test_co2_force_inside_erv_test():
    now = datetime(2026, 11, 19, 22, 0)  # erv_off
    cmds, mem, _ = decide(now, PLAN, obs(now, co2=1300.0, erv="Off", auto=False), Mem(), CFG)
    assert {c.key: c.value for c in cmds}.get("erv") == "Maximum"


def test_lockout_auto_reset_once_then_fallback():
    cfg = Config(lockout_auto_reset=True)
    now = datetime(2026, 11, 17, 22, 0)
    o, mem, _ = matching(now)
    zones = dict(o.zones)
    zones["EAST"] = ZoneObs(True, "heat", 68, None, None, 66.0, "heating", "on")
    called = Observation(now, zones, True, False, "Medium", True, 600.0, (True, True))
    actions, east = [], []
    for i in range(0, 40):
        cmds, mem, ev = decide(now + timedelta(minutes=i), PLAN, called, mem, cfg)
        actions += [e.detail.get("action") for e in ev if e.kind == "lockout"]
        east += [c.value.mode for c in cmds if c.key == "zone:EAST"]
    assert actions == ["reset", "fallback"]
    assert east[:2] == ["off", "heat"] and east[-1] == "off"


def test_restart_converges_from_persisted_mem():
    import json
    now = datetime(2026, 11, 17, 3, 30)
    o, mem, _ = matching(now)
    reloaded = Mem.from_json(json.loads(json.dumps(mem.to_json())))
    cmds, _, ev = decide(now, PLAN, o, reloaded, CFG)
    assert cmds == [] and not [e for e in ev if e.kind == "override"]


def test_cold_coast_runs_a_hold_instead():
    now = datetime(2026, 11, 16, 21, 0)  # coast_1
    o = replace(obs(now), outdoor=12.0)
    cmds, mem, ev = decide(now, PLAN, o, Mem(), CFG)
    assert [e.kind for e in ev if e.kind == "coast_aborted"] == ["coast_aborted"]
    assert {c.key: c.value for c in cmds}["zone:EAST"] == heat(68)
    warm = replace(o, outdoor=30.0)  # decided once, at the segment start
    cmds, mem, ev = decide(now + timedelta(minutes=30), PLAN, warm, mem, CFG)
    assert mem.coast_abort == {PLAN.at(now).key: True}
    mild = replace(obs(now, EAST=zobs(sp=68)), outdoor=20.0)
    cmds, _, _ = decide(now, PLAN, mild, Mem(), CFG)
    assert {c.key: c.value for c in cmds}["zone:EAST"] == heat(64)


def test_dry_run_reconciles_against_intent():
    cfg = Config(dry_run=True)
    now = datetime(2026, 11, 16, 21, 0)
    o = obs(now, EAST=zobs(sp=68), WEST=zobs(sp=66))
    cmds, mem, _ = decide(now, PLAN, o, Mem(), cfg)
    assert {c.key: c.value for c in cmds}["zone:EAST"] == heat(64)
    for i in range(1, 60):  # the device never changes; no resends, retries or overrides
        cmds, mem, ev = decide(now + timedelta(minutes=i), PLAN, o, mem, cfg)
        assert cmds == [] and not [e for e in ev if e.kind in ("override", "not_applied")]
    cmds, mem, _ = decide(datetime(2026, 11, 17, 3, 0), PLAN, o, mem, cfg)  # radiant_recovery
    assert {c.key: c.value for c in cmds}["zone:EAST"] == OFF


def test_weak_radiant_zone_brings_the_heat_pump_back():
    now = datetime(2026, 11, 17, 3, 30)  # radiant_recovery: East off, radiant 68
    o = obs(now, EAST=zobs(mode="off", sp=None, temp=67.0), WEST=zobs(sp=68, temp=64.0, action="heating"))
    cmds, mem, ev = decide(now, PLAN, o, Mem(), CFG)
    assert [e.detail.get("rescue") for e in ev if e.kind == "floor_guard"] == ["heat_pump"]
    assert {c.key: c.value for c in cmds}["zone:EAST"] == heat(CFG.hp_rescue_east_sp)


def test_floor_overrides_peak():
    now = datetime(2026, 11, 17, 16, 0)  # Tuesday on-peak
    o = obs(now, WEST=zobs(sp=69, temp=64.0, action="heating"))
    cmds, mem, ev = decide(now, PLAN, o, Mem(), CFG)
    fl = [e for e in ev if e.kind.startswith("floor")]
    assert [(e.kind, e.detail.get("during_peak")) for e in fl] == [("floor_guard", True)]
    assert {c.key: c.value for c in cmds}["zone:EAST"] == heat(CFG.hp_rescue_east_sp)


def test_floor_at_risk_when_the_heat_pump_is_locked_out():
    now = datetime(2026, 11, 17, 16, 0)
    o = obs(now, WEST=zobs(sp=69, temp=64.0, action="heating"))
    _, m, _ = decide(now, PLAN, obs(now, WEST=zobs(sp=69, temp=66.0)), Mem(), CFG)
    m.lockout = {**m.lockout, "state": "FALLBACK", "since": now.isoformat()}
    later = now + timedelta(minutes=1)
    _, mem, ev = decide(later, PLAN, obs(later, WEST=zobs(sp=69, temp=64.0, action="heating")), m, CFG)
    assert [e.kind for e in ev if e.kind.startswith("floor")] == ["floor_at_risk"]


def test_missing_fan_mode_is_not_an_override():
    now = datetime(2026, 11, 17, 22, 0)
    base = {z: zobs(sp=68 if z == "EAST" else 64) for z in ("EAST", "WEST", "FIRST", "BASEMENT")}
    _, mem, _ = decide(now, PLAN, obs(now, **base), Mem(), CFG)
    later = now + timedelta(minutes=10)
    base["EAST"] = zobs(sp=68, fan=None)
    cmds, mem, ev = decide(later, PLAN, obs(later, **base), mem, CFG)
    assert not [e for e in ev if e.kind == "override"]
    assert not [c for c in cmds if c.key == "east_fan"]


def test_erv_halves_hold_east_at_its_starting_reading():
    start = datetime(2026, 11, 19, 21, 0)
    zones = {z: zobs(sp=68 if z == "EAST" else 64, temp=70.4 if z == "EAST" else 67.0)
             for z in ("EAST", "WEST", "FIRST", "BASEMENT")}
    cmds, mem, _ = decide(start, PLAN, obs(start, **zones), Mem(), CFG)
    assert {c.key: c.value for c in cmds}["zone:EAST"] == heat(70)
    assert mem.holds == {"erv": 70}
    later = datetime(2026, 11, 20, 1, 0)  # erv_max half: same hold even though East has cooled
    zones["EAST"] = zobs(sp=70, temp=68.0)
    _, mem, _ = decide(later, PLAN, obs(later, **zones), mem, CFG)
    assert mem.holds == {"erv": 70}


def test_co2_decay_reruns_in_a_new_window_after_an_abort():
    from src.hvac.core.guards import co2_decay_step
    aborted = {"state": "ABORTED", "since": "2026-11-19T13:30:28", "away_since": None}
    start = datetime(2026, 11, 21, 7, 30)
    s, _ = co2_decay_step(aborted, start, obs(start, persons=(False, False)), PLAN.overlay_at(start), CFG)
    assert s["state"] == "IDLE"
    t = start + timedelta(minutes=31)
    s, ev = co2_decay_step(s, t, obs(t, persons=(False, False)), PLAN.overlay_at(t), CFG)
    assert s["state"] == "OFF" and s["window"] == "co2_decay@2026-11-21T07:00"
    done = dict(s, state="DONE")
    s, _ = co2_decay_step(done, t + timedelta(hours=2), obs(t, persons=(False, False)), PLAN.overlay_at(t), CFG)
    assert s["state"] == "DONE"


def test_co2_cycle_alternates_two_hour_blocks_until_someone_is_home():
    from src.hvac.core.guards import co2_decay_step
    s = {"state": "DONE", "since": "2026-11-21T10:24:33", "away_since": None,
         "window": "co2_decay@2026-11-21T07:00"}
    t = datetime(2026, 11, 22, 9, 0)
    away = lambda t: obs(t, persons=(False, False))
    s, _ = co2_decay_step(s, t, away(t), PLAN.overlay_at(t), CFG)
    t += timedelta(minutes=31)
    s, _ = co2_decay_step(s, t, away(t), PLAN.overlay_at(t), CFG)
    assert (s["state"], s["block"]) == ("OFF", 1) and s["window"].startswith("co2_cycle@")
    phases = []
    for _ in range(6 * 60):
        t += timedelta(minutes=1)
        s, ev = co2_decay_step(s, t, away(t), PLAN.overlay_at(t), CFG)
        phases += [e.detail["phase"] for e in ev]
    assert phases == ["MEDIUM", "OFF", "MEDIUM"] and s["block"] == 4
    s, ev = co2_decay_step(s, t, obs(t, persons=(True, False)), PLAN.overlay_at(t), CFG)
    assert s["state"] == "DONE" and ev[0].detail["reason"] == "home"


def test_co2_cycle_ends_with_its_window():
    from src.hvac.core.guards import co2_decay_step
    t = datetime(2026, 11, 22, 20, 59)
    s = {"state": "MEDIUM", "since": "2026-11-22T20:00:00", "away_since": "2026-11-22T07:00:00",
         "window": "co2_cycle@2026-11-22T05:30", "block": 7}
    t += timedelta(minutes=2)
    s, ev = co2_decay_step(s, t, obs(t, persons=(False, False)), PLAN.overlay_at(t), CFG)
    assert s["state"] == "DONE" and ev[0].detail["reason"] == "window_end"
