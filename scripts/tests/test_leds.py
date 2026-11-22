import asyncio
import json
import logging

import pytest

from src import leds_app, zigbee
from src.leds_app import (
    EFFECTS,
    OFF,
    LedsApp,
    Look,
    Request,
    Setting,
    inovelli_color,
    interleave,
    look_for,
    room_of,
    switch_payloads,
)


def device(name, base="zigbee2mqtt-a", ieee=None, model="VZM31-SN", manufacturer="Inovelli"):
    return zigbee.ZigBeeDevice(
        base_topic=base, type="Router", ieee_address=ieee or "0x" + name.encode().hex()[:16],
        network_address=1, friendly_name=name, interview_completed=True, interviewing=False,
        supported=True, manufacturer=manufacturer, model_id=model, state=zigbee.ZigBeeDeviceState(),
    )


class FakeMqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload))

    def sets(self):
        return [(t.split("/")[1], p) for t, p in self.published if t.endswith("/set")]


class FakeZigbee:
    def __init__(self, devices):
        self._devices_by_ieee = {d.ieee_address: d for d in devices}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(leds_app, "PACE_S", 0)
    monkeypatch.setattr(leds_app, "DEBOUNCE_S", 0)


def app(devices):
    a = LedsApp(logger=logging.getLogger("test"), addon_config={}, app_config={})
    a._mqtt = FakeMqtt()
    a._zigbee = FakeZigbee(devices)
    a._reconcile_task = None
    a.sent = {d.ieee_address: OFF for d in devices}
    return a


HOUSE = [device("Kitchen Switch A", ieee="0x1"), device("Kitchen Switch B", ieee="0x2"),
         device("Nook Switch", base="zigbee2mqtt-b", ieee="0x3")]


async def settle(a):
    a._loop = asyncio.get_running_loop()
    for _ in range(3):
        await asyncio.sleep(0)
        if a._reconcile_task:
            await a._reconcile_task


def run(a, *steps):
    async def go():
        a._loop = asyncio.get_running_loop()
        for step in steps:
            step()
            await settle(a)
    asyncio.run(go())


def test_colors_map_to_inovelli_scale():
    assert inovelli_color(0, 100) == 0
    assert inovelli_color(120, 100) == 85
    assert inovelli_color(200, 5) == 255  # unsaturated reads as white


def test_rooms_come_from_switch_names():
    assert room_of(device("Kitchen Switch A")) == "kitchen"
    assert room_of(device("Main Bedroom Switch")) == "main bedroom"
    assert room_of(device("Driveway")) == "driveway"


def test_bar_effect_is_one_command_with_the_chosen_color():
    look = Setting(on=True, effect="chase", hue=120, brightness=40).look()
    assert switch_payloads(look, 3) == [
        {"led_effect": {"effect": "chase", "color": 85, "level": 40, "duration": 255}}
    ]


def test_christmas_pulse_alternates_switches():
    look = look_for("christmas_pulse", 0, 100)
    assert switch_payloads(look, 0)[0]["led_effect"]["color"] == 0
    assert switch_payloads(look, 1)[0]["led_effect"]["color"] == 85


def test_checker_clears_the_bar_then_flips_every_other_switch():
    look = look_for("christmas_checker", 0, 100)
    even, odd = switch_payloads(look, 0), switch_payloads(look, 1)
    assert even[0]["led_effect"]["effect"] == "clear_effect"
    colors = lambda p: [x["individual_led_effect"]["color"] for x in p[1:]]
    assert colors(even) == [0, 85, 0, 85, 0, 85, 0]
    assert colors(odd) == [85, 0, 85, 0, 85, 0, 85]


def test_off_clears_single_leds_only_after_a_per_led_look():
    checker = look_for("christmas_checker", 0, 100)
    assert len(switch_payloads(OFF, 0, previous=checker)) == 8
    assert len(switch_payloads(OFF, 0, previous=None)) == 8
    assert len(switch_payloads(OFF, 0, previous=look_for("pulse", 0, 100))) == 1


def test_merge_ignores_unknown_effects_and_clamps_brightness():
    s = Setting().merge({"state": "ON", "effect": "disco", "brightness": 400})
    assert s.on and s.effect == "solid" and s.brightness == 100


def test_state_payload_round_trips():
    s = Setting(on=True, effect="christmas_chase", hue=30, saturation=80, brightness=55)
    assert Setting().merge(json.loads(json.dumps(s.state_payload()))) == s


def test_request_parse_accepts_ha_or_inovelli_colors_and_rejects_unknown_effects():
    r = Request.parse("x", {"rooms": ["Kitchen"], "effect": "pulse", "color": {"h": 120, "s": 100}}, 0)
    assert r.look == Look("bar", "pulse", 85, 85, 100) and r.rooms == {"kitchen"}
    assert Request.parse("x", {"effect": "solid", "color": 0, "color_b": 85}, 0).look.kind == "alternate"
    with pytest.raises(ValueError):
        Request.parse("x", {"effect": "disco"}, 0)


def test_interleave_alternates_bridges():
    a = [device(f"a{i}") for i in range(3)]
    b = [device("b0", base="zigbee2mqtt-b")]
    assert [d.friendly_name for d in interleave(a + b)] == ["a0", "b0", "a1", "a2"]


def test_only_inovelli_led_switches_are_targeted():
    a = app([device("Kitchen Switch A"), device("Bulb", manufacturer="Signify", model="LCA001"),
             device("Fan", model="VZM35-SN")])
    assert [d.friendly_name for d in a._switches()] == ["Fan", "Kitchen Switch A"]


def test_house_setting_reaches_every_switch_once():
    a = app(HOUSE)
    run(a, lambda: a._set({"state": "ON", "effect": "pulse"}),
        lambda: a._set({"state": "ON", "effect": "pulse"}))
    assert sorted(n for n, _ in a._mqtt.sets()) == ["Kitchen Switch A", "Kitchen Switch B", "Nook Switch"]


def test_startup_with_the_house_off_sends_nothing():
    a = app(HOUSE)
    run(a, lambda: a._schedule_reconcile(0))
    assert a._mqtt.sets() == []


def test_room_request_overrides_house_and_release_restores_it():
    a = app(HOUSE)
    run(a, lambda: a._set({"state": "ON", "effect": "aurora", "color": {"h": 240, "s": 100}}))
    a._mqtt.published.clear()

    run(a, lambda: a.request("health", {"rooms": ["kitchen"], "effect": "fast_blink", "color": 0}))
    sets = a._mqtt.sets()
    assert {n for n, _ in sets} == {"Kitchen Switch A", "Kitchen Switch B"}
    assert all(p["led_effect"]["effect"] == "fast_blink" for _, p in sets)
    a._mqtt.published.clear()

    run(a, lambda: a.release("health"))
    sets = a._mqtt.sets()
    assert {n for n, _ in sets} == {"Kitchen Switch A", "Kitchen Switch B"}
    assert all(p["led_effect"]["effect"] == "aurora" for _, p in sets)


def test_higher_priority_request_wins():
    a = app(HOUSE)
    run(a, lambda: a.request("low", {"effect": "solid", "color": 85, "priority": 10}),
        lambda: a.request("high", {"switches": ["Nook Switch"], "effect": "pulse", "priority": 90}))
    nook = [p for n, p in a._mqtt.sets() if n == "Nook Switch"]
    assert nook[-1]["led_effect"]["effect"] == "pulse"
    assert a.sent["0x1"].color == 85


def test_expired_request_is_dropped(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(leds_app.time, "monotonic", lambda: clock[0])
    a = app(HOUSE)
    run(a, lambda: a.request("blip", {"rooms": ["nook"], "effect": "pulse", "expires_s": 5}))
    assert a.sent["0x3"].effect == "pulse"
    clock[0] = 106.0
    run(a, lambda: a._schedule_reconcile(0))
    assert "blip" not in a.requests and a.sent["0x3"] == OFF


def test_mqtt_request_and_empty_payload_release():
    a = app(HOUSE)

    async def go():
        a._loop = asyncio.get_running_loop()
        a._on_request("scripts/leds/request/test", json.dumps({"rooms": ["Nook"], "effect": "chase"}))
        await asyncio.sleep(0)
        await settle(a)
        assert a.sent["0x3"].effect == "chase"
        a._on_request("scripts/leds/request/test", "")
        await asyncio.sleep(0)
        await settle(a)
        assert a.sent["0x3"] == OFF
        a._on_request("scripts/leds/request/bad", "{\"effect\": \"disco\"}")
        await asyncio.sleep(0)
        assert "bad" not in a.requests

    asyncio.run(go())


def test_new_change_cancels_a_half_sent_rollout(monkeypatch):
    monkeypatch.setattr(leds_app, "PACE_S", 0.005)
    a = app([device(f"S{i:02}", ieee=f"0x{i}") for i in range(20)])

    async def go():
        a._loop = asyncio.get_running_loop()
        a._set({"state": "ON", "effect": "christmas_checker"})
        await asyncio.sleep(0.05)
        a._set({"effect": "pulse"})
        await settle(a)

    asyncio.run(go())
    assert all(look.effect == "pulse" for look in a.sent.values())
    assert len(a._mqtt.sets()) < 20 * 8 + 20  # the checker rollout was cut short


def test_rejoined_switch_gets_its_look_again(monkeypatch):
    monkeypatch.setattr(leds_app, "REJOIN_SETTLE_S", 0)
    a = app(HOUSE)
    run(a, lambda: a._set({"state": "ON", "effect": "aurora"}))
    a._mqtt.published.clear()

    async def go():
        a._loop = asyncio.get_running_loop()
        a._on_rejoin("0x3")
        await asyncio.sleep(0.01)
        await settle(a)

    asyncio.run(go())
    assert [n for n, _ in a._mqtt.sets()] == ["Nook Switch"]


def test_effect_list_has_builtins_and_patterns():
    assert "aurora" in EFFECTS and "christmas_checker" in EFFECTS
    assert len(EFFECTS) == len(set(EFFECTS))
