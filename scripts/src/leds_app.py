"""LedsApp: owns the LED bar on every Inovelli Blue switch.

Two layers decide what each bar shows:

- The house setting: a Home Assistant light ("Switch LED Bars") made with MQTT discovery, with
  on/off, brightness, a color and an effect dropdown. Its retained state topic is the app's
  memory, so a restart picks it back up. Off puts the factory indicator back.
- Requests: other apps (or anything on MQTT) ask for a look on some rooms or switches, with a
  priority, e.g. the lights app showing an unhealthy room in red. On each switch the
  highest-priority request wins; with none, the house setting shows. Releasing a request puts
  those switches back. Send a request to `scripts/leds/request/<id>` (empty payload releases
  it) or call `LedsApp.request()` / `LedsApp.release()` in-process.

A look is drawn with two of the switch's own features:

- The indicator: the bar that shows the dim level. Its color and brightness are parameters
  (P95-P98 for the whole bar, P60-P93 per LED), stored in the switch, so they follow the load's
  level and on/off, survive a power cut, and never flash. A steady color or pattern uses only
  this.
- A notification (`led_effect`): an animation that covers the whole bar. Only animated effects
  use it, on top of an indicator in the same color, so the switch never shows another color
  when it briefly drops the animation to show a level change.

The reconcile loop writes only what differs from what each switch last reported (or was just
sent), restarts from the top on every change, and runs every 10 minutes to put back anything
that drifted. Zigbee2MQTT 2.4 can't send Inovelli's custom-cluster commands to a group, so
every switch gets its own commands, paced so the mesh isn't flooded.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, replace

from . import mqtt, zigbee

TOPIC = "scripts/leds"
DISCOVERY_TOPIC = "homeassistant/light/scripts_led_bars/config"
STATE_FILE = "/data/leds/notifications.json"
LED_MODELS = {"VZM30-SN", "VZM31-SN", "VZM35-SN", "VZM36"}
LEDS_PER_BAR = 7
PACE_S = 0.06  # per Zigbee command
DEBOUNCE_S = 0.5
REJOIN_SETTLE_S = 5
RESYNC_S = 600
# Inovelli's "until cleared" notification duration.
FOREVER = 255
# Inovelli's color scale: 0 red, 21 orange, 42 yellow, 85 green, 127 cyan, 170 blue,
# 212 purple, 234 pink, 255 white.
RED, GREEN, BLUE, WHITE = 0, 85, 170, 255
# Per-LED parameter values that mean "use the whole-bar parameter".
SYNC_COLOR, SYNC_INTENSITY = 255, 101

# The factory indicator: what "off" puts back (Inovelli defaults, and this house's settings).
FACTORY_COLOR, FACTORY_ON_INTENSITY, FACTORY_OFF_INTENSITY = BLUE, 33, 1

# Animations the switch firmware runs itself (Zigbee2MQTT led_effect names). "solid" isn't an
# animation: it's the indicator in the chosen color, following the load's level.
ANIMATIONS = [
    "pulse", "chase", "slow_chase", "fast_chase", "aurora", "open_close", "small_to_big",
    "slow_blink", "medium_blink", "fast_blink", "slow_falling", "medium_falling", "fast_falling",
    "slow_rising", "medium_rising", "fast_rising", "slow_siren", "fast_siren",
]
# Patterns with their own colors: (kind, effect, color A, color B).
PATTERNS = {
    "christmas_checker": ("checker", "solid", RED, GREEN),
    "christmas_alternate": ("alternate", "solid", RED, GREEN),
    "christmas_pulse": ("alternate", "pulse", RED, GREEN),
    "christmas_chase": ("alternate", "chase", RED, GREEN),
}
EFFECTS = ["solid"] + ANIMATIONS + list(PATTERNS)

_instance: "LedsApp | None" = None


def instance() -> "LedsApp | None":
    """The running LedsApp, for other apps in this add-on to send requests to."""
    return _instance


@dataclass(frozen=True)
class Look:
    """What one bar should show.

    kind: off (factory indicator), bar (one color), alternate (every other switch color_b) or
    checker (LEDs alternate color/color_b, flipped on every other switch). effect: "solid" or
    an animation. level: brightness 1-100.
    """

    kind: str = "off"
    effect: str = "solid"
    color: int = FACTORY_COLOR
    color_b: int = FACTORY_COLOR
    level: int = FACTORY_ON_INTENSITY


OFF = Look()

# A notification on a switch: (effect, color, level), or None for no notification.
Notification = tuple[str, int, int] | None


def look_for(effect: str, color: int, level: int, color_b: int | None = None) -> Look:
    if effect == "off":
        return OFF
    if effect in PATTERNS:
        kind, fx, a, b = PATTERNS[effect]
        return Look(kind, fx, a, b, level)
    if effect != "solid" and effect not in ANIMATIONS:
        raise ValueError(f"unknown effect {effect!r}")
    if color_b is not None:
        return Look("alternate", effect, color, color_b, level)
    return Look("bar", effect, color, color, level)


def inovelli_color(hue: float, saturation: float) -> int:
    """Home Assistant hue/saturation to Inovelli's scale (0 red ... 254, 255 white)."""
    if saturation < 20:
        return WHITE
    return round((hue % 360) / 360 * 254)


def parse_color(value) -> int:
    """A color as an Inovelli 0-255 number or a Home Assistant {"h": 0-360, "s": 0-100}."""
    if isinstance(value, dict):
        return inovelli_color(float(value["h"]), float(value.get("s", 100)))
    return max(0, min(255, int(value)))


def indicator(look: Look, index: int) -> dict[str, int]:
    """The indicator parameters (Zigbee2MQTT keys) that draw `look` on switch number `index`."""
    if look.kind == "off":
        color, on, off = FACTORY_COLOR, FACTORY_ON_INTENSITY, FACTORY_OFF_INTENSITY
    else:
        color = look.color_b if look.kind == "alternate" and index % 2 == 1 else look.color
        on, off = look.level, min(look.level, FACTORY_OFF_INTENSITY)
    params = {
        "ledColorWhenOn": color, "ledColorWhenOff": color,
        "ledIntensityWhenOn": on, "ledIntensityWhenOff": off,
    }
    flip = index % 2 == 1
    for n in range(1, LEDS_PER_BAR + 1):
        if look.kind == "checker":
            led_color = look.color if (n % 2 == 1) != flip else look.color_b
        else:
            led_color = SYNC_COLOR
        params[f"defaultLed{n}ColorWhenOn"] = led_color
        params[f"defaultLed{n}ColorWhenOff"] = led_color
        params[f"defaultLed{n}IntensityWhenOn"] = SYNC_INTENSITY
        params[f"defaultLed{n}IntensityWhenOff"] = SYNC_INTENSITY
    return params


def notification(look: Look, index: int) -> Notification:
    """The animation `look` runs on switch number `index`, or None."""
    if look.kind == "off" or look.effect == "solid":
        return None
    color = look.color_b if look.kind == "alternate" and index % 2 == 1 else look.color
    return (look.effect, color, look.level)


def notification_payloads(want: Notification, had: Notification | str) -> list[dict]:
    """Commands that take a switch from notification `had` to `want`. `had` is "unknown" when
    the app doesn't know what's on the switch (first run): then everything is cleared."""
    if want == had:
        return []
    if want is not None:
        effect, color, level = want
        return [{"led_effect": {"effect": effect, "color": color, "level": level,
                                "duration": FOREVER}}]
    payloads = [{"led_effect": _clear()}]
    if had == "unknown":
        # Single-LED notifications (an older version of this app used them) sit under the bar.
        payloads += [{"individual_led_effect": {"led": str(n), **_clear()}}
                     for n in range(1, LEDS_PER_BAR + 1)]
    return payloads


def _clear() -> dict:
    return {"effect": "clear_effect", "color": 0, "level": 0, "duration": 0}


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
        effect may be "solid", an animation, a pattern or "off" (the factory indicator).
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
    # Indicator values sent since the last resync, by IEEE. Compared before what the switch
    # reported, so a quick second change doesn't resend what Zigbee2MQTT hasn't echoed yet.
    written: dict[str, dict[str, int]] = field(default_factory=dict)
    # The notification on each switch (by IEEE) as far as the app knows; saved to STATE_FILE.
    notifications: dict[str, Notification] = field(default_factory=dict)
    state_file: str = STATE_FILE

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
        self._load_notifications()

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
        self._resync_task = asyncio.create_task(self._resync_forever())

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

    def _load_notifications(self) -> None:
        try:
            with open(self.state_file) as f:
                saved = json.load(f)
            self.notifications = {k: (tuple(v) if v else None) for k, v in saved.items()}
        except FileNotFoundError:
            self.logger.info("No saved notification state; clearing notifications once")
        except (ValueError, TypeError) as e:
            self.logger.warning(f"Unreadable {self.state_file} ({e}); clearing notifications once")

    def _save_notifications(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file + ".tmp", "w") as f:
                json.dump(self.notifications, f)
            os.replace(self.state_file + ".tmp", self.state_file)
        except OSError as e:
            self.logger.warning(f"Couldn't save {self.state_file}: {e}")

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

    def _current(self, device: zigbee.ZigBeeDevice) -> dict:
        return device.state.properties | self.written.get(device.ieee_address, {})

    # Reconcile.

    def _schedule_reconcile(self, delay: float) -> None:
        # Restart from the top on every change; finished switches have nothing left to send.
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
        changed = commands = 0
        try:
            for device in interleave(switches):
                ieee, i = device.ieee_address, index[device.ieee_address]
                look = self._desired(device, now)
                current = self._current(device)
                params = {k: v for k, v in indicator(look, i).items() if current.get(k) != v}
                want = notification(look, i)
                # Indicator first, so an animation never drops back to the old color. One key
                # per message: Zigbee2MQTT's Inovelli converter applies only the first key of a
                # multi-key set and drops the rest.
                payloads = [{k: v} for k, v in params.items()]
                payloads += notification_payloads(want, self.notifications.get(ieee, "unknown"))
                if not payloads:
                    continue
                for payload in payloads:
                    self._send(device, payload)
                    await asyncio.sleep(PACE_S)
                self.written.setdefault(ieee, {}).update(params)
                self.notifications[ieee] = want
                changed += 1
                commands += len(payloads)
        finally:
            if changed:
                self._save_notifications()
                self.logger.info(
                    f"Updated LED bars on {changed} of {len(switches)} switches ({commands} commands)"
                )

    def _arm_next_expiry(self, now: float) -> None:
        expiries = [r.expires_at for r in self.requests.values() if r.expires_at]
        if expiries:
            self._loop.call_later(max(0.0, min(expiries) - now) + 0.1, self._schedule_reconcile, 0)

    async def _resync_forever(self) -> None:
        """Compare against what the switches report, so anything that drifted (a change in the
        Zigbee2MQTT UI, a missed write) is put back."""
        while True:
            await asyncio.sleep(RESYNC_S)
            self.written.clear()
            self._schedule_reconcile(delay=0)

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
            "default_entity_id": "light.switch_led_bars",
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
        """A switch that rejoins (power cut, re-pair) keeps its indicator parameters but loses
        its notification; send that again."""
        try:
            event = json.loads(payload)
        except ValueError:
            return
        if event.get("type") not in ("device_announce", "device_joined"):
            return
        ieee = str((event.get("data") or {}).get("ieee_address", ""))
        self._loop.call_soon_threadsafe(self._on_rejoin, ieee)

    def _on_rejoin(self, ieee: str) -> None:
        if self.notifications.get(ieee) is None:
            return
        self.notifications[ieee] = None
        self._loop.call_later(REJOIN_SETTLE_S, self._schedule_reconcile, 0)
