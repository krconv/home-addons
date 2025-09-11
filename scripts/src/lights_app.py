import asyncio
import datetime
import logging
import math
import os
import typing

import pydantic
import yaml

from . import zigbee

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


class LightsApp:
    """Main app for managing home lighting with adaptive features and health monitoring."""

    _zigbee: zigbee.ZigBeeClient
    _config: LightsConfig
    _lighting_lock: asyncio.Lock = asyncio.Lock()
    _health_lock: asyncio.Lock = asyncio.Lock()

    def __init__(self, logger: logging.Logger, addon_config: dict, app_config: dict):
        self.logger = logger
        self.addon_config = addon_config
        self.app_config = app_config

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

        await self._setup_schedulers()

    async def _setup_schedulers(self):
        """Setup timers for lighting updates and health checks."""
        asyncio.create_task(self._lighting_loop())
        asyncio.create_task(self._health_loop())

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
        """Run health checks aligned to every 15-minute mark, skipping quiet hours."""
        await asyncio.sleep(self._seconds_until_next_interval(15))
        while True:
            try:
                now = datetime.datetime.now()
                if self._is_within_quiet_hours(now.time()):
                    self.logger.info("Skipping health checks during quiet hours (18:00-08:00)")
                else:
                    await self._run_healthchecks(now)
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
            for (brightness, temperature), circuit in calculated_lighting:
                await self._update_circuit_lighting(
                    circuit, brightness, temperature, default_transition
                )

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
                if await self._heal_circuit_if_needed(circuit):
                    # After a repair, quickly bring lights back to the desired state
                    await self._update_circuit_lighting(
                        circuit, brightness, temperature, 1
                    )

    def _calculate_circuit_lighting(
        self, circuit: LightCircuit, now: datetime.datetime
    ) -> tuple[int, int]:
        brightness_pct, temperature_k = self._get_scheduled_lighting_values(now.time())

        return self._map_lighting_for_circuit(circuit, brightness_pct, temperature_k)

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
        await self._zigbee.set_property(
            group, "color_temp", temperature, transition=transition
        )

    async def _heal_circuit_if_needed(self, circuit: LightCircuit) -> bool:
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

                await asyncio.sleep(120)

                unresponsive_devices = await self._zigbee.get_unresponsive_devices(
                    devices_to_check=unresponsive_devices
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
