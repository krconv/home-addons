"""ControllerApp: the controller's only Home Assistant boundary. Tick every 60 s:
read every state once, sense.read, control.step, send (live only), persist, journal, status.

Dry run is structural: the app is built with a Reader and a StatusPublisher for its own entity,
and no Sender. Nothing it holds can call a service.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Awaitable, Callable, Optional, Protocol

from . import control, sense
from .site import SITE, Site
from .types import Mem, Step, Switch

TICK_S = 60
TICK_TIMEOUT_S = 45
ALIVE_EVERY = 60
SAMPLE_EVERY_MIN = 15
STATE_DIR = "/data/hvac-controller"
SUPERVISOR_CORE_API = "http://supervisor/core/api"

Get = Callable[[str], Awaitable[object]]
Post = Callable[[str, dict], Awaitable[object]]


class Http(Protocol):
    async def get(self, path: str): ...
    async def post(self, path: str, body: dict): ...


class RestHttp:
    """Blocking urllib on a pool of its own, so the shared event loop is never held."""

    def __init__(self, token: str, base_url: str = SUPERVISOR_CORE_API, timeout: float = 10.0):
        self._token, self._base, self._timeout = token, base_url.rstrip("/"), timeout
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hvac-ctl-http")

    @classmethod
    def from_env(cls) -> "RestHttp":
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise RuntimeError("SUPERVISOR_TOKEN is not set; the add-on needs homeassistant_api: true")
        return cls(token)

    async def get(self, path: str):
        return await asyncio.get_running_loop().run_in_executor(self._pool, self._request, "GET", path, None)

    async def post(self, path: str, body: dict):
        return await asyncio.get_running_loop().run_in_executor(self._pool, self._request, "POST", path, body)

    def _request(self, method: str, path: str, body: Optional[dict]):
        data = None if body is None else json.dumps(body, default=str).encode()
        req = urllib.request.Request(self._base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else None


class Reader:
    def __init__(self, get: Get):
        self._get = get

    async def states(self) -> dict[str, dict]:
        """Every state in one request. A transport error raises and skips the tick."""
        return {s["entity_id"]: s for s in await self._get("/states")}


class StatusPublisher:
    """Writes one entity: the controller's own status sensor."""

    def __init__(self, post: Post, entity: str):
        self._post, self._path = post, f"/states/{entity}"

    async def publish(self, state: str, attributes: dict) -> None:
        await self._post(self._path, {"state": state, "attributes": attributes})


class Sender:
    """Live only. A Switch becomes heat mode, then the circuit's hold or call setpoint: the
    thermostats reject both in one call. The boiler gets only its commissioned CH setpoint."""

    def __init__(self, post: Post, site: Site, logger: logging.Logger):
        self._post, self._site, self._logger = post, site, logger

    async def send(self, s: Switch) -> None:
        t = self._site.thermostats[s.source]
        await self._calls((("climate/set_hvac_mode", {"entity_id": t.entity, "hvac_mode": "heat"}),
                           ("climate/set_temperature", {"entity_id": t.entity, "temperature": t.setpoint(s.on)})))

    async def send_ch(self, value: float) -> None:
        await self._calls((("number/set_value", {"entity_id": self._site.boiler_ch, "value": value}),))

    async def _calls(self, calls) -> None:
        for service, data in calls:
            try:
                await self._post(f"/services/{service}", data)
            except Exception as e:  # the next tick reconciles against what the device shows
                self._logger.warning(f"service call failed: {service} {data}: {e!r}")
                return


def local_now() -> datetime:
    return datetime.now(SITE.tz).replace(tzinfo=None)


class ControllerApp:
    def __init__(self, logger: logging.Logger, addon_config: dict, app_config: dict,
                 http: Optional[Http] = None, clock: Callable[[], datetime] = local_now,
                 state_dir: str = STATE_DIR, site: Site = SITE):
        opts = addon_config.get("hvac_controller") or {}
        site.validate()
        self.logger, self.clock, self.site = logger, clock, site
        self.dry_run = bool(opts.get("dry_run", True))
        http = http if http is not None else RestHttp.from_env()
        self.reader = Reader(http.get)
        self.publisher = StatusPublisher(http.post, site.status)
        self.sender = None if self.dry_run else Sender(http.post, site, logger)
        self.state_dir = os.path.join(state_dir, "shadow" if self.dry_run else "live")
        self.mem_path = os.path.join(self.state_dir, "mem.json")
        self.log_path = os.path.join(self.state_dir, "journal.jsonl")
        self.mem = Mem()
        self._task: Optional[asyncio.Task] = None
        self._step = "idle"
        self._ticks = 0
        self._last_sample: Optional[datetime] = None

    async def initialize(self) -> None:
        self.load()
        self._task = asyncio.create_task(self._run(), name="hvac_controller")
        self.logger.info(f"hvac_controller up; dry_run={self.dry_run}; state={self.state_dir}")

    def load(self) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        try:
            with open(self.mem_path) as f:
                self.mem = Mem.from_json(json.load(f))
        except FileNotFoundError:
            self.mem = Mem()
        except (ValueError, TypeError) as e:
            self.logger.warning(f"mem unreadable, starting fresh: {e!r}")
            self.mem = Mem()

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        due = loop.time()
        while True:
            try:
                await asyncio.wait_for(self.tick(), TICK_TIMEOUT_S)
            except asyncio.TimeoutError:
                self.logger.warning(f"hvac_controller tick timed out in step {self._step}")
            except Exception:  # a failed tick never stops the loop or the other apps
                self.logger.exception(f"hvac_controller tick failed in step {self._step}")
            self._ticks += 1
            if self._ticks % ALIVE_EVERY == 0:
                self.logger.info(f"hvac_controller alive: {self._ticks} ticks")
            due += TICK_S
            await asyncio.sleep(max(0.0, due - loop.time()))

    async def tick(self) -> None:
        now = self.clock()
        self._step = "read"
        snap = sense.read(await self.reader.states(), now, self.site)
        self._step = "decide"
        out = control.step(snap, self.mem, self.site, self.dry_run, honor_kill=not self.dry_run)
        if self.sender is not None:
            self._step = "send"
            for s in out.switches:
                await self.sender.send(s)
            if out.ch is not None:
                await self.sender.send_ch(out.ch)
        self.mem = out.mem
        self._step = "persist"
        self._save()
        self._journal(snap, out)
        self._step = "status"
        state, attrs = out.status
        await self.publisher.publish(state, {**attrs, "tick": now.isoformat(timespec="seconds")})
        self._step = "idle"

    def _save(self) -> None:
        tmp = self.mem_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.mem.to_json(), f)
        os.replace(tmp, self.mem_path)

    def _journal(self, snap, out: Step) -> None:
        """One "intent" line per Switch: what it sets, the rule that won, and whether the thermostat
        (driven by the live baseline while this runs dry) shows something else; one per boiler CH
        reassert; one per lockout, boiler, override or foreign-setpoint transition. A "sample" line
        every SAMPLE_EVERY_MIN with every zone and circuit, for the shadow report."""
        t = snap.now.isoformat(timespec="seconds")
        lines = []
        for s in out.switches:
            w, obs, spec = out.wants[s.source], snap.tstats[s.source], self.site.thermostats[s.source]
            lines.append({"t": t, "kind": "intent", "dry_run": self.dry_run, "circuit": s.source,
                          "entity": spec.entity, "on": s.on, "mode": "heat", "setpoint": spec.setpoint(s.on),
                          "rule": w.rule.value, "why": w.why,
                          "observed": {"mode": obs.mode, "setpoint": obs.setpoint} if obs.available else None,
                          "diverges": not (obs.mode == "heat" and obs.setpoint == spec.setpoint(s.on))})
        if out.ch is not None:
            lines.append({"t": t, "kind": "intent", "dry_run": self.dry_run, "entity": self.site.boiler_ch,
                          "value": out.ch, "observed": snap.boiler.ch_f, "rule": "boiler.reassert_ch",
                          "why": f"readback {snap.boiler.ch_f} F, commissioned {out.ch} F"})
        lines += [{**e, "dry_run": self.dry_run} for e in out.events]
        if self._last_sample is None or (snap.now - self._last_sample).total_seconds() >= SAMPLE_EVERY_MIN * 60:
            self._last_sample = snap.now
            lines.append({"t": t, "kind": "sample", **out.status[1],
                          "observed": {c: {"mode": o.mode, "setpoint": o.setpoint, "reading": o.reading}
                                       for c, o in snap.tstats.items()}})
        if lines:
            with open(self.log_path, "a") as f:
                for line in lines:
                    f.write(json.dumps(line, default=str) + "\n")
