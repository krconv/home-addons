"""LedsApp: owns the LED bar on every Inovelli Blue switch.

Two layers decide what each bar shows:

- The house setting: a Home Assistant light ("Switch LED Bars") made with MQTT discovery, with
  on/off, brightness, a color and an effect dropdown. Its retained state topic is the app's
  memory, so a restart picks it back up. Off means the bars show their normal level indicator.
- Requests: other apps (or anything on MQTT) ask for a look on some rooms or switches, with a
  priority, e.g. the lights app showing an unhealthy room in red. On each switch the
  highest-priority request wins; with none, the house setting shows. Releasing a request puts
  those switches back. Send a request to `scripts/leds/request/<id>` (empty payload releases
  it) or call `LedsApp.request()` / `LedsApp.release()` in-process.

A reconcile loop sends each switch only what changed since the last thing it was sent.
Zigbee2MQTT 2.4 can't send Inovelli's custom-cluster commands to a group, so every switch gets
its own unicast command, paced so the mesh isn't flooded: about 4 s for a whole-bar effect on
~50 switches, about 30 s for per-LED patterns. A switch that rejoins (power cut, re-pair) is
sent its look again.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, replace

from . import mqtt, zigbee

TOPIC = "scripts/leds"
DISCOVERY_TOPIC = "homeassistant/light/scripts_led_bars/config"
LED_MODELS = {"VZM30-SN", "VZM31-SN", "VZM35-SN", "VZM36"}
LEDS_PER_BAR = 7
PACE_S = 0.06
DEBOUNCE_S = 0.5
REJOIN_SETTLE_S = 5
# Inovelli's "until cleared" duration.
FOREVER = 255
RED, GREEN, WHITE = 0, 85, 255

# Whole-bar effects the switch firmware runs itself (Zigbee2MQTT led_effect names).
BAR_EFFECTS = [
    "solid", "pulse", "chase", "slow_chase", "fast_chase", "aurora", "open_close", "small_to_big",
    "slow_blink", "medium_blink", "fast_blink", "slow_falling", "medium_falling", "fast_falling",
    "slow_rising", "medium_rising", "fast_rising", "slow_siren", "fast_siren",
]
# Patterns built from those, with their own colors: (kind, effect, color A, color B).
PATTERNS = {
    "christmas_checker": ("checker", "solid", RED, GREEN),
    "christmas_pulse": ("alternate", "pulse", RED, GREEN),
    "christmas_chase": ("alternate", "chase", RED, GREEN),
}
EFFECTS = BAR_EFFECTS + list(PATTERNS)

_instance: "LedsApp | None" = None


def instance() -> "LedsApp | None":
    """The running LedsApp, for other apps in this add-on to send requests to."""
    return _instance


@dataclass(frozen=True)
class Look:
    """What one bar should show. kind: off (normal indicator), bar, alternate or checker."""

    kind: str = "off"
    effect: str = "solid"
    color: int = 0
    color_b: int = 0
    level: int = 100


OFF = Look()


def look_for(effect: str, color: int, level: int, color_b: int | None = None) -> Look:
    if effect == "off":
        return OFF
    if effect in PATTERNS:
        kind, fx, a, b = PATTERNS[effect]
        return Look(kind, fx, a, b, level)
    if effect not in BAR_EFFECTS:
        raise ValueError(f"unknown effect {effect!r}")
    if color_b is not None:
        return Look("alternate", effect, color, color_b, level)
    return Look("bar", effect, color, color, level)


def inovelli_color(hue: float, saturation: float) -> int:
    """Home Assistant hue/saturation to Inovelli's 0-255 scale (0 red ... 254, 255 white)."""
    if saturation < 20:
        return WHITE
    return round((hue % 360) / 360 * 254)


def parse_color(value) -> int:
    """A color as an Inovelli 0-255 number or a Home Assistant {"h": 0-360, "s": 0-100}."""
    if isinstance(value, dict):
        return inovelli_color(float(value["h"]), float(value.get("s", 100)))
    return max(0, min(255, int(value)))


@dataclass
class Setting:
    """The house setting, as the Home Assistant light holds it."""

    on: bool = False
    effect: str = "solid"
    hue: float = 0.0  # Home Assistant hs color: hue 0-360, saturation 0-100
    saturation: float = 100.0
    brightness: int = 100  # 1-100

    def look(self) -> Look:
        if not self.on:
            return OFF
        return look_for(self.effect, inovelli_color(self.hue, self.saturation), self.brightness)

    def state_payload(self) -> dict:
        return {
            "state": "ON" if self.on else "OFF",
            "brightness": self.brightness,
            "color_mode": "hs",
            "color": {"h": self.hue, "s": self.saturation},
            "effect": self.effect,
        }

    def merge(self, command: dict) -> "Setting":
        s = replace(self)
        if "state" in command:
            s.on = str(command["state"]).upper() == "ON"
        if "brightness" in command:
            s.brightness = max(1, min(100, int(command["brightness"])))
        if isinstance(command.get("color"), dict) and "h" in command["color"]:
            s.hue = float(command["color"]["h"])
            s.saturation = float(command["color"].get("s", 100))
        if command.get("effect") in EFFECTS:
            s.effect = command["effect"]
        return s


@dataclass
class Request:
    """A look asked for on some rooms and/or switches. No targets means every switch."""

    id: str
    look: Look
    rooms: frozenset[str] = frozenset()  # lower-case room names
    switches: frozenset[str] = frozenset()  # Zigbee2MQTT friendly names
    priority: int = 50
    expires_at: float | None = None  # time.monotonic()

    @classmethod
    def parse(cls, request_id: str, data: dict, now: float) -> "Request":
        """From the MQTT/in-process form:
        {"rooms": ["Kitchen"], "switches": ["Nook Switch"], "effect": "pulse",
         "color": 0 or {"h": 0, "s": 100}, "color_b": 85, "level": 100,
         "priority": 50, "expires_s": 600}
        effect may be a built-in effect, a pattern or "off" (force the normal indicator).
        """
        color_b = data.get("color_b")
        look = look_for(
            str(data.get("effect", "solid")),
            parse_color(data.get("color", RED)),
            max(1, min(100, int(data.get("level", 100)))),
            None if color_b is None else parse_color(color_b),
        )
        expires_s = data.get("expires_s")
        return cls(
            id=request_id,
            look=look,
            rooms=frozenset(str(r).lower() for r in data.get("rooms", [])),
            switches=frozenset(str(s) for s in data.get("switches", [])),
            priority=int(data.get("priority", 50)),
            expires_at=None if expires_s is None else now + float(expires_s),
        )

    def targets(self, device: zigbee.ZigBeeDevice) -> bool:
        if not self.rooms and not self.switches:
            return True
        return device.friendly_name in self.switches or room_of(device) in self.rooms

    def summary(self) -> dict:
        return {
            "rooms": sorted(self.rooms), "switches": sorted(self.switches),
            "priority": self.priority, "look": self.look.__dict__,
        }


def room_of(device: zigbee.ZigBeeDevice) -> str:
    """Switches are named "<Room> Switch [letter]"; the room is what comes before "Switch"."""
    name = device.friendly_name
    head, sep, _ = name.partition(" Switch")
    return (head if sep else name).strip().lower()


def switch_payloads(look: Look, index: int, previous: Look | None = None) -> list[dict]:
    """The Zigbee2MQTT set payloads that put switch number `index` into `look`, in order."""
    if look.kind == "off":
        payloads = [_bar("clear_effect", 0, 0, 0)]
        # Single-LED effects sit under the bar effect; clear them only if they may be there.
        if previous is None or previous.kind == "checker":
            payloads += [_led(n, "clear_effect", 0, 0, 0) for n in range(1, LEDS_PER_BAR + 1)]
        return payloads
    if look.kind == "checker":
        # The bar effect outranks single-LED effects, so clear it first.
        flip = index % 2 == 1
        return [_bar("clear_effect", 0, 0, 0)] + [
            _led(n, look.effect, look.color if (n % 2 == 1) != flip else look.color_b,
                 look.level, FOREVER)
            for n in range(1, LEDS_PER_BAR + 1)
        ]
    color = look.color_b if look.kind == "alternate" and index % 2 == 1 else look.color
    return [_bar(look.effect, color, look.level, FOREVER)]


def _bar(effect: str, color: int, level: int, duration: int) -> dict:
    return {"led_effect": {"effect": effect, "color": color, "level": level, "duration": duration}}


def _led(n: int, effect: str, color: int, level: int, duration: int) -> dict:
    return {"individual_led_effect": {
        "led": str(n), "effect": effect, "color": color, "level": level, "duration": duration,
    }}


def interleave(devices: list) -> list:
    """Alternate between bridges so both Zigbee networks work at the same time."""
    by_base: dict[str, list] = {}
    for d in devices:
        by_base.setdefault(d.base_topic, []).append(d)
    queues = list(by_base.values())
    out = []
    for i in range(max((len(q) for q in queues), default=0)):
        out.extend(q[i] for q in queues if i < len(q))
    return out


@dataclass
class LedsApp:
    logger: logging.Logger
    addon_config: dict
    app_config: dict
    setting: Setting = field(default_factory=Setting)
    requests: dict[str, Request] = field(default_factory=dict)
    # What each switch (by IEEE) was last sent. Assumed normal at start, so a restart with the
    # house light off doesn't clear notifications something else put on the bars.
    sent: dict[str, Look] = field(default_factory=dict)

    async def initialize(self) -> None:
        global _instance
        self._loop = asyncio.get_running_loop()
        self._reconcile_task: asyncio.Task | None = None
        self._zigbee = zigbee.ZigBeeClient(self.logger, self.addon_config)
        await self._zigbee.initialize()
        self._mqtt = mqtt.MqttClient(self.logger, self.addon_config)

        switches = self._switches()
        rooms = sorted({room_of(d) for d in switches})
        self.logger.info(f"Found {len(switches)} switches with LED bars in {len(rooms)} rooms")
        self.sent = {d.ieee_address: OFF for d in switches}

        await self._restore_setting()
        self._publish_discovery()
        self._publish_state()
        self._mqtt.publish(f"{TOPIC}/rooms", {"rooms": rooms}, retain=True)
        self._mqtt.subscribe(f"{TOPIC}/set", self._on_command)
        # Retained requests come back here too, so they survive a restart.
        self._mqtt.subscribe(f"{TOPIC}/request/+", self._on_request)
        for base in self.addon_config["zigbee_base_topics"]:
            self._mqtt.subscribe(f"{base}/bridge/event", self._on_bridge_event)

        _instance = self
        self._schedule_reconcile(delay=DEBOUNCE_S)

    async def _restore_setting(self) -> None:
        """The light's retained state is the last house setting; read it before taking commands."""
        restored = asyncio.Event()

        def on_state(topic: str, payload: str) -> None:
            try:
                self.setting = Setting().merge(json.loads(payload))
            except (ValueError, TypeError) as e:
                self.logger.warning(f"Ignoring unreadable saved LED setting {payload!r}: {e}")
            self._loop.call_soon_threadsafe(restored.set)

        self._mqtt.subscribe(f"{TOPIC}/state", on_state)
        try:
            await asyncio.wait_for(restored.wait(), 3)
        except asyncio.TimeoutError:
            self.logger.info("No saved LED setting; starting off")
        self._mqtt.unsubscribe(f"{TOPIC}/state", on_state)

    # Public API for other apps in this add-on.

    def request(self, request_id: str, data: dict) -> None:
        """Ask for a look on some rooms/switches; see Request.parse for `data`. Replaces any
        earlier request with the same id. Call from the event loop."""
        self.requests[request_id] = Request.parse(request_id, data, time.monotonic())
        self._on_requests_changed()

    def release(self, request_id: str) -> None:
        """Drop a request; its switches go back to the next request or the house setting."""
        if self.requests.pop(request_id, None) is not None:
            self._on_requests_changed()

    # Resolution.

    def _switches(self) -> list[zigbee.ZigBeeDevice]:
        devices = [
            d for d in self._zigbee._devices_by_ieee.values()
            if d.manufacturer == "Inovelli" and d.model_id in LED_MODELS
        ]
        return sorted(devices, key=lambda d: d.friendly_name)

    def _desired(self, device: zigbee.ZigBeeDevice, now: float) -> Look:
        live = [
            r for r in self.requests.values()
            if (r.expires_at is None or r.expires_at > now) and r.targets(device)
        ]
        if live:
            return max(live, key=lambda r: r.priority).look
        return self.setting.look()

    def _drop_expired(self, now: float) -> None:
        for rid in [rid for rid, r in self.requests.items() if r.expires_at and r.expires_at <= now]:
            self.logger.info(f"LED request {rid!r} expired")
            del self.requests[rid]

    # Reconcile.

    def _schedule_reconcile(self, delay: float) -> None:
        # Restart from the top on every change; `sent` is per switch, so finished switches
        # aren't sent again.
        if self._reconcile_task and not self._reconcile_task.done():
            self._reconcile_task.cancel()
        self._reconcile_task = asyncio.create_task(self._reconcile(delay))

    async def _reconcile(self, delay: float) -> None:
        await asyncio.sleep(delay)
        now = time.monotonic()
        self._drop_expired(now)
        self._arm_next_expiry(now)
        switches = self._switches()
        index = {d.ieee_address: i for i, d in enumerate(switches)}
        changed = 0
        for device in interleave(switches):
            want = self._desired(device, now)
            had = self.sent.get(device.ieee_address)
            if want == had:
                continue
            for payload in switch_payloads(want, index[device.ieee_address], had):
                self._send(device, payload)
                await asyncio.sleep(PACE_S)
            self.sent[device.ieee_address] = want
            changed += 1
        if changed:
            self.logger.info(f"Updated LED bars on {changed} of {len(switches)} switches")

    def _arm_next_expiry(self, now: float) -> None:
        expiries = [r.expires_at for r in self.requests.values() if r.expires_at]
        if expiries:
            self._loop.call_later(max(0.0, min(expiries) - now) + 0.1, self._schedule_reconcile, 0)

    def _send(self, device: zigbee.ZigBeeDevice, payload: dict) -> None:
        try:
            self._mqtt.publish(f"{device.base_topic}/{device.friendly_name}/set", payload)
        except Exception as e:  # one failed publish shouldn't stop the rest
            self.logger.warning(f"LED command to {device.friendly_name} failed: {e}")

    # House setting (the Home Assistant light).

    def _publish_discovery(self) -> None:
        self._mqtt.publish(DISCOVERY_TOPIC, {
            "name": "Switch LED Bars",
            "unique_id": "scripts_led_bars",
            "object_id": "switch_led_bars",
            "icon": "mdi:led-strip-variant",
            "schema": "json",
            "command_topic": f"{TOPIC}/set",
            "state_topic": f"{TOPIC}/state",
            "brightness": True,
            "brightness_scale": 100,
            "supported_color_modes": ["hs"],
            "effect": True,
            "effect_list": EFFECTS,
            "device": {
                "identifiers": ["scripts_leds"],
                "name": "Scripts LED Bars",
                "manufacturer": "krconv/home-addons",
                "model": "Scripts add-on",
            },
        }, retain=True)

    def _publish_state(self) -> None:
        self._mqtt.publish(f"{TOPIC}/state", self.setting.state_payload(), retain=True)

    def _on_command(self, topic: str, payload: str) -> None:
        """Runs on the MQTT client thread."""
        try:
            command = json.loads(payload)
            if not isinstance(command, dict):
                raise ValueError("not an object")
        except ValueError as e:
            self.logger.warning(f"Ignoring invalid LED command {payload!r}: {e}")
            return
        self._loop.call_soon_threadsafe(self._set, command)

    def _set(self, command: dict) -> None:
        new = self.setting.merge(command)
        changed = new != self.setting
        self.setting = new
        self._publish_state()
        if changed:
            self.logger.info(f"LED house setting: {new.state_payload()}")
            self._schedule_reconcile(delay=DEBOUNCE_S)

    # Requests over MQTT.

    def _on_request(self, topic: str, payload: str) -> None:
        """Runs on the MQTT client thread. An empty payload releases the request."""
        request_id = topic.rsplit("/", 1)[-1]
        if not payload.strip():
            self._loop.call_soon_threadsafe(self.release, request_id)
            return
        try:
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            Request.parse(request_id, data, 0)  # validate on this thread, apply on the loop
        except (ValueError, TypeError, KeyError) as e:
            self.logger.warning(f"Ignoring invalid LED request {request_id!r} {payload!r}: {e}")
            return
        self._loop.call_soon_threadsafe(self.request, request_id, data)

    def _on_requests_changed(self) -> None:
        self._mqtt.publish(
            f"{TOPIC}/requests",
            {rid: r.summary() for rid, r in sorted(self.requests.items())},
            retain=True,
        )
        self._schedule_reconcile(delay=DEBOUNCE_S)

    # Rejoins.

    def _on_bridge_event(self, topic: str, payload: str) -> None:
        """A switch that rejoins (power cut, re-pair) has lost its effect; send it again."""
        try:
            event = json.loads(payload)
        except ValueError:
            return
        if event.get("type") not in ("device_announce", "device_joined"):
            return
        ieee = str((event.get("data") or {}).get("ieee_address", ""))
        self._loop.call_soon_threadsafe(self._on_rejoin, ieee)

    def _on_rejoin(self, ieee: str) -> None:
        if ieee not in self.sent:
            return
        # A rebooted switch shows its normal indicator; wait for it to settle, then reconcile.
        self.sent[ieee] = OFF
        self._loop.call_later(REJOIN_SETTLE_S, self._schedule_reconcile, 0)
