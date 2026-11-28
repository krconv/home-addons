"""Rates and cost, boiler, heat pump lockout and manual overrides."""
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from src.hvac_controller import cost, sense
from src.hvac_controller.site import SITE
from src.hvac_controller.types import BoilerObs, HeatPumpObs, Mem, Policy, Prices, Rule, TstatObs
from tests.hvac_controller.test_core import DAY, RATES, _state, reading, run, snap

PEAK = datetime(2026, 11, 30, 16, 0)
COLD = {z: reading(67.0) for z in ("EAST", "WEST", "FIRST")}


def test_cost_table_matches_the_context_numbers():
    t = cost.table(Prices(RATES, 3.83, True), SITE.economics)
    assert t["Off-peak"]["HP"] == pytest.approx(14.4, abs=0.1)
    assert t["On-peak"]["HP"] == pytest.approx(48.2, abs=0.1)
    assert t["Off-peak"]["RADIANT"] == pytest.approx(48.4, abs=0.2)
    assert t["On-peak"]["RADIANT"] == pytest.approx(55.2, abs=0.2)


def test_on_peak_keeps_the_heat_pump_on_a_near_tie_and_yields_when_radiant_is_clearly_cheaper():
    out = run(snap(now=PEAK, tou="On-peak", temps=COLD))
    assert out.mem.primary["source"] == "HP" and out.wants["HP"].on
    pricey = Prices({**RATES, "On-peak": 0.90}, 3.83, True)
    out = run(snap(now=PEAK, tou="On-peak", temps=COLD, prices=pricey))
    assert out.mem.primary["source"] == "RADIANT"
    assert out.wants["HP"].rule is Rule.PEAK_YIELDS
    assert out.wants["RAD_WEST"].on and out.wants["RAD_WEST"].rule is Rule.PRIMARY
    # Radiant running: at 0.66 HP is $52 vs radiant $56, inside the 10% margin, so radiant keeps it.
    tie = Prices({**RATES, "On-peak": 0.66}, 3.83, True)
    later = run(snap(now=PEAK + timedelta(minutes=15), tou="On-peak", temps=COLD, prices=tie), out.mem)
    assert later.mem.primary["source"] == "RADIANT" and "keeps" in later.mem.primary["why"]
    fresh = run(snap(now=PEAK, tou="On-peak", temps=COLD, prices=tie))
    assert fresh.mem.primary["source"] == "HP"


def test_missing_rates_disable_economic_switching_not_heat():
    out = run(snap(now=PEAK, tou="On-peak", temps=COLD, prices=Prices({}, 3.83, True)))
    assert out.mem.primary["source"] == "HP" and "no economic switching" in out.mem.primary["why"]
    assert out.wants["HP"].on
    attrs = out.status[1]
    assert attrs["cost"]["missing_rates"] == ["Mid-peak", "Off-peak", "On-peak"]
    assert attrs["cost"]["propane_source"].startswith("default")
    forced = run(snap(now=PEAK, tou="On-peak", temps=COLD, prices=Prices({}, 3.83, True)),
                 policy=Policy(peak_primary="RADIANT"))
    assert forced.mem.primary["source"] == "RADIANT"


def test_sense_reads_prices_and_flags_the_propane_default():
    st = {"input_number.unitil_off_peak_rate": _state(0.18188, DAY), "input_number.unitil_on_peak_rate": _state(0, DAY),
          "input_number.unitil_mid_peak_rate": _state("unknown", DAY)}
    p = sense.read(st, DAY, SITE).prices
    assert p.kwh == {"Off-peak": 0.18188} and p.propane_gal == 3.83 and p.propane_default
    p = sense.read({**st, "input_number.propane_price": _state(2.9, DAY)}, DAY, SITE).prices
    assert p.propane_gal == 2.9 and not p.propane_default


def test_sense_drops_stale_heat_pump_and_ch_evidence():
    st = {"binary_sensor.heat_pump_compressor": _state("off", DAY - timedelta(minutes=10)),
          "sensor.home_power_monitor_heat_pump_power": _state(220.5, DAY),
          "switch.boiler_power": _state("on", DAY - timedelta(days=2)),
          "number.boiler_heating_setpoint": _state(99.5, DAY - timedelta(hours=1))}
    s = sense.read(st, DAY, SITE)
    assert s.hp == HeatPumpObs(None, 220.5) and s.boiler == BoilerObs(True, None)
    st["number.boiler_heating_setpoint"] = _state(99.5, DAY - timedelta(minutes=2))
    assert sense.read(st, DAY, SITE).boiler.ch_f == 99.5


# --- boiler ---------------------------------------------------------------------------------

def test_boiler_off_makes_radiant_unusable_after_a_debounce():
    s = snap(now=DAY, temps={"WEST": reading(64.5)}, boiler=BoilerObs(False, None))
    blip = run(s)
    assert blip.wants["RAD_WEST"].rule is Rule.FLOOR        # a reconnect blip changes nothing
    down = run(replace(s, now=DAY + timedelta(minutes=3)), blip.mem)
    assert down.wants["RAD_WEST"].rule is Rule.UNUSABLE and not down.wants["RAD_WEST"].on
    assert down.wants["HP"].rule is Rule.FLOOR
    assert down.status[1]["unusable"]["RAD_FIRST"] == "boiler off"
    assert down.status[1]["boiler"]["power"] == "off"
    up = run(replace(s, now=DAY + timedelta(minutes=4), boiler=BoilerObs(True, 110.3)), down.mem)
    assert up.wants["RAD_WEST"].rule is Rule.FLOOR
    assert [e["what"] for e in up.events] == ["up"]


@pytest.mark.parametrize("dry", [True, False])
def test_ch_setpoint_is_reasserted_on_fresh_readback_at_most_every_10_min(dry):
    rebooted = snap(boiler=BoilerObs(True, 99.5))
    out = run(rebooted, dry_run=dry)
    assert out.ch == 110.3
    assert run(replace(rebooted, now=DAY + timedelta(minutes=5)), out.mem, dry_run=dry).ch is None
    assert run(replace(rebooted, now=DAY + timedelta(minutes=10)), out.mem, dry_run=dry).ch == 110.3
    assert run(snap(boiler=BoilerObs(True, 110.3)), dry_run=dry).ch is None
    assert run(snap(boiler=BoilerObs(True, None)), dry_run=dry).ch is None      # stale readback
    assert run(snap(boiler=BoilerObs(False, 99.5)), dry_run=dry).ch is None     # never while off


# --- heat pump lockout ----------------------------------------------------------------------

def _east(s, action="heating"):
    return {**s.tstats, "HP": TstatObs(True, "heat", 74, 66.0, action)}


def _ticks(minutes, hp=HeatPumpObs(False, 210.0), mem=Mem(), start=DAY, action="heating", dry_run=True):
    out = None
    for m in minutes:
        s = snap(now=start + timedelta(minutes=m), temps=COLD, hp=hp)
        out = run(replace(s, tstats=_east(s, action)), mem, dry_run=dry_run)
        mem = out.mem
    return out


def test_lockout_needs_ten_continuous_minutes_of_fresh_evidence():
    assert _ticks(range(10)).mem.lockout["state"] == "suspect"
    out = _ticks(range(11))
    assert out.mem.lockout["state"] == "locked"
    assert [e["what"] for e in out.events] == ["locked"]
    late = run(snap(now=DAY + timedelta(minutes=15), temps=COLD), out.mem)
    assert late.wants["HP"].rule is Rule.UNUSABLE and not late.wants["HP"].on
    assert late.wants["RAD_WEST"].rule is Rule.PRIMARY and late.wants["RAD_WEST"].on
    assert late.status[1]["heat_pump"]["lockout"]["state"] == "locked"


def test_unknown_or_a_gap_resets_the_suspect_never_latches():
    mid = _ticks(range(6))
    unknown = _ticks([6], hp=HeatPumpObs(None, 210.0), mem=mid.mem)
    assert unknown.mem.lockout == {}
    assert _ticks(range(7, 16), mem=unknown.mem).mem.lockout["state"] == "suspect"
    gap = _ticks(list(range(5)) + list(range(9, 16)))
    assert gap.mem.lockout["state"] == "suspect"
    assert _ticks(range(12), action="idle").mem.lockout == {}
    assert _ticks(range(12), hp=HeatPumpObs(False, 900.0)).mem.lockout == {}


def test_lockout_clears_only_on_measured_compressor_recovery_and_never_toggles_east():
    locked = _ticks(range(11), dry_run=False)
    still = _ticks([30], hp=HeatPumpObs(None, None), mem=locked.mem, dry_run=False)
    assert still.mem.lockout["state"] == "locked"
    assert all(s.on is False for s in still.switches if s.source == "HP")
    cleared = _ticks([31], hp=HeatPumpObs(True, 2900.0), mem=still.mem, dry_run=False)
    assert cleared.mem.lockout == {} and cleared.events[0]["what"] == "cleared"


# --- manual overrides -----------------------------------------------------------------------

def _live(setpoint, mem, minutes, mode="heat"):
    s = snap(now=DAY + timedelta(minutes=minutes))
    return run(replace(s, tstats={**s.tstats, "RAD_FIRST": TstatObs(True, mode, setpoint, 68.0, "idle")}), mem,
               dry_run=False)


def test_live_override_is_honored_until_it_returns_to_ours_or_the_daily_clear():
    ok = _live(68, Mem(), 0)
    assert "RAD_FIRST" in ok.mem.confirmed
    manual = _live(71, ok.mem, 1)
    assert manual.mem.override["RAD_FIRST"]["setpoint"] == 71
    assert manual.switches == () and manual.events[0]["kind"] == "override"
    assert manual.status[1]["circuits"]["RAD_FIRST"]["override"]["setpoint"] == 71
    assert _live(71, manual.mem, 30).switches == ()
    back = _live(72, manual.mem, 40)
    assert "RAD_FIRST" not in back.mem.override and back.events[0]["what"] == "ended: back to ours"
    off = _live(None, ok.mem, 2, mode="off")
    assert off.mem.override["RAD_FIRST"]["mode"] == "off"
    s = snap(now=datetime(2026, 12, 1, 4, 1))
    next_day = run(replace(s, tstats={**s.tstats, "RAD_FIRST": TstatObs(True, "heat", 71, 68.0, "idle")}), off.mem,
                   dry_run=False)
    assert next_day.mem.override == {} and next_day.events[0]["what"] == "ended: daily clear"
    assert [x.source for x in next_day.switches] == ["RAD_FIRST"]


def test_unconfirmed_or_unknown_values_are_not_overrides():
    assert _live(71, Mem(), 0).mem.override == {}                    # never confirmed: ours gets sent
    ok = _live(68, Mem(), 0)
    assert _live(None, ok.mem, 1).mem.override == {}                 # heat with no setpoint: attrs dropped


def test_dry_run_records_values_no_known_writer_uses():
    s = snap()
    out = run(replace(s, tstats={**s.tstats, "RAD_FIRST": TstatObs(True, "heat", 85, 68.0, "heating")}))
    assert out.events[0]["kind"] == "foreign" and out.mem.foreign["RAD_FIRST"]["setpoint"] == 85
    assert out.mem.override == {}
    again = run(replace(s, now=DAY + timedelta(minutes=1),
                        tstats={**s.tstats, "RAD_FIRST": TstatObs(True, "heat", 85, 68.0, "heating")}), out.mem)
    assert again.events == ()
    baseline = run(replace(s, tstats={**s.tstats, "RAD_FIRST": TstatObs(True, "heat", 64, 68.0, "idle")}), out.mem)
    assert baseline.mem.foreign == {}
