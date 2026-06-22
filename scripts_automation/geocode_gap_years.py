"""Geocode the 2024-2026 gap-year locations via Sean's API geocoder, with a resumable JSONL cache.

Dedups locations first (many repeat), caches each location -> WKT geometry to a JSONL file so a re-run
costs nothing and an interrupted run resumes (always-be-caching). Emits a geojson matching Sean's
2019-2022 schema so spread_metric.py runs on it unchanged.

  python -m scripts_automation.geocode_gap_years
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

    lg = LocationGeocoder(GeoCoderAPI())  # stateless wrappers -> safe to share across threads

    def geocode_one(loc):
        try:
            geom = lg.process_location_text(loc)
            return loc, (geom.wkt if geom is not None and not geom.is_empty else None)
        except Exception:  # noqa: BLE001 — geocoder fails on some location formats; store null, continue
            return loc, None

    # I/O-bound (API waits) -> thread pool. Main thread is the sole writer, so the JSONL cache stays
    # consistent; the resumable cache means a kill mid-run just continues next time.
    t0 = time.time()
    with CACHE.open("a") as fh, ThreadPoolExecutor(max_workers=8) as pool:
        for i, fut in enumerate(as_completed(pool.submit(geocode_one, l) for l in todo), 1):
            loc, w = fut.result()
            cache[loc] = w
            fh.write(json.dumps({"loc": loc, "wkt": w}) + "\n")
            fh.flush()
            if i % 200 == 0:
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
