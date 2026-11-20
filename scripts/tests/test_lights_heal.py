import asyncio
import datetime
import logging

from src import lights_app, zigbee
from src.lights_app import LightCircuit, LightCircuitHealth, LightDevice, SwitchDevice

BULB, GONE, SWITCH = "aa:00", "bb:00", "cc:00"
NOON = datetime.datetime(2026, 11, 20, 12, 0)
NIGHT = datetime.datetime(2026, 11, 20, 23, 0)
# ZigBeeClient is wrapped by utils.singleton; the class itself lives in the wrapper's closure.
ZigBeeClientClass = next(c.cell_contents for c in zigbee.ZigBeeClient.__closure__ if isinstance(c.cell_contents, type))


def device(ieee):
    return zigbee.ZigBeeDevice(
        base_topic="zigbee2mqtt-a", type="Router", ieee_address="0x" + ieee.replace(":", ""),
        network_address=1, friendly_name=ieee, interview_completed=True, interviewing=False,
        supported=True, state=zigbee.ZigBeeDeviceState(),
    )


def client(*ieees):
    c = ZigBeeClientClass.__new__(ZigBeeClientClass)
    c.logger = logging.getLogger("test")
    c._devices_by_ieee = {i: device(i) for i in ieees}
    return c


def app(circuit_id="kitchen"):
    a = lights_app.LightsApp.__new__(lights_app.LightsApp)
    a.logger = logging.getLogger("test")
    a._zigbee = client(BULB, SWITCH)
    a.resets = []

    async def reset(circuit, unresponsive, missing=()):
        a.resets.append(list(missing))

    async def health(circuit):
        missing_lights = a._zigbee.missing_ieees([d.ieee for d in circuit.lights])
        return LightCircuitHealth(unresponsive_devices=[], ungrouped_devices=[],
                                  missing_lights=missing_lights, is_healthy=not missing_lights)

    a._reset_and_reconnect_circuit = reset
    a._get_circuit_health = health
    circuit = LightCircuit(id=circuit_id, group_id="a1",
                           lights=[LightDevice(ieee=BULB), LightDevice(ieee=GONE)],
                           switches=[SwitchDevice(ieee=SWITCH, type="hardwired")])
    return a, circuit


def test_missing_devices_are_skipped_not_raised():
    c = client(BULB)
    assert [d.friendly_name for d in c.get_devices_by_ieee([BULB, GONE])] == [BULB]
    assert c.missing_ieees([BULB, GONE]) == [GONE]


def test_wait_for_devices_sees_a_rejoin():
    c = client()

    async def run():
        async def rejoin():
            await asyncio.sleep(0.01)
            c._devices_by_ieee[GONE] = device(GONE)
        asyncio.get_running_loop().create_task(rejoin())
        return await c.wait_for_devices([GONE], timeout=10)

    assert asyncio.run(run()) == []


def test_a_missing_light_triggers_a_rejoin_reset():
    a, circuit = app()
    assert asyncio.run(a._heal_circuit_if_needed(circuit, NOON)) is True
    assert a.resets == [[GONE]]


def test_a_missing_bedroom_light_waits_for_quiet_hours_to_end():
    a, circuit = app("main_bedroom")
    assert asyncio.run(a._heal_circuit_if_needed(circuit, NIGHT)) is False
    assert a.resets == []
