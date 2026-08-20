#!/usr/bin/env python3
"""
Sammenholder faktiske afsejlinger (AIS, data/ais/*.csv) med planlagte
afgange (bookingsystemets observationer, data/csv/*.csv) og skriver
docs/punctuality.json.

Metode (konservativ):
- En færge regnes "ved kaj" når den er < GEOFENCE_M fra lejet.
- En afsejling detekteres når den forlader geofencet efter >= 5 min ved kaj.
- En afsejling matches til nærmeste planlagte afgang fra samme leje i
  vinduet -15 .. +120 min. Forsinkelse = faktisk - planlagt (minutter).
- Planlagte afgange uden detekteret afsejling markeres kun "ikke bekræftet"
  (aldrig "aflyst" alene på AIS — AIS-huller findes).
"""

import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Copenhagen")

BERTHS = {
    "kragenaes": (11.35968, 54.91611),
    "femo":      (11.51476, 54.97189),
    "fejo":      (11.37220, 54.93623),
    "asko":      (11.48316, 54.88443),
}
FERRY_ROUTE = {"219000809": 411, "219000811": 413, "219002177": 414}
ROUTE_ISLAND_BERTH = {411: "femo", 413: "asko", 414: "fejo"}
GEOFENCE_M = 300
MIN_DOCKED_S = 300
MATCH_EARLY_MIN, MATCH_LATE_MIN = -15, 120


def dist_m(lon1, lat1, lon2, lat2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def load_tracks():
    tracks = defaultdict(list)
    days = set()
    for p in sorted((ROOT / "data" / "ais").glob("ais-*.csv")):
        days.add(p.stem[4:])
        with p.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                ts = datetime.strptime(r["ts_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                tracks[r["mmsi"]].append((ts, float(r["lat"]), float(r["lon"])))
    for v in tracks.values():
        v.sort()
    return tracks, sorted(days)


def detect_departures(track, berths):
    """-> liste af (leave_ts, berth_navn)"""
    events = []
    docked_at, docked_since = None, None
    for ts, lat, lon in track:
        here = None
        for name, (blon, blat) in berths.items():
            if dist_m(lon, lat, blon, blat) < GEOFENCE_M:
                here = name
                break
        if here:
            if docked_at != here:
                docked_at, docked_since = here, ts
        else:
            if docked_at and docked_since and (ts - docked_since).total_seconds() >= MIN_DOCKED_S:
                events.append((ts, docked_at))
            docked_at, docked_since = None, None
    return events


def load_planned(days):
    """Planlagte afgange (seneste kendte plan pr. departure_id) på AIS-dækkede dage."""
    plan = {}
    for p in sorted((ROOT / "data" / "csv").glob("observations-*.csv")):
        with p.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                d = r["depart"][:10]
                if d not in days:
                    continue
                plan[r["departure_id"]] = r
    return list(plan.values())


def main():
    tracks, days = load_tracks()
    if not days:
        print("Ingen AIS-filer endnu — skriver tomt punctuality.json")
        out = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "days_covered": [], "departures": [], "routes": {}}
        (ROOT / "docs" / "punctuality.json").write_text(json.dumps(out), encoding="utf-8")
        return

    events_by_route_berth = defaultdict(list)
    for mmsi, track in tracks.items():
        rid = FERRY_ROUTE[mmsi]
        for ts, berth in detect_departures(track, BERTHS):
            events_by_route_berth[(rid, berth)].append(ts)
    for v in events_by_route_berth.values():
        v.sort()

    planned = load_planned(set(days))
    results = []
    used = set()
    for r in planned:
        rid = int(r["ferry_route_id"])
        from_kragenaes = r["crossing"].strip().startswith("Kragenæs")
        berth = "kragenaes" if from_kragenaes else ROUTE_ISLAND_BERTH[rid]
        sched = datetime.fromisoformat(r["depart"]).replace(tzinfo=TZ).astimezone(timezone.utc)
        cands = events_by_route_berth.get((rid, berth), [])
        best = None
        for i, ts in enumerate(cands):
            if (rid, berth, i) in used:
                continue
            dm = (ts - sched).total_seconds() / 60
            if MATCH_EARLY_MIN <= dm <= MATCH_LATE_MIN and (best is None or abs(dm) < abs(best[1])):
                best = (i, dm)
        if best is not None:
            used.add((rid, berth, best[0]))
        results.append({
            "id": r["departure_id"], "route_id": rid, "crossing": r["crossing"],
            "depart": r["depart"],
            "delay_min": round(best[1], 1) if best else None,
            "status": "bekræftet" if best else "ikke bekræftet",
        })

    routes = {}
    for rid in (411, 413, 414):
        rs = [x for x in results if x["route_id"] == rid]
        ds = sorted(x["delay_min"] for x in rs if x["delay_min"] is not None)
        n = len(ds)
        routes[str(rid)] = {
            "planned": len(rs), "confirmed": n,
            "median_delay_min": ds[n // 2] if n else None,
            "p90_delay_min": ds[int(n * 0.9)] if n else None,
            "late_over_5min": sum(1 for d in ds if d > 5),
            "unconfirmed": len(rs) - n,
        }

    out = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days_covered": days,
        "geofence_m": GEOFENCE_M,
        "departures": sorted(results, key=lambda x: x["depart"]),
        "routes": routes,
    }
    (ROOT / "docs" / "punctuality.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    conf = sum(1 for x in results if x["delay_min"] is not None)
    print(f"punctuality.json: {len(results)} planlagte, {conf} bekræftede afsejlinger, {len(days)} AIS-dage")


if __name__ == "__main__":
    main()
