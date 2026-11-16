import asyncio
import json
import sys

import pytest

from src import lights_app, main, mqtt
from src.hvac import app as hvac_app


class FakeLights:
    started = 0

    def __init__(self, logger, addon_config, app_config):
        pass

    async def initialize(self):
        FakeLights.started += 1


class BrokenHvac:
    def __init__(self, logger, addon_config, app_config):
        raise RuntimeError("hvac exploded")


class ConnectedMqtt:
    is_connected = True

    def __init__(self, logger, addon_config):
        pass


OPTIONS = {
    "log_level": "info",
    "zigbee_base_topics": [],
    "apps": [{"name": "hvac", "enabled": True}, {"name": "lights", "enabled": True}],
}


@pytest.fixture
def manager(tmp_path, monkeypatch):
    (tmp_path / "options.local.json").write_text(json.dumps(OPTIONS))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(lights_app, "LightsApp", FakeLights)
    monkeypatch.setattr(mqtt, "MqttClient", ConnectedMqtt)
    monkeypatch.setattr(main.AppManager, "_start_http_health", lambda self: asyncio.sleep(0))
    FakeLights.started = 0
    return main.AppManager()


def _assert_lights_up(m):
    assert FakeLights.started == 1
    assert [type(a) for a in m.apps] == [FakeLights]
    assert m.background_apps == []
    assert m._compute_health_sync() == (200, "connected\n")


def test_lights_starts_when_hvac_raises_in_init(manager, monkeypatch):
    monkeypatch.setattr(hvac_app, "HvacApp", BrokenHvac)
    asyncio.run(manager.initialize())
    _assert_lights_up(manager)


def test_lights_starts_when_hvac_cannot_import(manager, monkeypatch):
    monkeypatch.setitem(sys.modules, "src.hvac.app", None)
    asyncio.run(manager.initialize())
    _assert_lights_up(manager)


def test_lights_starts_without_supervisor_token(manager, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    asyncio.run(manager.initialize())
    _assert_lights_up(manager)
