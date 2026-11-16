"""The fitted house parameters. to_params and calibrated_params are copied from lab sim/calibrate.py;
the fitting itself stays in the lab."""
from __future__ import annotations

import json
import os
from dataclasses import replace

from .house import Params

PARAMS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "calibration.json")


def to_params(v: dict, base: Params = Params()) -> Params:
    s = v["hp_E"] + v["hp_W"]
    hp_e, hp_w = (v["hp_E"], v["hp_W"]) if s <= 0.95 else (v["hp_E"] * 0.95 / s, v["hp_W"] * 0.95 / s)
    ua = {"EAST": v["ua_E"], "WEST": v["ua_W"], "FIRST": v["ua_F"], "BASEMENT": v["ua_B"]}
    tot = sum(ua.values())
    return replace(
        base,
        ua_house=v["ua_house"], inf_share=v["inf_share"],
        ua_share=tuple((z, round(x / tot, 4)) for z, x in ua.items()),
        c_air_house=v["c_air_house"], c_mass_house=v["c_mass_house"], h_air_mass_house=v["h_air_mass_house"],
        hp_capacity=v["hp_capacity"],
        hp_split=(("EAST", round(hp_e, 4)), ("WEST", round(hp_w, 4)), ("FIRST", round(1 - hp_e - hp_w, 4))),
        fan_mix=v["fan_mix"], solar_scale=1.0,
        aperture=(("EAST", v["ap_E"]), ("WEST", v["ap_W"]), ("FIRST", v["ap_F"]), ("BASEMENT", v["ap_B"]),
                  ("GARAGE", 2.0)),
        wood_c_floor=v["wood_c_floor"], wood_ua_emitter=v["wood_ua_emitter"], wood_h_floor_air=v["wood_ua_emitter"],
        slab_c_floor=v["slab_c_floor"], slab_h_floor_air=600.0,
        emitter_scale=(("EAST", v["em_E"]), ("WEST", v["em_W"]), ("FIRST", v["em_F"]), ("BASEMENT", v["em_B"]),
                       ("GARAGE", 1.0)),
        ua_pipe=v["ua_pipe"], coupling_scale=v["coupling_scale"], stack_scale=v["stack_scale"],
        ground_ua_basement=v["ground_ua_basement"], garage_ua=v["garage_ua"],
        casing_frac=v.get("casing_frac", base.casing_frac), water_slab=v.get("water_slab", base.water_slab),
        sensor_kick_f=v.get("sensor_kick_f", base.sensor_kick_f),
        sensor_kick_tau_min=v.get("sensor_kick_tau_min", base.sensor_kick_tau_min),
    )


def calibrated_params(path: str = PARAMS_JSON) -> Params:
    with open(path) as f:
        return to_params(json.load(f)["values"])
