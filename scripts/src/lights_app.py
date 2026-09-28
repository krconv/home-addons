import asyncio
import datetime
import json
import logging
import math
import os
import random
import typing

import pydantic
import yaml

from . import mqtt, zigbee


class LightDevice(pydantic.BaseModel):
    ieee: str


class SwitchDevice(pydantic.BaseModel):
    ieee: str
    type: typing.Literal["hardwired"] | None = None


class LightCircuit(pydantic.BaseModel):
    id: str

    @pydantic.computed_field
    @property
    def friendly_name(self) -> str:
        return self.id.replace("_", " ").title()

    group_id: str
    lights: list[LightDevice] = []
    switches: list[SwitchDevice]


class LightCircuitHealth(pydantic.BaseModel):
    unresponsive_devices: list[zigbee.ZigBeeDevice]
    ungrouped_devices: list[zigbee.ZigBeeDevice]

    is_healthy: bool


class LightSchedule(pydantic.BaseModel):
    time: str | int
    brightness: int
    temperature: int
    transition: str  # "10s", "1m", "1h" etc.


class LightsConfig(pydantic.BaseModel):
    circuits: list[LightCircuit]
    schedule: list[LightSchedule]


class NightLight(pydantic.BaseModel):
    light: str  # ieee of the one bulb to keep on, colon form as in lights.yaml
    brightness: int = 1  # raw zigbee level 1..254

    @pydantic.field_validator("brightness")
    @classmethod
    def _clamp_brightness(cls, value: int) -> int:
        return max(1, min(254, value))


class CircuitMode(pydantic.BaseModel):
    mode: typing.Literal["auto", "nightlight"] = "auto"
    nightlight: NightLight | None = None

    @pydantic.model_validator(mode="after")
    def _nightlight_matches_mode(self) -> "CircuitMode":
        if (self.mode == "nightlight") != (self.nightlight is not None):
            raise ValueError("nightlight settings are required iff mode is nightlight")
        return self


# Grace window before enforcing lights == switch, so a legitimate paddle press
# whose light report arrives before the switch's own report isn't reverted.
SYNC_GRACE_SECONDS = 2

MODE_TOPIC_PREFIX = "scripts/lights"
# The schedule is skipped in night-light mode, so pin a warm white (mireds).
NIGHTLIGHT_COLOR_TEMP = 370


class LightsApp:
    """Main app for managing home lighting with adaptive features and health monitoring."""

    _zigbee: zigbee.ZigBeeClient
    _mqtt: mqtt.MqttClient
    _config: LightsConfig
    _lighting_lock: asyncio.Lock = asyncio.Lock()
    _health_lock: asyncio.Lock = asyncio.Lock()

    def __init__(self, logger: logging.Logger, addon_config: dict, app_config: dict):
        self.logger = logger
        self.addon_config = addon_config
        self.app_config = app_config
        self._last_sent: dict[str, tuple[int, int]] = {}

    async def initialize(self) -> None:
        """Initialize the app and its components."""

        # Load lights configuration from file
        config_file = self.app_config.get("config_file", "/config/lights.yaml")
        if not os.path.exists(config_file):
            self.logger.error(f"Lights config file not found: {config_file}")
            raise FileNotFoundError(f"Config file not found: {config_file}")

        with open(config_file, "r") as file:
            config_data = yaml.safe_load(file)

        self._config = LightsConfig(**config_data)
        self.logger.info(f"Loaded {len(self._config.circuits)} circuits from config")

        self._zigbee = zigbee.ZigBeeClient(self.logger, self.addon_config)
        await self._zigbee.initialize()

        self._loop = asyncio.get_running_loop()
        self._sync_tasks: dict[str, asyncio.Task] = {}
        self._mode_tasks: set[asyncio.Task] = set()
        self._healing_circuits: set[str] = set()
        self._modes: dict[str, CircuitMode] = {}
        self._circuits_by_id = {c.id: c for c in self._config.circuits}
        self._circuits_by_ieee = self._build_circuit_lookup()
        self._hardwired_switch_by_circuit: dict[str, str] = {}
        self._switch_states: dict[str, str] = {}
        for circuit in self._config.circuits:
            if not self._is_sync_eligible(circuit):
                continue
            switch = self._zigbee.get_device_by_ieee(
                next(s.ieee for s in circuit.switches if s.type == "hardwired")
            )
            self._hardwired_switch_by_circuit[circuit.id] = switch.ieee_address
            if (state := switch.state.properties.get("state")) is not None:
                self._switch_states[circuit.id] = state
        self._zigbee.add_state_listener(self._on_device_state)

        # MqttClient is a singleton, so this is the ZigBeeClient's connected instance.
        self._mqtt = mqtt.MqttClient(self.logger, self.addon_config)
        self._mqtt.subscribe(f"{MODE_TOPIC_PREFIX}/+/set", self._on_mode_command)
        for circuit in self._config.circuits:
            self._publish_mode_state(circuit)

        await self._setup_schedulers()

    async def _setup_schedulers(self):
        """Setup timers for lighting updates and health checks."""
        asyncio.create_task(self._lighting_loop())
        asyncio.create_task(self._health_loop())

    def _build_circuit_lookup(self) -> dict[str, LightCircuit]:
        """Map device ieee -> circuit, for circuits eligible for switch sync."""
        lookup: dict[str, LightCircuit] = {}
        for circuit in self._config.circuits:
            if not circuit.lights:
                continue
            if not self._is_sync_eligible(circuit):
                self.logger.warning(
                    f"Circuit {circuit.friendly_name} has no hardwired switch; "
                    "skipping switch state sync"
                )
                continue
            # Key by the device object's ieee_address (raw 0x... form) since
            # that's what state listeners receive; config ieees use the
            # colon-separated registry form.
            for device in self._zigbee.get_devices_by_ieee(
                [d.ieee for d in [*circuit.lights, *circuit.switches]]
            ):
                lookup[device.ieee_address] = circuit
        return lookup

    def _is_sync_eligible(self, circuit: LightCircuit) -> bool:
        return bool(circuit.lights) and any(
            s.type == "hardwired" for s in circuit.switches
        )

    def _on_device_state(self, device: zigbee.ZigBeeDevice, data: dict) -> None:
        """State listener; runs on the MQTT client thread."""
        if "state" not in data:
            return
        circuit = self._circuits_by_ieee.get(device.ieee_address)
        if circuit is None:
            return
        # zigbee2mqtt republishes the whole cached state on any attribute
        # report, so only a change in the switch's state counts as a press.
        switch_changed = False
        if device.ieee_address == self._hardwired_switch_by_circuit.get(circuit.id):
            previous = self._switch_states.get(circuit.id)
            self._switch_states[circuit.id] = data["state"]
            switch_changed = previous is not None and previous != data["state"]
        self._loop.call_soon_threadsafe(
            self._on_circuit_report, circuit, switch_changed
        )

    def _on_circuit_report(self, circuit: LightCircuit, switch_changed: bool) -> None:
        if circuit.id in self._healing_circuits:
            return
        if switch_changed and self._nightlight_for(circuit) is not None:
            # A paddle press (or HA command to the switch) ends night-light;
            # bulb reports only reconcile towards the night-light target.
            self._create_mode_task(self._set_mode(circuit, CircuitMode()))
            return
        self._schedule_circuit_sync(circuit)

    def _schedule_circuit_sync(self, circuit: LightCircuit) -> None:
        if circuit.id in self._healing_circuits:
            return
        pending = self._sync_tasks.get(circuit.id)
        if pending is not None and not pending.done():
            # The pending reconcile re-reads live state when it fires.
            return
        self._sync_tasks[circuit.id] = asyncio.create_task(
            self._sync_circuit_to_switch(circuit)
        )

    def _cancel_pending_sync(self, circuit: LightCircuit) -> None:
        pending = self._sync_tasks.pop(circuit.id, None)
        if pending is not None:
            pending.cancel()

    def _create_mode_task(
        self, coro: typing.Coroutine[typing.Any, typing.Any, None]
    ) -> None:
        task = asyncio.create_task(coro)
        self._mode_tasks.add(task)
        task.add_done_callback(self._mode_tasks.discard)

    def _nightlight_for(self, circuit: LightCircuit) -> NightLight | None:
        return self._modes.get(circuit.id, CircuitMode()).nightlight

    def _on_mode_command(self, topic: str, payload: str) -> None:
        """Mode command listener; runs on the MQTT client thread."""
        circuit_id = topic.split("/")[-2]
        circuit = self._circuits_by_id.get(circuit_id)
        if circuit is None:
            self.logger.warning(f"Mode command for unknown circuit {circuit_id!r}")
            return
        try:
            new_mode = self._parse_mode_command(payload)
        except (json.JSONDecodeError, pydantic.ValidationError) as e:
            self.logger.warning(
                f"Ignoring invalid mode command for {circuit.friendly_name}: "
                f"{payload!r} ({e})"
            )
            return
        self._loop.call_soon_threadsafe(
            self._create_mode_task, self._set_mode(circuit, new_mode)
        )

    def _parse_mode_command(self, payload: str) -> CircuitMode:
        data = json.loads(payload)
        if isinstance(data, dict) and data.get("mode") == "nightlight":
            return CircuitMode(
                mode="nightlight", nightlight=NightLight.model_validate(data)
            )
        return CircuitMode.model_validate(data)

    def _publish_mode_state(self, circuit: LightCircuit) -> None:
        self._mqtt.publish(
            f"{MODE_TOPIC_PREFIX}/{circuit.id}/state",
            self._modes.get(circuit.id, CircuitMode()).model_dump(),
            retain=True,
        )

    async def _set_mode(self, circuit: LightCircuit, new_mode: CircuitMode) -> None:
        if new_mode == self._modes.get(circuit.id, CircuitMode()):
            return

        if new_mode.nightlight is not None:
            if not self._is_sync_eligible(circuit):
                self.logger.warning(
                    f"Refusing night-light for {circuit.friendly_name}: circuit "
                    "needs lights and a hardwired switch"
                )
                return
            if new_mode.nightlight.light not in {l.ieee for l in circuit.lights}:
                self.logger.warning(
                    f"Refusing night-light for {circuit.friendly_name}: "
                    f"{new_mode.nightlight.light} is not one of its lights"
                )
                return

        self._modes[circuit.id] = new_mode
        self._publish_mode_state(circuit)
        self.logger.info(
            f"Circuit {circuit.friendly_name} mode -> {new_mode.mode}"
            + (
                f" ({new_mode.nightlight.light} @ {new_mode.nightlight.brightness})"
                if new_mode.nightlight is not None
                else ""
            )
        )

        self._cancel_pending_sync(circuit)
        if new_mode.nightlight is not None:
            await self._apply_nightlight(circuit, new_mode.nightlight)
        else:
            self._schedule_circuit_sync(circuit)

    async def _apply_nightlight(
        self, circuit: LightCircuit, nightlight: NightLight
    ) -> None:
        target = self._zigbee.get_device_by_ieee(nightlight.light)
        self.logger.info(
            f"Applying night-light to {circuit.friendly_name}: "
            f"{target.friendly_name} at level {nightlight.brightness}"
        )
        # Bulbs are switched off one by one rather than via the group: the
        # hardwired switch is a group member, and a group OFF would flip its
        # state and read as a paddle press that ends night-light.
        for light in circuit.lights:
            if light.ieee != nightlight.light:
                await self._zigbee.set_property(
                    self._zigbee.get_device_by_ieee(light.ieee), "state", "OFF"
                )
        # One set so the bulb comes up at the night-light level rather than
        # flashing to its previous level first.
        await self._zigbee.set_properties(
            target,
            {
                "state": "ON",
                "brightness": nightlight.brightness,
                "color_temp": NIGHTLIGHT_COLOR_TEMP,
                "transition": 1,
            },
        )

    async def _sync_circuit_to_nightlight(
        self, circuit: LightCircuit, nightlight: NightLight
    ) -> None:
        mismatched: list[str] = []
        for light in circuit.lights:
            device = self._zigbee.get_device_by_ieee(light.ieee)
            expected = "ON" if light.ieee == nightlight.light else "OFF"
            actual = device.state.properties.get("state")
            if actual not in (None, expected):
                mismatched.append(f"{device.friendly_name} {actual}")
        if not mismatched:
            return

        self.logger.info(
            f"Circuit {circuit.friendly_name} out of sync with night-light "
            f"(mismatched: {', '.join(mismatched)}); re-applying"
        )
        await self._apply_nightlight(circuit, nightlight)

    async def _sync_circuit_to_switch(self, circuit: LightCircuit) -> None:
        """Enforce that a circuit's lights match its hardwired switch state."""
        await asyncio.sleep(SYNC_GRACE_SECONDS)

        if circuit.id in self._healing_circuits:
            return

        nightlight = self._nightlight_for(circuit)
        if nightlight is not None:
            await self._sync_circuit_to_nightlight(circuit, nightlight)
            return

        switch_state = self._get_hardwired_switch_state(circuit)
        if switch_state is None:
            return

        mismatched = [
            light
            for light in self._zigbee.get_devices_by_ieee(
                [light.ieee for light in circuit.lights]
            )
            if light.state.properties.get("state") not in (None, switch_state)
        ]
        if not mismatched:
            return

        self.logger.info(
            f"Circuit {circuit.friendly_name} out of sync with switch "
            f"(switch {switch_state}, mismatched: "
            f"{', '.join(light.friendly_name for light in mismatched)}); "
            f"forcing lights {switch_state}"
        )
        group = self._zigbee.get_group_by_id(circuit.group_id)
        await self._zigbee.set_property(group, "state", switch_state)

        if switch_state == "ON":
            brightness, temperature = self._calculate_circuit_lighting(
                circuit, datetime.datetime.now()
            )
            await self._update_circuit_lighting(circuit, brightness, temperature, 1)

    def _get_hardwired_switch_state(self, circuit: LightCircuit) -> str | None:
        for switch in circuit.switches:
            if switch.type != "hardwired":
                continue
            device = self._zigbee.get_device_by_ieee(switch.ieee)
            return device.state.properties.get("state")
        return None

    async def _lighting_loop(self):
        """Run lighting updates aligned to every 5-minute mark."""
        await asyncio.sleep(self._seconds_until_next_interval(5))
        while True:
            try:
                await self._update_all_circuits_lighting(datetime.datetime.now())
            except Exception as e:
                self.logger.error(f"Error in lighting update loop: {e}")
            await asyncio.sleep(self._seconds_until_next_interval(5))

    async def _health_loop(self):
        """Run health checks aligned to every 15-minute mark."""
        await asyncio.sleep(self._seconds_until_next_interval(15))
        while True:
            try:
                await self._run_healthchecks(datetime.datetime.now())
            except Exception as e:
                self.logger.error(f"Error in health check loop: {e}")
            await asyncio.sleep(self._seconds_until_next_interval(15))

    def _seconds_until_next_interval(self, minutes_interval: int) -> float:
        """Return seconds until the next aligned N-minute boundary."""
        now = datetime.datetime.now()
        seconds_since_hour = now.minute * 60 + now.second + now.microsecond / 1_000_000
        interval_seconds = minutes_interval * 60
        remaining = interval_seconds - (seconds_since_hour % interval_seconds)
        if remaining == 0:
            remaining = interval_seconds
        return remaining

    def _is_within_quiet_hours(self, t: datetime.time) -> bool:
        """Return True if time is between 18:00 and 08:00 (inclusive of 18:00)."""
        start_quiet = datetime.time(18, 0)
        end_quiet = datetime.time(8, 0)
        return t >= start_quiet or t < end_quiet

    def _is_bedroom(self, circuit: LightCircuit) -> bool:
        """Return True if the circuit belongs to a bedroom."""
        return "bedroom" in circuit.id.lower()

    async def _update_all_circuits_lighting(self, now: datetime.datetime) -> None:
        if self._lighting_lock.locked():
            return
        async with self._lighting_lock:
            self.logger.info("Updating lighting for all circuits")
            default_transition = 30  # seconds
            calculated_lighting = [
                (self._calculate_circuit_lighting(circuit, now), circuit)
                for circuit in self._config.circuits
            ]
            tasks = []

            async def sleep_then_update(
                circuit: LightCircuit, brightness: int, temperature: int
            ) -> None:
                await asyncio.sleep(random.uniform(0, 60))
                if (
                    circuit.id in self._healing_circuits
                    or self._nightlight_for(circuit) is not None
                ):
                    return
                await self._update_circuit_lighting(
                    circuit, brightness, temperature, default_transition
                )

            for (brightness, temperature), circuit in calculated_lighting:
                if self._needs_lighting_update(circuit, brightness, temperature):
                    tasks.append(sleep_then_update(circuit, brightness, temperature))

            if tasks:
                await asyncio.gather(*tasks)

    async def _run_healthchecks(self, now: datetime.datetime) -> None:
        if self._health_lock.locked():
            return
        async with self._health_lock:
            self.logger.info("Running health checks for all circuits")
            calculated_lighting = [
                (self._calculate_circuit_lighting(circuit, now), circuit)
                for circuit in self._config.circuits
            ]
            for (brightness, temperature), circuit in calculated_lighting:
                # Block switch-state reconciles and scheduled lighting updates
                # while a circuit is being checked/healed: the heal path
                # power-cycles switches, which would otherwise look like
                # mismatches and trigger competing commands.
                self._healing_circuits.add(circuit.id)
                self._cancel_pending_sync(circuit)
                try:
                    if await self._heal_circuit_if_needed(circuit, now):
                        # After a repair, quickly bring lights back to the desired state
                        nightlight = self._nightlight_for(circuit)
                        if nightlight is not None:
                            await self._apply_nightlight(circuit, nightlight)
                        else:
                            await self._update_circuit_lighting(
                                circuit, brightness, temperature, 1
                            )
                finally:
                    self._healing_circuits.discard(circuit.id)

    def _calculate_circuit_lighting(
        self, circuit: LightCircuit, now: datetime.datetime
    ) -> tuple[int, int]:
        brightness_pct, temperature_k = self._get_scheduled_lighting_values(now.time())

        return self._map_lighting_for_circuit(circuit, brightness_pct, temperature_k)

    def _needs_lighting_update(
        self, circuit: LightCircuit, brightness: int, temperature: int
    ) -> bool:
        last = self._last_sent.get(circuit.id)
        if last is None:
            return True

        last_brightness, last_temperature = last
        brightness_changed = abs(brightness - last_brightness) >= 255 * 0.01
        temperature_changed = (
            last_temperature > 0
            and abs(temperature - last_temperature) / last_temperature >= 0.01
        )
        return brightness_changed or temperature_changed

    def _get_scheduled_lighting_values(
        self, current_time: datetime.time
    ) -> tuple[float, int]:
        """Get current brightness and temperature from schedule based on time.

        Returns:
            tuple: (brightness_percentage, temperature_kelvin)
        """
        schedule = sorted(
            self._config.schedule, key=lambda x: self._time_to_minutes(x.time)
        )
        current_minutes = self._time_to_minutes(current_time)

        for i, next_entry in enumerate(schedule):
            is_first_entry = i == 0
            next_minutes = self._time_to_minutes(next_entry.time)
            prev_entry = schedule[-1] if is_first_entry else schedule[i - 1]
            prev_minutes = self._time_to_minutes(prev_entry.time)
            transition_minutes = self._duration_to_minutes(next_entry.transition)

            if is_first_entry:
                prev_minutes -= 24 * 60  # wrap backwards to previous day

            if current_minutes < prev_minutes or current_minutes > next_minutes:
                continue

            transition_elapsed_pct = max(
                0.0, 1 - ((next_minutes - current_minutes) / float(transition_minutes))
            )

            return (
                float(
                    self._apply_transition(
                        prev_entry.brightness / 100.0,
                        next_entry.brightness / 100.0,
                        transition_elapsed_pct,
                    )
                ),
                int(
                    self._apply_transition(
                        prev_entry.temperature,
                        next_entry.temperature,
                        transition_elapsed_pct,
                    )
                ),
            )

        return schedule[-1].brightness / 100.0, schedule[-1].temperature

    def _time_to_minutes(self, time: str | int | datetime.time) -> int:
        """Convert time to minutes since midnight."""
        if isinstance(time, int):
            time = datetime.time(hour=time // 100, minute=time % 100)
        elif isinstance(time, str):
            time = datetime.time.fromisoformat(time)

        return time.hour * 60 + time.minute

    def _duration_to_minutes(self, duration: str) -> int:
        if duration.endswith("s"):
            return int(duration[:-1]) // 60
        elif duration.endswith("m"):
            return int(duration[:-1])
        elif duration.endswith("h"):
            return int(duration[:-1]) * 60
        else:
            raise ValueError(f"Invalid duration format: {duration}")

    def _apply_transition(
        self, start_value: int | float, end_value: int | float, elapsed_pct: float
    ) -> int | float:
        value = start_value + (end_value - start_value) * elapsed_pct
        if isinstance(start_value, int):
            return round(value)
        return value

    def _map_lighting_for_circuit(
        self, circuit: LightCircuit, brightness_pct: float, temperature_k: int
    ) -> tuple[int, int]:
        has_smart_lights = circuit.lights

        brightness = round(brightness_pct * 255)
        temperature = round(1000000 / temperature_k)

        lights = self._zigbee.get_devices_by_ieee(
            [light.ieee for light in circuit.lights]
        )
        if has_smart_lights and all(
            [light.model_id == "ABL-LIGHT-Z-001" for light in lights]
        ):
            max_lux = 52.24734230107197
            lux = max_lux * brightness_pct
            brightness = round(math.exp((lux - 4.26) / 8.66))

        return brightness, temperature

    async def _update_circuit_lighting(
        self, circuit: LightCircuit, brightness: int, temperature: int, transition: int
    ):
        group = self._zigbee.get_group_by_id(circuit.group_id)

        await self._zigbee.set_property(
            group, "brightness", brightness, transition=transition
        )
        if circuit.lights:
            await self._zigbee.set_property(
                group, "color_temp", temperature, transition=transition
            )
        else:
            # Dimmer switches driving dumb bulbs: no color support, and the
            # turn-on level is governed by the switch's default-level settings
            # (device-specific attributes, so they can't be set via the group).
            # Non-hardwired switches get them too since they set the switch's
            # own light intensity.
            default_level = max(1, min(254, brightness))
            for switch in circuit.switches:
                device = self._zigbee.get_device_by_ieee(switch.ieee)
                await self._zigbee.set_property(
                    device, "defaultLevelLocal", default_level
                )
                await self._zigbee.set_property(
                    device, "defaultLevelRemote", default_level
                )
        self._last_sent[circuit.id] = (brightness, temperature)

    async def _heal_circuit_if_needed(
        self, circuit: LightCircuit, now: datetime.datetime
    ) -> bool:
        health = await self._get_circuit_health(circuit)
        if health.is_healthy:
            return False

        lights = self._zigbee.get_devices_by_ieee(
            [light.ieee for light in circuit.lights]
        )
        switches = self._zigbee.get_devices_by_ieee(
            [switch.ieee for switch in circuit.switches]
        )
        if health.unresponsive_devices:
            if any(device in lights for device in health.unresponsive_devices):
                if self._is_bedroom(circuit) and self._is_within_quiet_hours(
                    now.time()
                ):
                    # Avoid the disruptive power-cycle reset during quiet hours;
                    # just drop the switch out of smart bulb mode so the bulbs are
                    # power-controlled without waking anyone.
                    await self._disable_smart_bulb_mode(
                        circuit, health.unresponsive_devices
                    )
                    return False
                await self._reset_and_reconnect_circuit(
                    circuit, health.unresponsive_devices
                )
            else:
                self.logger.warning(
                    f"Unresponsive devices found in circuit {circuit.friendly_name}, but no lights to reset"
                )
        elif health.ungrouped_devices:
            for device in health.ungrouped_devices:
                group = self._zigbee.get_group_by_id(circuit.group_id)
                await self._zigbee.add_to_group(device, group)

        return True

    async def _disable_smart_bulb_mode(
        self,
        circuit: LightCircuit,
        unresponsive_devices: list[zigbee.ZigBeeDevice],
    ):
        """Disable smart bulb mode on the circuit's hardwired switches.

        Used for bedroom circuits during quiet hours: instead of power-cycling the
        switch to recover an unresponsive bulb (which would wake someone), turn off
        smart bulb mode so the switch cuts power to the bulbs directly.
        """
        hardwired_switches = [
            self._zigbee.get_device_by_ieee(s.ieee)
            for s in circuit.switches
            if s.type == "hardwired"
        ]

        if not hardwired_switches:
            self.logger.error(
                f"No hardwired switch found for circuit {circuit.friendly_name}"
            )
            return
        elif any(switch in unresponsive_devices for switch in hardwired_switches):
            self.logger.error("Switch is unresponsive, cannot disable smart bulb mode")
            return

        self.logger.info(
            f"Disabling smart bulb mode for bedroom circuit {circuit.friendly_name} "
            "during quiet hours to avoid waking someone"
        )
        for switch in hardwired_switches:
            if not await self._zigbee.set_and_verify_property(
                switch, "smartBulbMode", "Disabled"
            ):
                self.logger.error(
                    f"Failed to disable smart mode on {switch.friendly_name}"
                )

    async def _reset_and_reconnect_circuit(
        self,
        circuit: LightCircuit,
        unresponsive_devices: list[zigbee.ZigBeeDevice],
    ):
        """Attempt to recover an unresponsive device."""
        hardwired_switches = [
            self._zigbee.get_device_by_ieee(s.ieee)
            for s in circuit.switches
            if s.type == "hardwired"
        ]

        if not hardwired_switches:
            self.logger.error(
                f"No hardwired switch found for circuit {circuit.friendly_name}"
            )
            return
        elif any(switch in unresponsive_devices for switch in hardwired_switches):
            self.logger.error("Switch is unresponsive, cannot perform reset")
            return

        initial_state = hardwired_switches[0].state.properties["state"]
        self.logger.info(
            f"Captured initial state for {circuit.friendly_name}: {initial_state}"
        )

        self.logger.info(
            f"Starting circuit reset for {circuit.friendly_name} due to unresponsive devices"
        )

        try:
            for switch in hardwired_switches:
                if not await self._zigbee.set_and_verify_property(
                    switch, "smartBulbMode", "Disabled"
                ):
                    self.logger.error(
                        f"Failed to disable smart mode on {switch.friendly_name}"
                    )
                    return

            if not await self._power_cycle_switches(hardwired_switches, 4):
                self.logger.error(
                    f"Failed to power cycle switch {', '.join([s.friendly_name for s in hardwired_switches])}"
                )
                return

            lights = self._zigbee.get_devices_by_ieee(
                [light.ieee for light in circuit.lights]
            )
            unresponsive_devices = (
                lights  # after a reset, all lights will be unresponsive
            )

            for attempt in range(3):
                if not await self._power_cycle_switches(hardwired_switches, 1):
                    continue

                if not await self._zigbee.permit_join(hardwired_switches[0], 120):
                    continue

                unresponsive_devices = await self._zigbee.get_unresponsive_devices(
                    devices_to_check=unresponsive_devices, timeout=120
                )
                if not unresponsive_devices:
                    break

            group = self._zigbee.get_group_by_id(circuit.group_id)
            for device in lights:
                await self._zigbee.add_to_group(device, group)

            self.logger.info(f"Successfully reset circuit {circuit.friendly_name}")

        finally:
            for switch in hardwired_switches:
                await self._zigbee.set_and_verify_property(
                    switch, "smartBulbMode", "Smart Bulb Mode"
                )

            group = self._zigbee.get_group_by_id(circuit.group_id)
            await self._zigbee.set_property(group, "state", initial_state)
            self.logger.info(
                f"Restored {circuit.friendly_name} to original state: {initial_state}"
            )

    async def _power_cycle_switches(
        self, devices: list[zigbee.ZigBeeDevice], cycles: int = 5
    ):
        """Power cycle a switch by turning it off/on multiple times."""
        self.logger.info(
            f"Power cycling switch {', '.join([s.friendly_name for s in devices])} for {cycles} cycles"
        )
        for cycle in range(cycles):
            for device in devices:
                if not await self._zigbee.set_and_verify_property(
                    device, "state", "OFF"
                ):
                    self.logger.error(
                        f"Failed to turn off switch {device.friendly_name}"
                    )
                    return False

            await asyncio.sleep(2)

            for device in devices:
                if not await self._zigbee.set_and_verify_property(
                    device, "state", "ON"
                ):
                    self.logger.error(
                        f"Failed to turn on switch {device.friendly_name}"
                    )
                    return False

            await asyncio.sleep(2)

        return True

    async def _get_circuit_health(self, circuit: LightCircuit) -> LightCircuitHealth:
        devices = self._zigbee.get_devices_by_ieee(
            [
                device.ieee
                for devices in [circuit.lights, circuit.switches]
                for device in typing.cast(list[LightDevice | SwitchDevice], devices)
            ]
        )

        base_topic = f"zigbee2mqtt-{circuit.group_id[0]}"
        group = self._zigbee.get_group_by_id(circuit.group_id)
        for device in devices:
            if device.base_topic != base_topic:
                self.logger.warning(
                    f"Device {device.friendly_name} has wrong base topic. expected {base_topic}, actual {device.base_topic}: {device}"
                )
        if group.base_topic != base_topic:
            self.logger.warning(
                f"Group {group.friendly_name} has wrong base topic. expected {base_topic}, actual {group.base_topic}: {group}"
            )

        ungrouped_devices = await self._zigbee.get_ungrouped_devices(
            group=group, devices_to_check=devices
        )
        unresponsive_devices = await self._zigbee.get_unresponsive_devices(
            devices_to_check=list(ungrouped_devices)
        )

        self.logger.info(
            f"Health check for circuit {circuit.friendly_name}: "
            f"{len(unresponsive_devices)} unresponsive, "
            f"{len(ungrouped_devices)} ungrouped devices"
        )

        return LightCircuitHealth(
            unresponsive_devices=unresponsive_devices,
            ungrouped_devices=ungrouped_devices,
            is_healthy=not unresponsive_devices and not ungrouped_devices,
        )
