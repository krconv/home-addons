"""Home Assistant core REST API through the Supervisor proxy (needs `homeassistant_api: true`)."""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Optional, Protocol

SUPERVISOR_CORE_API = "http://supervisor/core/api"


class HaClient(Protocol):
    async def get_state(self, entity_id: str) -> Optional[dict]: ...
    async def call_service(self, service: str, data: dict) -> None: ...
    async def set_state(self, entity_id: str, state: str, attributes: dict) -> None: ...


class RestClient:
    """Blocking urllib calls run in the default executor; a few requests a minute need nothing more."""

    def __init__(self, token: str, base_url: str = SUPERVISOR_CORE_API, timeout: float = 10.0):
        self._token = token
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> "RestClient":
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise RuntimeError("SUPERVISOR_TOKEN is not set; the add-on needs homeassistant_api: true")
        return cls(token)

    async def get_state(self, entity_id: str) -> Optional[dict]:
        """The full state object, or None when the entity does not exist. Transport errors raise."""
        try:
            return await asyncio.to_thread(self._request, "GET", f"/states/{entity_id}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    async def call_service(self, service: str, data: dict) -> None:
        domain, name = service.split("/", 1)
        await asyncio.to_thread(self._request, "POST", f"/services/{domain}/{name}", data)

    async def set_state(self, entity_id: str, state: str, attributes: dict) -> None:
        await asyncio.to_thread(self._request, "POST", f"/states/{entity_id}",
                                {"state": state, "attributes": attributes})

    def _request(self, method: str, path: str, body: Optional[dict] = None):
        data = None if body is None else json.dumps(body, default=str).encode()
        req = urllib.request.Request(self._base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else None
