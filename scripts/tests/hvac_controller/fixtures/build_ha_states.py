"""Rebuild ha_states.json: the states sense.read consumes, as Home Assistant held them at one
moment, taken from the lab's recorded history archive (read-only, no HA call).

  python -m tests.hvac_controller.fixtures.build_ha_states /workspace/ha/lab/data/archive/2026-11-27 2026-11-27T22:00:00-05:00

Sanitized: only the attributes sense reads are kept. History rows carry no last_reported, so
last_updated stands in for it; entities the archive doesn't record are listed under "absent".
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

from src.hvac_controller.site import SITE

KEEP_ATTRS = ("temperature", "current_temperature", "hvac_action")
OUT = os.path.join(os.path.dirname(__file__), "ha_states.json")


def build(archive_day: str, at: str) -> dict:
    t = datetime.fromisoformat(at)
    states, absent = [], []
    for entity in sorted(SITE.read_list()):
        path = os.path.join(archive_day, f"{entity}.history.json")
        rows = json.load(open(path)) if os.path.exists(path) else []
        rows = [r for r in rows if datetime.fromisoformat(r["last_updated"]) <= t]
        if not rows:
            absent.append(entity)
            continue
        r = rows[-1]
        attrs = {k: v for k, v in (r.get("attributes") or {}).items() if k in KEEP_ATTRS}
        states.append({"entity_id": entity, "state": r["state"], "attributes": attrs,
                       "last_changed": r["last_changed"], "last_updated": r["last_updated"],
                       "last_reported": r["last_updated"]})
    return {"captured_at": at, "source": os.path.basename(archive_day.rstrip("/")), "absent": absent,
            "states": states}


if __name__ == "__main__":
    with open(OUT, "w") as f:
        json.dump(build(sys.argv[1], sys.argv[2]), f, indent=1)
        f.write("\n")
