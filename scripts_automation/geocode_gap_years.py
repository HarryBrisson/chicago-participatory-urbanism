"""Geocode the 2024-2026 gap-year locations via Sean's API geocoder, with a resumable JSONL cache.

Dedups locations first (many repeat), caches each location -> WKT geometry to a JSONL file so a re-run
costs nothing and an interrupted run resumes (always-be-caching). Emits a geojson matching Sean's
2019-2022 schema so spread_metric.py runs on it unchanged.

  python -m scripts_automation.geocode_gap_years
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
from shapely import wkt as shapely_wkt
from shapely.geometry import mapping

from src.chicago_participatory_urbanism.geocoder_api import GeoCoderAPI
from src.chicago_participatory_urbanism.ward_spending.location_geocoding import LocationGeocoder

DATA = Path("data/output/menu_2024_2026_processed.csv")
CACHE = Path("data/geocode/gap_years_cache.jsonl")
OUT = Path("data/output/menu_2024_2026_geocoded.geojson")
ANNUAL = {2024: "Q3", 2025: "Q4", 2026: "Q1"}  # latest cumulative snapshot per year


def load_cache() -> dict:
    cache = {}
    if CACHE.exists():
        for line in CACHE.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                cache[rec["loc"]] = rec["wkt"]
    return cache


def main():
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(DATA)
    locs = sorted({str(x) for x in df["location"].dropna().unique() if str(x).strip()})
    cache = load_cache()
    todo = [l for l in locs if l not in cache]
    print(f"{len(locs)} unique locations; {len(cache)} cached; geocoding {len(todo)} new …", flush=True)

    lg = LocationGeocoder(GeoCoderAPI())
    t0 = time.time()
    with CACHE.open("a") as fh:
        for i, loc in enumerate(todo, 1):
            try:
                geom = lg.process_location_text(loc)
                w = geom.wkt if geom is not None and not geom.is_empty else None
            except Exception:  # noqa: BLE001
                w = None
            cache[loc] = w
            fh.write(json.dumps({"loc": loc, "wkt": w}) + "\n")
            fh.flush()
            if i % 100 == 0:
                rate = i / (time.time() - t0)
                print(f"  {i}/{len(todo)}  ({rate:.1f}/s, ~{(len(todo)-i)/rate/60:.0f} min left)", flush=True)

    # build geojson for the annual snapshots
    feats = []
    for _, r in df.iterrows():
        if ANNUAL.get(r["year"]) != r["period"]:
            continue
        w = cache.get(str(r["location"]))
        geom = None
        if w:
            try:
                geom = mapping(shapely_wkt.loads(w))
            except Exception:  # noqa: BLE001
                geom = None
        feats.append({"type": "Feature", "geometry": geom,
                      "properties": {k: (None if pd.isna(r[k]) else r[k])
                                     for k in ("ward", "item", "location", "cost", "year", "category")}})
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    got = sum(1 for f in feats if f["geometry"])
    print(f"\nwrote {len(feats)} annual-snapshot features ({got} geocoded, "
          f"{got/len(feats)*100:.0f}%) -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
