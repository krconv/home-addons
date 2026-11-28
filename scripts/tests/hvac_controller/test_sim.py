import asyncio
import logging

import pytest

from src.hvac_controller.app import ControllerApp
from tests.hvac.sim import fake_ha
from tests.hvac.sim.house import House
from tests.hvac_controller.sim import (FLOOR, STATUS, CtlScenario, FakeHttp, bounded_problems, capacity_min,
                                       controller_service_calls, room_min, simulate, standing_problems)


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
