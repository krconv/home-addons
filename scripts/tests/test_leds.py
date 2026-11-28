import asyncio
import json
import logging

import pytest

from src import leds_app, zigbee
from src.leds_app import (EFFECTS, OFF, LedsApp, Look, Request, Setting, chunks,
                          desired_groups, indicator, inovelli_color, look_for, notification,
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


class FakeZigbee:
    """Devices and groups as the bridges report them; bridge requests are recorded and applied."""

    def __init__(self, devices):
        self._devices_by_ieee = {zigbee.normalize_ieee(d.ieee_address): d for d in devices}
        self._groups_by_id = {}
        self.requests = []

    def add_group(self, base, name, members=()):
        self._groups_by_id[f"{base[-1]}-{len(self._groups_by_id) + 1}"] = zigbee.ZigBeeGroup(
            base_topic=base, id=len(self._groups_by_id) + 1, friendly_name=name,
            members=sorted(members))

    def device(self, ieee):
        return self._devices_by_ieee[zigbee.normalize_ieee(ieee)]

    def group(self, base, name):
        return next((g for g in self._groups_by_id.values()
                     if g.base_topic == base and g.friendly_name == name), None)

    def _ieee(self, device):
        return next((d.ieee_address for d in self._devices_by_ieee.values()
                     if d.friendly_name == device), device)

    async def create_group(self, base, name):
        self.requests.append((base, "group/add", name, None))
        self.add_group(base, name)

    async def set_group_options(self, base, name, options):
        self.requests.append((base, "group/options", name, options))

    async def add_group_member(self, base, group, device):
        self.requests.append((base, "group/members/add", group, device))
        self.group(base, group).members.append(self._ieee(device))

    async def remove_group_member(self, base, group, device):
        self.requests.append((base, "group/members/remove", group, device))
        self.group(base, group).members.remove(self._ieee(device))


class FakeMqtt:
    """Records publishes and models Zigbee2MQTT: a set on a switch is echoed into its reported
    state; a set on a group changes the members but isn't reported until a get on the group."""

    def __init__(self, devices, zb):
        self.published = []
        self.by_name = {d.friendly_name: d for d in devices}
        self.actual = {d.friendly_name: dict(d.state.properties) for d in devices}
        self.zb = zb

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload))
        parts = topic.split("/")
        if len(parts) != 3:
            return
        base, name, verb = parts
        ints = {k: v for k, v in payload.items() if isinstance(v, int)}
        if name in self.by_name and verb == "set":
            self.actual[name].update(ints)
            self.by_name[name].state.properties.update(ints)
        elif (group := self.zb.group(base, name)) is not None:
            for ieee in group.members:
                member = self.zb.device(ieee).friendly_name
                if verb == "set":
                    self.actual[member].update(ints)
                else:
                    self.by_name[member].state.properties.update(
                        {k: self.actual[member][k] for k in payload if k in self.actual[member]})

    def sets(self):
        return [(t.split("/")[1], p) for t, p in self.published if t.endswith("/set")]

    def messages(self, base):
        return [(t.split("/", 1)[1], p) for t, p in self.published if t.startswith(base + "/")]

    def clear(self):
        self.published.clear()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(leds_app, "PACE_S", 0)
    monkeypatch.setattr(leds_app, "GROUP_PACE_S", 0)
    monkeypatch.setattr(leds_app, "DEBOUNCE_S", 0)


@pytest.fixture
def make_app(tmp_path):
    def make(devices, known=True, groups=None):
        a = LedsApp(logger=logging.getLogger("test"), addon_config={}, app_config={},
                    state_file=str(tmp_path / "leds" / "notifications.json"),
                    indicator_file=str(tmp_path / "leds" / "indicator.json"))
        a._zigbee = FakeZigbee(devices)
        if groups == "desired":
            groups = {k: v for k, v in desired_groups(a._switches()).items() if v}
        for (base, name), members in (groups or {}).items():
            a._zigbee.add_group(base, name, members)
        a._mqtt = FakeMqtt(devices, a._zigbee)
        a._reconcile_task = None
        a._groups_task = None
        if known:
            a.notifications = {d.ieee_address: None for d in devices}
        return a
    return make


def house():
    return [device("Kitchen Switch A", ieee="0x1"), device("Kitchen Switch B", ieee="0x2"),
            device("Nook Switch", base="zigbee2mqtt-b", ieee="0x3")]


def network(per_network):
    """Switches S00.. on zigbee2mqtt-a, then as many on zigbee2mqtt-b."""
    return [device(f"S{i:02}", base="zigbee2mqtt-a" if i < per_network else "zigbee2mqtt-b",
                   ieee=f"0x{i:02}") for i in range(2 * per_network)]


async def settle(a):
    for _ in range(3):
        await asyncio.sleep(0)
        for task in (a._reconcile_task, a._groups_task):
            if task:
                await task


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


def test_groups_split_by_global_index():
    groups = desired_groups(network(2))
    assert groups[("zigbee2mqtt-a", "leds_all")] == {"0x00", "0x01"}
    assert groups[("zigbee2mqtt-b", "leds_even")] == {"0x02"}
    assert groups[("zigbee2mqtt-b", "leds_odd")] == {"0x03"}


def test_chunks_cap_keys_per_write():
    assert [len(c) for c in chunks(FACTORY)] == [8, 8, 8, 8]
    assert chunks({}) == []


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


def test_without_groups_each_switch_gets_one_message_of_changed_parameters(make_app):
    a = make_app(house())
    run(a, lambda: a._set({"state": "ON", "effect": "solid", "color": {"h": 0, "s": 100},
                           "brightness": 33}))
    assert sorted(a._mqtt.sets()) == [
        (name, {"ledColorWhenOn": 0, "ledColorWhenOff": 0})
        for name in ("Kitchen Switch A", "Kitchen Switch B", "Nook Switch")]


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
    assert nook[0] == {"ledColorWhenOn": 85, "ledColorWhenOff": 85, "ledIntensityWhenOn": 100}
    assert nook[1]["led_effect"]["effect"] == "chase" and len(nook) == 2
    a._mqtt.clear()
    run(a, lambda: a._set({"state": "OFF"}))
    nook = [p for n, p in a._mqtt.sets() if n == "Nook Switch"]
    assert nook[0] == {"ledColorWhenOn": 170, "ledColorWhenOff": 170, "ledIntensityWhenOn": 33}
    assert nook[1]["led_effect"]["effect"] == "clear_effect" and len(nook) == 2


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
    a._zigbee.device("0x2").state.properties["ledColorWhenOn"] = 42
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


def test_restart_with_nothing_reported_sends_nothing(make_app):
    a = make_app(house())
    run(a, lambda: a._set({"state": "ON", "effect": "christmas_checker"}))
    assert json.load(open(a.indicator_file))["0x1"]["defaultLed2ColorWhenOn"] == 85

    # Restart: Zigbee2MQTT doesn't retain device state, so the switches report nothing yet.
    b = make_app([device(d.friendly_name, base=d.base_topic, ieee=d.ieee_address, props={})
                  for d in house()], known=False)
    b.state_file, b.indicator_file = a.state_file, a.indicator_file
    b._load_notifications()
    b._load_sent()
    b.setting = a.setting
    run(b, lambda: b._schedule_reconcile(0))
    assert b._mqtt.sets() == []


def test_without_saved_values_unreported_parameters_are_written(make_app):
    a = make_app([device("Nook Switch", ieee="0x3", props={})])
    run(a, lambda: a._schedule_reconcile(0))
    sets = a._mqtt.sets()
    assert [len(p) for _, p in sets] == [8, 8, 8, 8]
    assert {k: v for _, p in sets for k, v in p.items()} == FACTORY


def test_a_report_overrides_the_saved_value(make_app):
    a = make_app(house())
    a.sent = {"0x1": {"ledColorWhenOn": 170}}
    a._zigbee.device("0x1").state.properties["ledColorWhenOn"] = 42
    run(a, lambda: a._schedule_reconcile(0))
    assert a._mqtt.sets() == [("Kitchen Switch A", {"ledColorWhenOn": 170})]


def test_unreadable_indicator_file_is_ignored(make_app):
    a = make_app(house())
    import os
    os.makedirs(os.path.dirname(a.indicator_file), exist_ok=True)
    open(a.indicator_file, "w").write("{not json")
    a._load_sent()
    assert a.sent == {}


# Groups.

def test_house_color_change_goes_to_groups_then_reads_back(make_app):
    a = make_app(network(3), groups="desired")
    run(a, lambda: a._set({"state": "ON", "color": {"h": 0, "s": 100}}))
    red = {"ledColorWhenOn": 0, "ledColorWhenOff": 0, "ledIntensityWhenOn": 100}
    for base in ("zigbee2mqtt-a", "zigbee2mqtt-b"):
        assert a._mqtt.messages(base) == [
            ("leds_all/set", red),
            ("leds_all/get", {k: "" for k in red}),
        ]
    assert all(d.state.properties["ledColorWhenOn"] == 0 for d in a._switches())
    a._mqtt.clear()
    a.written.clear()
    run(a, lambda: a._schedule_reconcile(0))
    assert a._mqtt.published == []


def test_a_group_with_the_wrong_members_is_not_used(make_app):
    a = make_app(network(3), groups="desired")
    a._zigbee.group("zigbee2mqtt-a", "leds_all").members.remove("0x02")
    run(a, lambda: a._set({"state": "ON", "color": {"h": 0, "s": 100}}))
    assert sorted(n for n, _ in a._mqtt.messages("zigbee2mqtt-a")) == ["S00/set", "S01/set", "S02/set"]
    assert [n for n, _ in a._mqtt.messages("zigbee2mqtt-b")] == ["leds_all/set", "leds_all/get"]


def test_a_room_request_keeps_its_keys_off_that_networks_group(make_app):
    a = make_app(network(3), groups="desired")
    run(a, lambda: (a.request("health", {"switches": ["S00"], "color": 85}),
                    a._set({"state": "ON", "color": {"h": 0, "s": 100}})))
    a_msgs = a._mqtt.messages("zigbee2mqtt-a")
    assert a_msgs[0] == ("leds_all/set", {"ledIntensityWhenOn": 100})
    assert ("S00/set", {"ledColorWhenOn": 85, "ledColorWhenOff": 85}) in a_msgs
    assert ("S01/set", {"ledColorWhenOn": 0, "ledColorWhenOff": 0}) in a_msgs
    assert [n for n, _ in a._mqtt.messages("zigbee2mqtt-b")] == ["leds_all/set", "leds_all/get"]
    assert [a._current(d)["ledColorWhenOn"] for d in a._switches()] == [85] + [0] * 5


def test_alternate_uses_even_and_odd_groups(make_app):
    a = make_app(network(6), groups="desired")
    run(a, lambda: a._set({"state": "ON", "effect": "christmas_alternate"}))
    for base in ("zigbee2mqtt-a", "zigbee2mqtt-b"):
        sets = [(n, p) for n, p in a._mqtt.messages(base) if n.endswith("/set")]
        assert sets == [("leds_all/set", {"ledIntensityWhenOn": 100}),
                        ("leds_even/set", {"ledColorWhenOn": 0, "ledColorWhenOff": 0}),
                        ("leds_odd/set", {"ledColorWhenOn": 85, "ledColorWhenOff": 85})]
    assert [a._current(d)["ledColorWhenOn"] for d in a._switches()] == [0, 85] * 6


def test_checker_writes_per_led_colors_to_even_and_odd_in_chunks(make_app):
    a = make_app(network(6), groups="desired")
    run(a, lambda: a._set({"state": "ON", "effect": "christmas_checker"}))
    sets = [(n, len(p)) for n, p in a._mqtt.messages("zigbee2mqtt-a") if n.endswith("/set")]
    assert sets == [("leds_all/set", 3), ("leds_even/set", 8), ("leds_even/set", 6),
                    ("leds_odd/set", 8), ("leds_odd/set", 6)]
    gets = [n for n, _ in a._mqtt.messages("zigbee2mqtt-a") if n.endswith("/get")]
    assert len(gets) == 5
    look = look_for("christmas_checker", 0, 100)
    assert all(a._current(d) == indicator(look, i) for i, d in enumerate(a._switches()))


def test_animation_indicator_writes_precede_every_animation(make_app):
    a = make_app(network(6), groups="desired")
    run(a, lambda: (a.request("one", {"switches": ["S07"], "effect": "pulse", "color": 42}),
                    a._set({"state": "ON", "effect": "christmas_chase"})))
    for base in ("zigbee2mqtt-a", "zigbee2mqtt-b"):
        msgs = a._mqtt.messages(base)
        effects = [i for i, (_, p) in enumerate(msgs) if "led_effect" in p]
        writes = [i for i, (_, p) in enumerate(msgs) if "led_effect" not in p]
        assert effects and max(writes) < min(effects)
    assert ("leds_even/set", {"led_effect": {"effect": "chase", "color": 0, "level": 100,
                                             "duration": 255}}) in a._mqtt.messages("zigbee2mqtt-a")
    assert ("S07/set", {"led_effect": {"effect": "pulse", "color": 42, "level": 100,
                                       "duration": 255}}) in a._mqtt.messages("zigbee2mqtt-b")


def test_a_key_fewer_than_group_min_switches_need_goes_unicast(make_app):
    a = make_app(network(3), groups="desired")
    for ieee in ("0x00", "0x01"):
        a._zigbee.device(ieee).state.properties["ledColorWhenOn"] = 42
    run(a, lambda: a._schedule_reconcile(0))
    assert sorted(a._mqtt.sets()) == [("S00", {"ledColorWhenOn": 170}),
                                      ("S01", {"ledColorWhenOn": 170})]


def test_missing_groups_are_created_disabled_before_their_first_member(make_app):
    a = make_app(network(3))
    run(a, a._schedule_group_maintenance)
    reqs = [r for r in a._zigbee.requests if r[0] == "zigbee2mqtt-a" and r[2] == "leds_odd"]
    assert reqs == [
        ("zigbee2mqtt-a", "group/add", "leds_odd", None),
        ("zigbee2mqtt-a", "group/options", "leds_odd",
         {"homeassistant": {"light": {"enabled_by_default": False}}}),
        ("zigbee2mqtt-a", "group/members/add", "leds_odd", "S01"),
    ]
    desired = {k: v for k, v in desired_groups(a._switches()).items() if v}
    assert a._usable_groups(a._switches()) == desired
    a._zigbee.requests.clear()
    run(a, a._schedule_group_maintenance)
    assert a._zigbee.requests == []


def test_wrong_members_are_fixed_and_other_groups_left_alone(make_app):
    a = make_app(network(3), groups="desired")
    gone = "0x0000000000000099"
    a._zigbee.group("zigbee2mqtt-a", "leds_all").members.append(gone)
    a._zigbee.group("zigbee2mqtt-b", "leds_odd").members.remove("0x05")
    a._zigbee.add_group("zigbee2mqtt-a", "a-1", ["0x00", gone])
    run(a, a._schedule_group_maintenance)
    assert a._zigbee.requests == [
        ("zigbee2mqtt-a", "group/members/remove", "leds_all", "0x0000000000000099"),
        ("zigbee2mqtt-b", "group/members/add", "leds_odd", "S05"),
    ]
    assert a._zigbee.group("zigbee2mqtt-a", "a-1").members == sorted(["0x00", gone])
    assert a._usable_groups(a._switches()) == desired_groups(a._switches())


def test_bridge_groups_are_parsed_and_replaced():
    cls = next(c.cell_contents for c in zigbee.ZigBeeClient.__closure__
               if isinstance(c.cell_contents, type))
    c = cls.__new__(cls)
    c.logger = logging.getLogger("test")
    c._groups_by_id = {"b-1": zigbee.ZigBeeGroup(base_topic="zigbee2mqtt-b", id=1, friendly_name="x")}
    c._groups_listeners = []
    seen = []
    c.add_groups_listener(seen.append)
    on_groups = c._on_groups_received("zigbee2mqtt-a", asyncio.Event())
    on_groups("zigbee2mqtt-a/bridge/groups", json.dumps([{"id": 1, "friendly_name": "old", "members": []}]))
    on_groups("zigbee2mqtt-a/bridge/groups", json.dumps([{
        "id": 7, "friendly_name": "leds_all", "scenes": [],
        "members": [{"ieee_address": "0x00124B0012345678", "endpoint": 1},
                    {"ieee_address": "0x00124B0012345678", "endpoint": 2}]}]))
    assert sorted(c._groups_by_id) == ["a-7", "b-1"]
    assert c._groups_by_id["a-7"].members == ["0x00124b0012345678"]
    assert seen == ["zigbee2mqtt-a", "zigbee2mqtt-a"]
