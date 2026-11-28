"""Cost per delivered MMBTU per source, the only economic quantity. Pure."""
from __future__ import annotations

from typing import Literal, Mapping, Optional

from .site import Economics
from .types import Prices, TouBand

Source = Literal["HP", "RADIANT"]
KWH_PER_MMBTU = 293.07


def table(prices: Prices, econ: Economics) -> dict[TouBand, dict[Source, float]]:
    """$/MMBTU delivered, for each band with a known electric rate. Propane carries the boiler's
    own electricity at the same band's rate."""
    propane = prices.propane_gal * 1e6 / (econ.propane_btu_gal * econ.boiler_eff)
    return {band: {"HP": round(rate * KWH_PER_MMBTU / econ.cop, 2),
                   "RADIANT": round(propane + rate * econ.boiler_kwh_mmbtu, 2)}
            for band, rate in prices.kwh.items()}


def choose(costs: Optional[Mapping[Source, float]], running: Source, margin: float) -> tuple[Source, str]:
    """The cheaper source, except that the running one keeps a near-tie: the other must be cheaper
    by `margin`. No costs means no economic switching."""
    if costs is None:
        return running, f"no electric rate: no economic switching, {running} keeps it"
    other: Source = "RADIANT" if running == "HP" else "HP"
    vs = f"{other} ${costs[other]:.0f} vs {running} ${costs[running]:.0f}/MMBTU"
    if costs[other] < costs[running] * (1 - margin):
        return other, vs
    return running, f"{vs}: {running} keeps it"
