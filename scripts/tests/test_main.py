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


@pytest.fixture
def manager_with_controller(tmp_path, monkeypatch, manager):
    opts = {**OPTIONS, "apps": [{"name": "hvac_controller", "enabled": True}, {"name": "lights", "enabled": True}]}
    (tmp_path / "options.local.json").write_text(json.dumps(opts))
    return main.AppManager()


def test_lights_starts_when_hvac_controller_raises_in_init(manager_with_controller, monkeypatch):
    from src.hvac_controller import app as controller_app
    monkeypatch.setattr(controller_app, "ControllerApp", BrokenHvac)
    asyncio.run(manager_with_controller.initialize())
    _assert_lights_up(manager_with_controller)


def test_lights_starts_when_hvac_controller_cannot_import(manager_with_controller, monkeypatch):
    monkeypatch.setitem(sys.modules, "src.hvac_controller.app", None)
    asyncio.run(manager_with_controller.initialize())
    _assert_lights_up(manager_with_controller)


def test_hvac_controller_is_a_background_app():
    assert "hvac_controller" in main.BACKGROUND_APPS
