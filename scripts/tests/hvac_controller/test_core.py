from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from src.hvac_controller import control, sense
from src.hvac_controller.site import SITE, Site, Thermostat
from src.hvac_controller.types import (CIRCUITS, ZONES, Band, Blind, BoilerObs, HeatPumpObs, Mem, Policy, Prices,
                                       Reading, Rule, Snapshot, Tier, TstatObs)

DAY = datetime(2026, 11, 30, 10, 0)  # Monday, Mid-peak
NIGHT = datetime(2026, 11, 30, 2, 0)
RATES = {"Off-peak": 0.18188, "Mid-peak": 0.18887, "On-peak": 0.60857}
PRICES = Prices(RATES, 3.83, propane_default=True)
HP_IDLE = HeatPumpObs(compressor=False, power_w=5.0)
BOILER_ON = BoilerObs(power=True, ch_f=110.3)


def reading(v: float, coldest: float | None = None) -> Reading:
    return Reading(v, v if coldest is None else coldest, Tier.ROOM, ("r",))


def snap(now=DAY, temps=None, tou="Mid-peak", available=True, enabled=True, setpoint=None, hp=HP_IDLE,
         boiler=BOILER_ON, prices=PRICES) -> Snapshot:
    """Thermostats show their hold unless `setpoint` says otherwise."""
    temps = {z: reading(69.0) for z in ZONES} | (temps or {})
    obs = {c: TstatObs(True, "heat", setpoint or SITE.thermostats[c].hold_f, 68.0, "idle") if available
           else TstatObs(False) for c in CIRCUITS}
    return Snapshot(now, enabled, temps, obs, tou, hp, boiler, prices)


def run(s: Snapshot, mem: Mem = Mem(), dry_run=True, **kw):
    return control.step(s, mem, SITE, dry_run, **kw)


def bounded(out) -> None:
    for c, w in out.wants.items():
        t = SITE.thermostats[c]
        assert t.setpoint(w.on) in (t.hold_f, t.call_f)
        assert 64 <= t.setpoint(w.on) <= 75


def test_band_rejects_lo_under_65():
    with pytest.raises(ValueError):
        Band(64.9, 70)
    with pytest.raises(ValueError):
        Band(68, 68.5)
    assert Band(65, 70).lo == 65


def test_site_rejects_a_hold_that_leaves_the_coldest_room_under_the_floor():
    bad = Site(**{**SITE.__dict__, "thermostats": {
        **SITE.thermostats, "RAD_FIRST": Thermostat("climate.first_floor", 66, 72, 3.0, 0.6)}})
    with pytest.raises(ValueError, match="RAD_FIRST hold"):
        bad.validate()
    SITE.validate()


def test_cold_zone_calls_the_heat_pump_and_names_the_rule():
    out = run(snap(temps={"WEST": reading(67.5)}))
    assert out.wants["HP"].on and out.wants["HP"].rule is Rule.CALL
    assert "WEST" in out.wants["HP"].why
    bounded(out)


def test_floor_dominates_on_peak_and_hi_stop():
    # On-peak with radiant primary policy and EAST hot: FIRST's coldest room at 64.8 still turns on
    # every circuit that serves FIRST.
    s = snap(now=datetime(2026, 11, 30, 16, 0), tou="On-peak",
             temps={"FIRST": reading(66.5, coldest=64.8), "EAST": reading(71.5)})
    out = run(s, policy=Policy(peak_primary="RADIANT"))
    assert out.wants["HP"].rule is Rule.FLOOR and out.wants["HP"].on
    assert out.wants["RAD_FIRST"].rule is Rule.FLOOR and out.wants["RAD_FIRST"].on
    bounded(out)


def test_floor_guard_has_hysteresis():
    out = run(snap(temps={"WEST": reading(65.0)}))
    assert out.wants["RAD_WEST"].rule is Rule.FLOOR
    out2 = run(snap(now=DAY + timedelta(minutes=1), temps={"WEST": reading(65.5)}), out.mem)
    assert out2.wants["RAD_WEST"].rule is Rule.FLOOR
    out3 = run(snap(now=DAY + timedelta(minutes=2), temps={"WEST": reading(66.0)}), out2.mem)
    assert out3.wants["RAD_WEST"].rule is not Rule.FLOOR


def test_hi_stop_turns_a_circuit_off():
    mem = run(snap(temps={"WEST": reading(66.5)})).mem
    out = run(snap(now=DAY + timedelta(minutes=1), temps={"WEST": reading(66.5), "EAST": reading(71.2)}), mem)
    assert not out.wants["HP"].on and out.wants["HP"].rule is Rule.HI_STOP


def test_blind_zone_delegates_to_its_thermostat_hold():
    out = run(snap(temps={"BASEMENT": Blind("x")}))
    assert out.wants["RAD_BASEMENT"].rule is Rule.BLIND and not out.wants["RAD_BASEMENT"].on
    blind_above = {z: Blind("x") for z in ("EAST", "WEST", "FIRST")}
    out = run(snap(temps=blind_above))
    assert all(out.wants[c].rule is Rule.BLIND and not out.wants[c].on for c in ("HP", "RAD_WEST", "RAD_FIRST"))
    bounded(out)


def test_preheat_rides_to_band_hi_before_on_peak():
    out = run(snap(now=datetime(2026, 11, 30, 14, 10), temps={z: reading(69.0) for z in ZONES}))
    assert out.wants["HP"].on and out.wants["HP"].rule is Rule.PREHEAT
    holiday = run(snap(now=datetime(2026, 11, 30, 14, 10), tou="Off-peak"))
    assert not holiday.wants["HP"].on


def test_recover_starts_the_heat_pump_before_0530():
    early = run(snap(now=datetime(2026, 11, 30, 4, 15), temps={z: reading(66.5) for z in ZONES}))
    assert early.wants["HP"].on
    asleep = run(snap(now=NIGHT, temps={z: reading(66.5) for z in ZONES}))
    assert not asleep.wants["HP"].on


def test_one_radiant_at_a_time():
    out = run(snap(now=datetime(2026, 11, 30, 4, 30), temps={z: reading(65.4) for z in ZONES} |
                   {"BASEMENT": reading(66.0)}))
    on = [c for c in ("RAD_WEST", "RAD_FIRST", "RAD_BASEMENT") if out.wants[c].on and out.wants[c].rule is not Rule.FLOOR]
    assert len(on) <= 1


def test_policy_decision_holds_for_the_slot_and_is_idempotent():
    out = run(snap(temps={"WEST": reading(67.5)}))
    later = run(snap(now=DAY + timedelta(minutes=5), temps={"WEST": reading(68.6)}), out.mem)
    assert later.wants["HP"].on and later.switches == ()


def test_unavailable_thermostats_make_circuits_unusable_but_setpoints_stay_bounded():
    out = run(snap(available=False, temps={z: reading(66.0) for z in ZONES}))
    assert not out.wants["HP"].on and out.wants["HP"].rule is Rule.UNUSABLE
    bounded(out)


@pytest.mark.parametrize("hour", range(0, 24, 3))
@pytest.mark.parametrize("t", [63.0, 65.0, 67.0, 69.0, 71.5])
@pytest.mark.parametrize("tou", ["Off-peak", "Mid-peak", "On-peak", None])
def test_bounded_setpoints_in_every_branch(hour, t, tou):
    for policy in (Policy(), Policy(peak_primary="RADIANT")):
        for dry in (True, False):
            out = run(snap(now=DAY.replace(hour=hour), tou=tou, temps={z: reading(t) for z in ZONES}),
                      dry_run=dry, policy=policy)
            bounded(out)
            if t <= 65.0:
                assert all(out.wants[c].on for c in CIRCUITS), out.wants


def test_kill_switch_holds_once_then_hands_off_in_live():
    out = run(snap(enabled=False, setpoint=72), dry_run=False, honor_kill=True)
    assert {s.source for s in out.switches} == set(CIRCUITS) and not any(s.on for s in out.switches)
    again = run(snap(now=DAY + timedelta(minutes=10), enabled=False, setpoint=72), out.mem, dry_run=False,
                honor_kill=True)
    assert again.switches == () and again.status[0] == "disabled"


def test_live_reconcile_waits_for_the_poll_and_caps_sends():
    s = snap(temps={"WEST": reading(67.5)})
    out = run(s, dry_run=False)
    assert [x.source for x in out.switches] == ["HP"]
    soon = run(replace(s, now=DAY + timedelta(minutes=3)), out.mem, dry_run=False)
    assert soon.switches == ()
    polled = run(replace(s, now=DAY + timedelta(minutes=4),
                         tstats={**s.tstats, "HP": TstatObs(True, "heat", 74, 68.0)}), out.mem, dry_run=False)
    assert polled.switches == ()
    busy = Mem(sent={"HP": [(DAY - timedelta(minutes=m)).isoformat() for m in (50, 40, 30, 20)]})
    assert run(s, busy, dry_run=False).switches == ()
    floor = snap(temps={"WEST": reading(64.9)})
    assert "HP" in [x.source for x in run(floor, busy, dry_run=False).switches]


def _state(v, t: datetime, **attrs):
    iso = t.replace(tzinfo=SITE.tz).isoformat()
    return {"state": str(v), "attributes": attrs, "last_changed": iso, "last_updated": iso, "last_reported": iso}


def test_sense_fuses_rooms_prefers_settled_airthings_and_falls_back_to_the_thermostat():
    now = DAY
    st = {
        "sensor.main_bedroom_eye_level_temperature": _state(66.0, now),
        "sensor.south_bedroom_eye_level_temperature": _state(68.0, now),
        "sensor.north_bedroom_eye_level_temperature": _state(67.0, now - timedelta(hours=2)),  # stale
        "sensor.living_room_eye_level_temperature": _state("unavailable", now),
        "sensor.nook_eye_level_temperature": _state(69.0, now),
        "sensor.dining_room_temperature": _state(65.0, now),
        "input_select.airthings_location": _state("Main bedroom", now - timedelta(hours=3)),
        "climate.west_wing": _state("heat", now, temperature=66, current_temperature=67),
        "climate.basement": _state("unavailable", now),
        "sensor.unitil_time_of_use_band": _state("Mid-peak", now),
        "input_boolean.hvac_controller_enabled": _state("on", now),
    }
    s = sense.read(st, now, SITE)
    assert s.temps["EAST"] == Reading(66.5, 65.0, Tier.ROOM, ("Main bedroom", "South bedroom"))
    assert s.temps["WEST"].tier is Tier.THERMOSTAT and s.temps["WEST"].coldest == 66.0
    assert s.temps["FIRST"].rooms == ("Nook",)
    assert isinstance(s.temps["BASEMENT"], Blind)
    assert s.tou == "Mid-peak" and s.enabled
    moving = {**st, "input_select.airthings_location": _state("Main bedroom", now - timedelta(minutes=30))}
    assert sense.read(moving, now, SITE).temps["EAST"].value == 67.0
