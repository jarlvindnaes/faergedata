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
MIN_DOCKED_S = 120
MATCH_EARLY_MIN, MATCH_LATE_MIN = -10, 60


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
    """Planlagte afgange på AIS-dækkede dage.

    Direkte: seneste kendte plan pr. departure_id fra bookingarkivet (plan_source="booking").
    Mønster: for AIS-dage FØR bookingarkivet begyndte syntetiseres planen fra den faste
    ugedagsplan, som er observeret i arkivet (tider der optræder på >= 50 % af de
    observerede datoer for samme rute/ugedag/overfart) — plan_source="mønster".
    """
    plan = {}
    all_rows = []
    for p in sorted((ROOT / "data" / "csv").glob("observations-*.csv")):
        with p.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                all_rows.append(r)
                if r["depart"][:10] in days:
                    plan[r["departure_id"]] = r
    direct = list(plan.values())
    for r in direct:
        r["plan_source"] = "booking"
    covered = {r["depart"][:10] for r in direct}

    # ugedagsmønster fra arkivet: pr. rute/ugedag vælges den observerede dato med
    # FLEST afgange (= den ordinære køreplan; reducerede uger og fjernede afgange
    # trækker derfor ikke mønstret skævt). Fjernede/genudgivne ids udelades.
    removed = set()
    try:
        dj = json.loads((ROOT / "docs" / "data.json").read_text(encoding="utf-8"))
        removed = {d["id"] for d in dj["departures"] if d.get("cancelled") or d.get("cancel_kind") == "replaced"}
    except Exception:
        pass
    last_by_id = {}
    for r in all_rows:
        if r["departure_id"] in removed:
            continue
        last_by_id[r["departure_id"]] = r
    per_date = defaultdict(set)   # (route, weekday, date) -> {(crossing, HH:MM)}
    for r in last_by_id.values():
        d = datetime.fromisoformat(r["depart"])
        per_date[(r["ferry_route_id"], d.weekday(), r["depart"][:10])].add((r["crossing"].strip(), r["depart"][11:16]))
    best_for = {}                 # (route, weekday) -> (count, date)
    for (rid, wd, date), times in per_date.items():
        cand = (len(times), date)
        if (rid, wd) not in best_for or cand > best_for[(rid, wd)]:
            best_for[(rid, wd)] = cand
    pattern = {}
    for (rid, wd), (_, date) in best_for.items():
        pattern[(rid, wd)] = sorted(per_date[(rid, wd, date)], key=lambda t: t[1])

    synthetic = []
    for day in sorted(days):
        if day in covered:
            continue
        wd = datetime.fromisoformat(day).weekday()
        for rid in ("411", "413", "414"):
            for crossing, hhmm in pattern.get((rid, wd), []):
                synthetic.append({
                    "departure_id": f"pattern-{rid}-{day}-{hhmm}-{crossing[:2]}",
                    "ferry_route_id": rid, "crossing": crossing,
                    "depart": f"{day}T{hhmm}:00", "plan_source": "mønster",
                })
    return direct + synthetic


def main():
    tracks, days = load_tracks()
    if not days:
        print("Ingen AIS-filer endnu — skriver tomt punctuality.json")
        out = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "days_covered": [], "departures": [], "routes": {}, "actual_by_day": []}
        (ROOT / "docs" / "punctuality.json").write_text(json.dumps(out), encoding="utf-8")
        return

    events_by_route_berth = defaultdict(list)
    for mmsi, track in tracks.items():
        rid = FERRY_ROUTE[mmsi]
        for ts, berth in detect_departures(track, BERTHS):
            events_by_route_berth[(rid, berth)].append(ts)
    for v in events_by_route_berth.values():
        v.sort()

    # FAKTISKE afsejlinger pr. dag pr. rute — virker uafhængigt af bookingdata
    actual = defaultdict(list)
    for (rid, berth), evs in events_by_route_berth.items():
        for ts in evs:
            local = ts.astimezone(TZ)
            actual[(local.strftime("%Y-%m-%d"), rid)].append(
                {"t": local.strftime("%H:%M"), "from": berth})
    actual_by_day = [
        {"date": k[0], "route_id": k[1], "n": len(v),
         "sailings": sorted(v, key=lambda e: e["t"])}
        for k, v in sorted(actual.items())
    ]

    planned = load_planned(set(days))
    # Global nærmeste-først tildeling: alle (plan, afsejling)-par i vinduet sorteres
    # efter |forsinkelse| og tildeles én-til-én. Undgår at en tidlig planlagt afgang
    # "stjæler" en senere afsejling på højfrekvente ruter (Fejø).
    plan_rows = []
    for r in planned:
        rid = int(r["ferry_route_id"])
        from_kragenaes = r["crossing"].strip().startswith("Kragenæs")
        berth = "kragenaes" if from_kragenaes else ROUTE_ISLAND_BERTH[rid]
        sched = datetime.fromisoformat(r["depart"]).replace(tzinfo=TZ).astimezone(timezone.utc)
        plan_rows.append((r, rid, berth, sched))
    pairs = []
    for pi, (r, rid, berth, sched) in enumerate(plan_rows):
        for ei, ts in enumerate(events_by_route_berth.get((rid, berth), [])):
            dm = (ts - sched).total_seconds() / 60
            if MATCH_EARLY_MIN <= dm <= MATCH_LATE_MIN:
                pairs.append((abs(dm), dm, pi, (rid, berth, ei)))
    pairs.sort()
    assigned, used_ev = {}, set()
    for _, dm, pi, ev in pairs:
        if pi in assigned or ev in used_ev:
            continue
        assigned[pi] = dm
        used_ev.add(ev)
    results = []
    for pi, (r, rid, berth, sched) in enumerate(plan_rows):
        dm = assigned.get(pi)
        results.append({
            "id": r["departure_id"], "route_id": rid, "crossing": r["crossing"],
            "depart": r["depart"], "plan_source": r.get("plan_source", "booking"),
            "delay_min": round(dm, 1) if dm is not None else None,
            "status": "bekræftet" if dm is not None else "ikke bekræftet",
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
        "actual_by_day": actual_by_day,
    }
    (ROOT / "docs" / "punctuality.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    conf = sum(1 for x in results if x["delay_min"] is not None)
    print(f"punctuality.json: {len(results)} planlagte, {conf} bekræftede afsejlinger, {len(days)} AIS-dage")


if __name__ == "__main__":
    main()
