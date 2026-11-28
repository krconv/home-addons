import asyncio
import logging
from datetime import date, datetime, timedelta

import pytest

from src.hvac_controller.app import ControllerApp
from tests.hvac.sim import fake_ha
from tests.hvac.sim.house import House
from tests.hvac_controller.sim import (FLOOR, START, STATUS, TARIFF, CtlScenario, FakeHttp, bill, bounded_problems,
                                       capacity_min, controller_service_calls, room_min, simulate, standing_problems)


@pytest.fixture(scope="module")
def shadow(tmp_path_factory):
    return simulate(CtlScenario("shadow_twin", mode="shadow", hours=48), str(tmp_path_factory.mktemp("shadow")))


def test_dry_run_holds_no_sender(tmp_path):
    app = ControllerApp(logging.getLogger("t"), {}, {}, http=FakeHttp(None, dry_run_spy=True),
                         state_dir=str(tmp_path))
    assert app.dry_run and app.sender is None


def test_spy_refuses_any_dry_run_service_call():
    world = fake_ha.World(now=None, house=House())
    http = FakeHttp(world, dry_run_spy=True)
    with pytest.raises(AssertionError):
        asyncio.run(http.post("/services/climate/set_temperature", {"entity_id": "climate.east_wing"}))
    with pytest.raises(AssertionError):
        asyncio.run(http.post("/states/sensor.hvac_lab_status", {"state": "x", "attributes": {}}))
    assert len(http.violations) == 2 and not world.commands


def test_shadow_twin_controller_writes_only_its_status(shadow):
    assert shadow.violations == []
    assert {p for _, p, _ in shadow.posts} == {f"/states/{STATUS}"}
    assert len(shadow.posts) == len(shadow.rows)
    assert shadow.world_commands, "the live baseline kept heating"
    assert shadow.rows[-1]["status"] == "rules (dry)"


def test_shadow_twin_journals_intents_with_rule_and_divergence(shadow):
    intents = [j for j in shadow.journal if j["kind"] == "intent"]
    assert intents and all(j["dry_run"] and j["rule"] and "diverges" in j for j in intents)
    assert any(j["diverges"] for j in intents)
    assert all(j["setpoint"] in (66, 68, 72, 74) and j["mode"] == "heat" for j in intents)
    assert sum(j["kind"] == "sample" for j in shadow.journal) == 48 * 4


@pytest.mark.parametrize("name,offset", [("live_nominal", 0.0), ("live_cold", -25.0)])
def test_live_controller_holds_the_floor_with_bounded_setpoints(tmp_path, name, offset):
    sc = CtlScenario(name, temp_offset=offset, hours=48, forks=6)
    res = simulate(sc, str(tmp_path))
    cap = capacity_min(sc)
    lows = room_min(res.rows)
    assert controller_service_calls(res)
    assert bounded_problems(res) == []
    assert standing_problems(res.rows) == []
    assert {z: v for z, v in lows.items() if cap[z] >= FLOOR and v < FLOOR} == {}, (lows, cap)
    # fork_and_freeze: the app dies at sampled ticks; 12 h on standing setpoints alone.
    assert len(res.forks) == 6
    for t, rows in res.forks:
        frozen = room_min(rows, settle_min=0)
        assert {z: v for z, v in frozen.items() if cap[z] >= FLOOR and v < FLOOR} == {}, (t, frozen)
        assert standing_problems([{"t": t, "tstats": {}}] * 60 + rows) == []


# --- day 2: lockout, boiler, rates ---------------------------------------------------------

MON = date(2026, 11, 30)


def _on_peak(res, day=None):
    return [x for x in res.rows if x["band"] == "On-peak" and (day is None or x["t"].date() == day)]


def test_lockout_hands_the_house_to_radiant_without_toggling_east(tmp_path):
    sc = CtlScenario("lockout", hours=36, lockout=(2, 20))
    res = simulate(sc, str(tmp_path))
    events = [(j["t"], j["what"]) for j in res.journal if j["kind"] == "lockout"]
    assert [w for _, w in events] == ["locked", "cleared"]
    locked_at, cleared_at = (datetime.fromisoformat(t) for t, _ in events)
    assert locked_at <= sc.start + timedelta(hours=2, minutes=20) and cleared_at >= sc.start + timedelta(hours=20)
    during = [x for x in res.rows if locked_at + timedelta(minutes=15) <= x["t"] < cleared_at]
    assert during and all(x["attrs"]["circuits"]["HP"]["rule"] == "policy.unusable" for x in during)
    assert all(x["attrs"]["primary"]["source"] == "RADIANT" for x in during)
    assert bounded_problems(res) == [] and standing_problems(res.rows) == []   # East never leaves heat
    cap, lows = capacity_min(sc), room_min(res.rows)
    assert all(lows[z] >= min(FLOOR, cap[z]) - 0.5 for z in lows), (lows, cap)


def test_boiler_reboot_is_reasserted_live_and_journaled_dry(tmp_path):
    live = simulate(CtlScenario("reboot_live", hours=8, boiler_reboot_h=3), str(tmp_path / "live"))
    writes = [(t, b) for t, p, b in controller_service_calls(live) if p == "/services/number/set_value"]
    reboot = START + timedelta(hours=3)
    assert len(writes) == 1 and reboot < writes[0][0] <= reboot + timedelta(minutes=2), writes
    assert live.rows[-1]["ch_setpoint"] == 110.3 and bounded_problems(live) == []
    dry = simulate(CtlScenario("reboot_dry", mode="shadow", hours=8, boiler_reboot_h=3), str(tmp_path / "dry"))
    assert dry.violations == [] and {p for _, p, _ in dry.posts} == {f"/states/{STATUS}"}
    intents = [j for j in dry.journal if j.get("rule") == "boiler.reassert_ch"]
    assert len(intents) == 1 and intents[0]["dry_run"] and intents[0]["observed"] == 99.5
    assert dry.rows[-1]["ch_setpoint"] == 99.5


@pytest.fixture(scope="module")
def peak(tmp_path_factory):
    pricey = {**TARIFF, "On-peak": 0.90}
    runs = {"default": CtlScenario("peak_default"), "pricey": CtlScenario("peak_pricey", tariff=pricey),
            "holiday": CtlScenario("peak_holiday", tariff=pricey, holidays=(MON,)),
            "missing": CtlScenario("rates_missing", tariff=pricey, rate_helpers=False)}
    return {k: simulate(sc, str(tmp_path_factory.mktemp(k))) for k, sc in runs.items()}


def test_on_peak_source_follows_delivered_cost(peak):
    assert {x["attrs"]["primary"]["source"] for x in _on_peak(peak["default"])} == {"HP"}
    pricey = _on_peak(peak["pricey"])
    assert {x["attrs"]["primary"]["source"] for x in pricey} == {"RADIANT"}
    assert {x["attrs"]["circuits"]["HP"]["rule"] for x in pricey} <= {"policy.peak_yields", "guard.floor"}
    assert _on_peak(peak["holiday"], MON) == [] and _on_peak(peak["holiday"])
    assert bill(peak["pricey"])["total"] < bill(peak["missing"])["total"]
    for res in peak.values():
        assert bounded_problems(res) == [] and standing_problems(res.rows) == []
        assert {z: v for z, v in room_min(res.rows).items() if v < FLOOR - 0.5} == {}


def test_missing_rates_keep_the_heat_pump_on_peak(peak):
    rows = _on_peak(peak["missing"])
    assert all(x["attrs"]["primary"]["source"] == "HP" and "no economic switching" in x["attrs"]["primary"]["why"]
               for x in rows)
    assert any(x["attrs"]["circuits"]["HP"]["on"] for x in rows)
    assert rows[-1]["attrs"]["cost"]["missing_rates"] == ["Mid-peak", "Off-peak", "On-peak"]
