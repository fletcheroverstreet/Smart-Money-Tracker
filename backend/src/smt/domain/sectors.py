"""Sector labels: the curated map first, then SIC as a fallback (lodestar DEC-009).

Nothing is silently lumped into "Other": each label records where it came
from (`curated`, `sic`, or `unmapped`).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache

import yaml

from smt.config import CONFIG_DIR


@dataclass(frozen=True)
class SectorLabel:
    sector: str | None
    industry: str | None
    source: str  # curated | sic | unmapped


@lru_cache(maxsize=1)
def _curated() -> dict[str, tuple[str, str]]:
    path = CONFIG_DIR / "sectors" / "industry_map.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        return {row["ticker"].strip().upper(): (row["sector"].strip(), row["industry"].strip())
                for row in csv.DictReader(fh) if row.get("ticker")}


@lru_cache(maxsize=1)
def _sic_rules() -> list[tuple[int, int, str]]:
    path = CONFIG_DIR / "sectors" / "sic_to_sector.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return [(int(r["from"]), int(r["to"]), str(r["sector"])) for r in (data or {}).get("rules", [])]


def sector_for(ticker: str | None, sic: str | None) -> SectorLabel:
    """Sector for a security: curated map by ticker, else SIC range, else unmapped."""
    if ticker:
        hit = _curated().get(ticker.strip().upper().replace("-", "."))
        hit = hit or _curated().get(ticker.strip().upper())
        if hit:
            return SectorLabel(sector=hit[0], industry=hit[1], source="curated")
    if sic and sic.strip().isdigit():
        code = int(sic)
        for lo, hi, sector in _sic_rules():
            if lo <= code <= hi:
                return SectorLabel(sector=sector, industry=None, source="sic")
    return SectorLabel(sector=None, industry=None, source="unmapped")
