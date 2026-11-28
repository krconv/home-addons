"""Boundary contract: sense.read and one dry tick over real recorded Home Assistant states.

The fixture is 2026-11-27 22:00 from the lab archive (fixtures/build_ha_states.py): the boiler
was powered off for a probe calibration with the radiant thermostats at 85, the Airthings sat in
the Nook, and the eye-level templates, rate helpers, TOU band and kill switch weren't archived.
"""
import json
import os
from datetime import datetime, timedelta

from src.hvac_controller import control, sense
from src.hvac_controller.site import SITE
from src.hvac_controller.types import Blind, BoilerObs, Mem, Reading, Tier

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "ha_states.json")
NOW = datetime(2026, 11, 27, 22, 0)


def _states(drop=()):
    with open(FIXTURE) as f:
        dump = json.load(f)
    return {s["entity_id"]: s for s in dump["states"] if s["entity_id"] not in drop}


def test_fixture_covers_every_entity_the_controller_reads():
    with open(FIXTURE) as f:
        dump = json.load(f)
    assert {s["entity_id"] for s in dump["states"]} | set(dump["absent"]) == SITE.read_list()
    assert all(set(s["attributes"]) <= {"temperature", "current_temperature", "hvac_action"} for s in dump["states"])


def test_sense_reads_the_recorded_states_into_expected_tiers():
    s = sense.read(_states(), NOW, SITE)
    tiers = {z: (t.tier, t.rooms) for z, t in s.temps.items() if isinstance(t, Reading)}
    assert tiers == {"EAST": (Tier.THERMOSTAT, ()), "WEST": (Tier.THERMOSTAT, ()),
                     "FIRST": (Tier.ROOM, ("Nook",)), "BASEMENT": (Tier.THERMOSTAT, ())}
    assert s.temps["EAST"].value == 71 - SITE.thermostats["HP"].offset_f
    assert s.tstats["RAD_FIRST"].setpoint == 85 and s.tstats["HP"].action == "fan"
    assert s.boiler == BoilerObs(power=False, ch_f=None)          # CH readback last moved at 11:24
    assert s.hp.compressor is None and round(s.hp.power_w) == 217  # compressor row is from 14:58
    assert s.prices.kwh == {} and s.prices.propane_default
    assert s.tou is None and not s.enabled


def test_missing_data_goes_blind_or_down_a_tier():
    gone = sense.read(_states(drop=("climate.basement",)), NOW, SITE)
    assert isinstance(gone.temps["BASEMENT"], Blind)
    later = sense.read(_states(), NOW + timedelta(minutes=75), SITE)   # the Airthings row goes stale
    assert later.temps["FIRST"].tier is Tier.THERMOSTAT


def test_a_dry_tick_over_the_recording_flags_foreign_setpoints_and_the_boiler():
    states = _states()
    out = control.step(sense.read(states, NOW, SITE), Mem(), SITE, dry_run=True)
    assert sorted(e["circuit"] for e in out.events if e["kind"] == "foreign") == ["RAD_BASEMENT", "RAD_FIRST", "RAD_WEST"]
    assert out.ch is None
    later = control.step(sense.read(states, NOW + timedelta(minutes=3), SITE), out.mem, SITE, dry_run=True)
    assert later.status[1]["unusable"] == {c: "boiler off" for c in ("RAD_WEST", "RAD_FIRST", "RAD_BASEMENT")}
    assert later.status[0] == "rules (dry)"
    json.dumps(later.status[1])
