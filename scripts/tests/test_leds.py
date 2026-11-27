import asyncio
import json
import logging

import pytest

from src import leds_app, zigbee
from src.leds_app import (EFFECTS, OFF, LedsApp, Look, Request, Setting, indicator,
                          inovelli_color, interleave, look_for, notification,
                          notification_payloads, room_of)

FACTORY = indicator(OFF, 0)


def device(name, base="zigbee2mqtt-a", ieee=None, model="VZM31-SN", manufacturer="Inovelli",
           props=None):
    return zigbee.ZigBeeDevice(
        base_topic=base, type="Router", ieee_address=ieee or "0x" + name.encode().hex()[:16],
        network_address=1, friendly_name=name, interview_completed=True, interviewing=False,
        supported=True, manufacturer=manufacturer, model_id=model,
        state=zigbee.ZigBeeDeviceState(properties=dict(FACTORY if props is None else props)),
    )


class FakeMqtt:
    """Records publishes; a set on a switch is echoed into its reported state, like Zigbee2MQTT."""

    def __init__(self, devices=()):
        self.published = []
        self.by_name = {d.friendly_name: d for d in devices}

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload))
        parts = topic.split("/")
        if len(parts) == 3 and parts[2] == "set" and parts[1] in self.by_name:
            echo = {k: v for k, v in payload.items() if isinstance(v, int)}
            self.by_name[parts[1]].state.properties.update(echo)

    def sets(self):
        return [(t.split("/")[1], p) for t, p in self.published if t.endswith("/set")]

    def clear(self):
        self.published.clear()


class FakeZigbee:
    def __init__(self, devices):
        self._devices_by_ieee = {d.ieee_address: d for d in devices}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(leds_app, "PACE_S", 0)
    monkeypatch.setattr(leds_app, "DEBOUNCE_S", 0)


@pytest.fixture
def make_app(tmp_path):
    def make(devices, known=True):
        a = LedsApp(logger=logging.getLogger("test"), addon_config={}, app_config={},
                    state_file=str(tmp_path / "leds" / "notifications.json"))
        a._mqtt = FakeMqtt(devices)
        a._zigbee = FakeZigbee(devices)
        a._reconcile_task = None
        if known:
            a.notifications = {d.ieee_address: None for d in devices}
        return a
    return make


def house():
    return [device("Kitchen Switch A", ieee="0x1"), device("Kitchen Switch B", ieee="0x2"),
            device("Nook Switch", base="zigbee2mqtt-b", ieee="0x3")]


async def settle(a):
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


def keys(sets):
    return {k for _, p in sets for k in p}


# Pure functions.

def test_colors_map_to_inovelli_scale():
    assert inovelli_color(0, 100) == 0
    assert inovelli_color(120, 100) == 85
    assert inovelli_color(200, 5) == 255  # unsaturated reads as white


def test_rooms_come_from_switch_names():
    assert room_of(device("Kitchen Switch A")) == "kitchen"
    assert room_of(device("Main Bedroom Switch")) == "main bedroom"
    assert room_of(device("Driveway")) == "driveway"


def test_off_is_the_factory_indicator():
    assert FACTORY["ledColorWhenOn"] == FACTORY["ledColorWhenOff"] == 170
    assert (FACTORY["ledIntensityWhenOn"], FACTORY["ledIntensityWhenOff"]) == (33, 1)
    assert FACTORY["defaultLed3ColorWhenOn"] == 255 and FACTORY["defaultLed3IntensityWhenOff"] == 101
    assert notification(OFF, 0) is None


def test_solid_is_indicator_only_and_follows_brightness():
    look = Setting(on=True, effect="solid", hue=120, brightness=40).look()
    p = indicator(look, 0)
    assert (p["ledColorWhenOn"], p["ledColorWhenOff"]) == (85, 85)
    assert (p["ledIntensityWhenOn"], p["ledIntensityWhenOff"]) == (40, 1)
    assert notification(look, 0) is None


def test_animation_sets_the_indicator_to_the_same_color():
    look = look_for("pulse", 170, 60)
    assert indicator(look, 0)["ledColorWhenOn"] == 170
    assert notification(look, 0) == ("pulse", 170, 60)


def test_christmas_pulse_alternates_switches():
    look = look_for("christmas_pulse", 0, 100)
    assert notification(look, 0)[1] == 0 and notification(look, 1)[1] == 85
    assert indicator(look, 0)["ledColorWhenOn"] == 0 and indicator(look, 1)["ledColorWhenOn"] == 85


def test_checker_uses_per_led_colors_flipped_on_every_other_switch():
    look = look_for("christmas_checker", 0, 100)
    colors = lambda i: [indicator(look, i)[f"defaultLed{n}ColorWhenOn"] for n in range(1, 8)]
    assert colors(0) == [0, 85, 0, 85, 0, 85, 0]
    assert colors(1) == [85, 0, 85, 0, 85, 0, 85]
    assert notification(look, 0) is None


def test_notification_payloads():
    assert notification_payloads(None, None) == []
    assert notification_payloads(None, ("pulse", 0, 100))[0]["led_effect"]["effect"] == "clear_effect"
    assert len(notification_payloads(None, "unknown")) == 8  # bar and every single LED
    assert notification_payloads(("chase", 0, 50), None)[0]["led_effect"]["duration"] == 255


def test_merge_ignores_unknown_effects_and_clamps_brightness():
    s = Setting().merge({"state": "ON", "effect": "disco", "brightness": 400})
    assert s.on and s.effect == "solid" and s.brightness == 100


def test_state_payload_round_trips():
    s = Setting(on=True, effect="christmas_chase", hue=30, saturation=80, brightness=55)
    assert Setting().merge(json.loads(json.dumps(s.state_payload()))) == s


def test_request_parse():
    r = Request.parse("x", {"rooms": ["Kitchen"], "effect": "pulse", "color": {"h": 120, "s": 100}}, 0)
    assert r.look == Look("bar", "pulse", 85, 85, 100) and r.rooms == {"kitchen"}
    assert Request.parse("x", {"color": 0, "color_b": 85}, 0).look.kind == "alternate"
    with pytest.raises(ValueError):
        Request.parse("x", {"effect": "disco"}, 0)


def test_interleave_alternates_bridges():
    a = [device(f"a{i}") for i in range(3)]
    b = [device("b0", base="zigbee2mqtt-b")]
    assert [d.friendly_name for d in interleave(a + b)] == ["a0", "b0", "a1", "a2"]


# The app.

def test_only_inovelli_led_switches_are_targeted(make_app):
    a = make_app([device("Kitchen Switch A"), device("Bulb", manufacturer="Signify", model="LCA001"),
                  device("Fan", model="VZM35-SN")])
    assert [d.friendly_name for d in a._switches()] == ["Fan", "Kitchen Switch A"]


def test_startup_matching_the_switches_sends_nothing(make_app):
    a = make_app(house())
    run(a, lambda: a._schedule_reconcile(0))
    assert a._mqtt.sets() == []


def test_first_run_clears_notifications_once(make_app):
    a = make_app(house(), known=False)
    run(a, lambda: a._schedule_reconcile(0))
    assert len(a._mqtt.sets()) == 3 * 8
    a._mqtt.clear()
    run(a, lambda: a._schedule_reconcile(0))
    assert a._mqtt.sets() == []
    saved = json.load(open(a.state_file))
    assert saved == {"0x1": None, "0x2": None, "0x3": None}


def test_solid_color_writes_only_changed_parameters_one_per_message(make_app):
    a = make_app(house())
    run(a, lambda: a._set({"state": "ON", "effect": "solid", "color": {"h": 0, "s": 100},
                           "brightness": 33}))
    sets = a._mqtt.sets()
    assert len(sets) == 6
    assert all(len(p) == 1 for _, p in sets)
    assert {k for _, p in sets for k in p} == {"ledColorWhenOn", "ledColorWhenOff"}


def test_brightness_change_is_one_write_per_switch(make_app):
    a = make_app(house())
    run(a, lambda: a._set({"state": "ON", "color": {"h": 0, "s": 100}}))
    a._mqtt.clear()
    run(a, lambda: a._set({"brightness": 10}))
    assert all(p == {"ledIntensityWhenOn": 10} for _, p in a._mqtt.sets())


def test_animation_sets_indicator_then_notification_and_off_restores_both(make_app):
    a = make_app(house())
    run(a, lambda: a._set({"state": "ON", "effect": "chase", "color": {"h": 120, "s": 100}}))
    nook = [p for n, p in a._mqtt.sets() if n == "Nook Switch"]
    assert nook[0] == {"ledColorWhenOn": 85} and nook[-1]["led_effect"]["effect"] == "chase"
    a._mqtt.clear()
    run(a, lambda: a._set({"state": "OFF"}))
    nook = [p for n, p in a._mqtt.sets() if n == "Nook Switch"]
    assert nook[0] == {"ledColorWhenOn": 170}
    assert nook[-1]["led_effect"]["effect"] == "clear_effect" and len(nook) == 4


def test_room_request_overrides_house_and_release_restores_it(make_app):
    a = make_app(house())
    run(a, lambda: a._set({"state": "ON", "color": {"h": 240, "s": 100}}))
    a._mqtt.clear()
    run(a, lambda: a.request("health", {"rooms": ["kitchen"], "effect": "fast_blink", "color": 0}))
    assert {n for n, _ in a._mqtt.sets()} == {"Kitchen Switch A", "Kitchen Switch B"}
    a._mqtt.clear()
    run(a, lambda: a.release("health"))
    sets = a._mqtt.sets()
    assert {n for n, _ in sets} == {"Kitchen Switch A", "Kitchen Switch B"}
    assert {"ledColorWhenOn", "led_effect"} <= keys(sets)


def test_higher_priority_request_wins(make_app):
    a = make_app(house())
    run(a, lambda: a.request("low", {"color": 85, "priority": 10}),
        lambda: a.request("high", {"switches": ["Nook Switch"], "effect": "pulse", "priority": 90}))
    assert a.notifications["0x3"][0] == "pulse"
    assert a.written["0x1"]["ledColorWhenOn"] == 85


def test_expired_request_is_dropped(make_app, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(leds_app.time, "monotonic", lambda: clock[0])
    a = make_app(house())
    run(a, lambda: a.request("blip", {"rooms": ["nook"], "effect": "pulse", "expires_s": 5}))
    assert a.notifications["0x3"][0] == "pulse"
    clock[0] = 106.0
    run(a, lambda: a._schedule_reconcile(0))
    assert "blip" not in a.requests and a.notifications["0x3"] is None


def test_mqtt_request_and_empty_payload_release(make_app):
    a = make_app(house())

    async def go():
        a._loop = asyncio.get_running_loop()
        a._on_request("scripts/leds/request/test", json.dumps({"rooms": ["Nook"], "effect": "chase"}))
        await asyncio.sleep(0)
        await settle(a)
        assert a.notifications["0x3"][0] == "chase"
        a._on_request("scripts/leds/request/test", "")
        await asyncio.sleep(0)
        await settle(a)
        assert a.notifications["0x3"] is None
        a._on_request("scripts/leds/request/bad", "{\"effect\": \"disco\"}")
        await asyncio.sleep(0)
        assert "bad" not in a.requests

    asyncio.run(go())


def test_drift_is_put_back_on_resync(make_app):
    a = make_app(house())
    run(a, lambda: a._set({"state": "ON", "color": {"h": 0, "s": 100}}))
    a._mqtt.clear()
    # Someone changes a switch in the Zigbee2MQTT UI; the switch reports it.
    a._zigbee._devices_by_ieee["0x2"].state.properties["ledColorWhenOn"] = 42
    a.written.clear()
    run(a, lambda: a._schedule_reconcile(0))
    assert a._mqtt.sets() == [("Kitchen Switch B", {"ledColorWhenOn": 0})]


def test_new_change_cancels_a_half_sent_rollout(make_app, monkeypatch):
    monkeypatch.setattr(leds_app, "PACE_S", 0.002)
    a = make_app([device(f"S{i:02}", ieee=f"0x{i}") for i in range(20)])

    async def go():
        a._loop = asyncio.get_running_loop()
        a._set({"state": "ON", "effect": "christmas_checker"})
        await asyncio.sleep(0.05)
        a._set({"effect": "pulse", "color": {"h": 0, "s": 100}})
        await settle(a)

    asyncio.run(go())
    assert all(n == ("pulse", 0, 100) for n in a.notifications.values())
    assert all(a._current(d)["defaultLed2ColorWhenOn"] == 255 for d in a._switches())


def test_rejoined_switch_gets_its_notification_again(make_app, monkeypatch):
    monkeypatch.setattr(leds_app, "REJOIN_SETTLE_S", 0)
    a = make_app(house())
    run(a, lambda: a._set({"state": "ON", "effect": "aurora"}))
    a._mqtt.clear()

    async def go():
        a._loop = asyncio.get_running_loop()
        a._on_rejoin("0x3")
        await asyncio.sleep(0.01)
        await settle(a)

    asyncio.run(go())
    assert a._mqtt.sets() == [("Nook Switch", {"led_effect": {
        "effect": "aurora", "color": 0, "level": 100, "duration": 255}})]


def test_effect_list():
    assert EFFECTS[0] == "solid" and "christmas_checker" in EFFECTS
    assert len(EFFECTS) == len(set(EFFECTS))
