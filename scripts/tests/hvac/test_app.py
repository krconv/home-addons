import asyncio
import json
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.hvac import app as hvac_app
from src.hvac.app import HvacApp
from src.hvac.ha import RestClient


class FailingClient:
    def __init__(self):
        self.reads = 0

    async def get_state(self, entity_id):
        self.reads += 1
        raise ConnectionError("core API down")

    async def call_service(self, service, data):
        raise AssertionError("no service call without an observation")

    async def set_state(self, entity_id, state, attributes):
        raise AssertionError("no status without an observation")


def test_tick_errors_are_logged_and_the_loop_keeps_going(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(hvac_app, "TICK_S", 0.01)
    client = FailingClient()
    app = HvacApp(logging.getLogger("t.hvac"), {}, {}, client=client,
                  clock=lambda: datetime(2026, 11, 16, 12, 0), state_dir=str(tmp_path))

    async def run():
        await app.initialize()
        await asyncio.sleep(0.1)
        assert not app._task.done()
        app._task.cancel()

    with caplog.at_level(logging.ERROR):
        asyncio.run(run())
    assert client.reads > 13  # more than one tick's worth of reads
    assert "hvac tick failed" in caplog.text


class HangingClient(FailingClient):
    async def get_state(self, entity_id):
        self.reads += 1
        await asyncio.sleep(3600)


def test_a_hung_tick_times_out_and_the_loop_keeps_going(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(hvac_app, "TICK_S", 0.01)
    monkeypatch.setattr(hvac_app, "TICK_TIMEOUT_S", 0.02)
    client = HangingClient()
    app = HvacApp(logging.getLogger("t.hvac"), {}, {}, client=client,
                  clock=lambda: datetime(2026, 11, 16, 12, 0), state_dir=str(tmp_path))

    async def run():
        await app.initialize()
        await asyncio.sleep(0.2)
        assert not app._task.done()
        app._task.cancel()

    with caplog.at_level(logging.WARNING):
        asyncio.run(run())
    assert client.reads > 13
    assert "timed out after 0.02s in step observe" in caplog.text


def test_dry_run_is_the_default():
    app = HvacApp(logging.getLogger("t.hvac"), {}, {}, client=FailingClient(), state_dir="/tmp/unused")
    assert app.dry_run and app.cfg.dry_run


class CoreApi(BaseHTTPRequestHandler):
    states = {"input_boolean.hvac_lab_enabled": {"state": "on", "attributes": {}}}
    posts: list = []

    def do_GET(self):
        entity = self.path.removeprefix("/api/states/")
        if self.headers["Authorization"] != "Bearer tok" or entity not in self.states:
            self._reply(404 if entity not in self.states else 401, {"message": "no"})
        else:
            self._reply(200, {"entity_id": entity, **self.states[entity]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.posts.append((self.path, body))
        self._reply(200, [])

    def _reply(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


@pytest.fixture
def core_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), CoreApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    CoreApi.posts = []
    yield f"http://127.0.0.1:{server.server_port}/api"
    server.shutdown()


def test_rest_client_against_the_core_api_shape(core_api):
    async def run():
        c = RestClient("tok", core_api)
        assert (await c.get_state("input_boolean.hvac_lab_enabled"))["state"] == "on"
        assert await c.get_state("climate.nope") is None
        await c.call_service("climate/set_temperature", {"entity_id": "climate.basement", "temperature": 64})
        await c.set_state("sensor.hvac_lab_status", "baseline/day", {"night": "2026-11-16"})
        with pytest.raises(Exception):
            await RestClient("bad", core_api).get_state("input_boolean.hvac_lab_enabled")

    asyncio.run(run())
    assert CoreApi.posts == [
        ("/api/services/climate/set_temperature", {"entity_id": "climate.basement", "temperature": 64}),
        ("/api/states/sensor.hvac_lab_status", {"state": "baseline/day", "attributes": {"night": "2026-11-16"}}),
    ]
