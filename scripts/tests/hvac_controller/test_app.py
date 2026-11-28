import asyncio
import json
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.hvac_controller import app as controller_app
from src.hvac_controller.app import ControllerApp, RestHttp

NOW = datetime(2026, 11, 30, 10, 0)
ISO = "2026-11-30T15:00:00+00:00"


def _st(entity, state, **attrs):
    return {"entity_id": entity, "state": state, "attributes": attrs,
            "last_changed": "2026-11-30T05:00:00+00:00", "last_updated": ISO, "last_reported": ISO}


class CoreApi(BaseHTTPRequestHandler):
    states = [
        _st("climate.east_wing", "heat", temperature=68, current_temperature=67),
        _st("climate.west_wing", "heat", temperature=66, current_temperature=66),
        _st("climate.first_floor", "heat", temperature=66, current_temperature=68),
        _st("climate.basement", "heat", temperature=68, current_temperature=68),
        _st("sensor.main_bedroom_eye_level_temperature", "65.1"),
        _st("sensor.unitil_time_of_use_band", "Mid-peak"),
    ]
    posts: list = []

    def do_GET(self):
        self._reply(200 if self.path == "/api/states" else 404, self.states)

    def do_POST(self):
        self.posts.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        self._reply(200, [])

    def _reply(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
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


def _app(core_api, tmp_path, opts):
    return ControllerApp(logging.getLogger("t.ctl"), opts, {}, http=RestHttp("tok", core_api),
                         clock=lambda: NOW, state_dir=str(tmp_path))


def test_dry_run_is_the_default_and_posts_only_its_status_over_rest(core_api, tmp_path):
    app = _app(core_api, tmp_path, {})
    app.load()
    asyncio.run(app.tick())
    assert app.dry_run and [p for p, _ in CoreApi.posts] == ["/api/states/sensor.hvac_controller_status"]
    status = CoreApi.posts[0][1]
    assert status["state"] == "rules (dry)"
    assert status["attributes"]["circuits"]["HP"]["rule"] == "guard.floor"   # main bedroom 65.1
    journal = [json.loads(line) for line in open(app.log_path)]
    assert {j["kind"] for j in journal} == {"intent", "sample"}
    assert app.log_path.startswith(str(tmp_path / "shadow"))


def test_live_sends_mode_then_setpoint(core_api, tmp_path):
    app = _app(core_api, tmp_path, {"hvac_controller": {"dry_run": False}})
    app.load()
    asyncio.run(app.tick())
    # The kill switch helper is missing, so live reads as disabled: every thermostat to its hold, once.
    sent = [(p, b) for p, b in CoreApi.posts if p.startswith("/api/services/")]
    first = [x for x in sent if x[1]["entity_id"] == "climate.first_floor"]
    assert first == [("/api/services/climate/set_hvac_mode", {"entity_id": "climate.first_floor", "hvac_mode": "heat"}),
                     ("/api/services/climate/set_temperature", {"entity_id": "climate.first_floor", "temperature": 68})]
    assert all(b.get("hvac_mode", "heat") == "heat" for _, b in sent)


def test_tick_errors_are_logged_and_the_loop_keeps_going(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(controller_app, "TICK_S", 0.01)
    app = ControllerApp(logging.getLogger("t.ctl"), {}, {}, http=RestHttp("tok", "http://127.0.0.1:9/api", 0.5),
                        clock=lambda: NOW, state_dir=str(tmp_path))

    async def run():
        await app.initialize()
        await asyncio.sleep(0.2)
        assert not app._task.done()
        app._task.cancel()

    with caplog.at_level(logging.ERROR):
        asyncio.run(run())
    assert "hvac_controller tick failed in step read" in caplog.text
