#!/usr/bin/env python3
"""
Bygger docs/data.json ud fra alle observations-CSV'er.

For hver afgang bruges den SIDSTE observation før afgangstid som "endelig"
tilstand (dvs. hvor mange pladser der stod ledige, da færgen sejlede).
Derudover gemmes bookingkurver og aktuelle/fremtidige pres-indikatorer.
"""

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Copenhagen")

# Kendt bilkapacitet pr. rute (kan justeres når den kendes præcist).
# Hvis ikke angivet estimeres den som største observerede antal ledige
# bilpladser på ruten (en nedre grænse for den reelle kapacitet).
CAR_CAPACITY_OVERRIDE = {
    # 411: 18,  # Kragenæs-Femø, M/F Femøsund
}
ROUTE_NAMES = {411: "Kragenæs–Femø", 413: "Kragenæs–Askø", 414: "Kragenæs–Fejø"}


def parse_utc(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def parse_local(s):
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def load_rows():
    rows = []
    for p in sorted((ROOT / "data" / "csv").glob("observations-*.csv")):
        with p.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                r["snapshot_dt"] = parse_utc(r["snapshot_utc"])
                r["depart_dt"] = parse_local(r["depart"])
                r["available_cars"] = int(r["available_cars"] or 0)
                r["available_pax"] = int(r["available_pax"] or 0)
                r["max_pax"] = int(r["max_pax"] or 0)
                rows.append(r)
    return rows


def main():
    rows = load_rows()
    now = datetime.now(timezone.utc)

    by_dep = defaultdict(list)
    for r in rows:
        by_dep[r["departure_id"]].append(r)
    for obs in by_dep.values():
        obs.sort(key=lambda r: r["snapshot_dt"])

    # bilkapacitet pr. rute: override eller max observerede ledige pladser
    cap = dict(CAR_CAPACITY_OVERRIDE)
    seen_max = defaultdict(int)
    for r in rows:
        rid = int(r["ferry_route_id"])
        seen_max[rid] = max(seen_max[rid], r["available_cars"])
    for rid, m in seen_max.items():
        cap.setdefault(rid, m)

    departures = []
    curves = {}
    for dep_id, obs in by_dep.items():
        last = obs[-1]
        depart_utc = last["depart_dt"].astimezone(timezone.utc)
        sailed = depart_utc < now
        pre = [o for o in obs if o["snapshot_dt"] <= depart_utc]
        final = pre[-1] if pre else last
        ferry_cap = cap.get(int(last["ferry_route_id"]), 0) or 0
        soldout_obs = next((o for o in obs if o["available_cars"] == 0), None)
        departures.append({
            "id": dep_id,
            "route_id": int(last["ferry_route_id"]),
            "crossing": last["crossing"],
            "depart": last["depart"],
            "ferry": last["ferry"],
            "sailed": sailed,
            "final_avail_cars": final["available_cars"],
            "final_avail_pax": final["available_pax"],
            "max_pax": final["max_pax"],
            "car_capacity_est": ferry_cap,
            "cars_booked_est": max(0, ferry_cap - final["available_cars"]) if ferry_cap else None,
            "soldout_cars": final["available_cars"] == 0,
            "ever_soldout_cars": soldout_obs is not None,
            "soldout_lead_hours": (
                round((depart_utc - soldout_obs["snapshot_dt"]).total_seconds() / 3600, 1)
                if soldout_obs else None
            ),
            "n_obs": len(obs),
            "final_obs_utc": final["snapshot_utc"],
        })
        curves[dep_id] = [
            {"t": o["snapshot_utc"], "cars": o["available_cars"], "pax": o["available_pax"]}
            for o in obs
        ]

    departures.sort(key=lambda d: d["depart"])

    # daglig aggregering pr. overfart (kun sejlede afgange)
    daily = defaultdict(lambda: {"n": 0, "soldout": 0, "avail_cars_sum": 0, "cars_booked_sum": 0})
    for d in departures:
        if not d["sailed"]:
            continue
        key = (d["depart"][:10], d["crossing"])
        agg = daily[key]
        agg["n"] += 1
        agg["soldout"] += 1 if d["soldout_cars"] else 0
        agg["avail_cars_sum"] += d["final_avail_cars"]
        agg["cars_booked_sum"] += d["cars_booked_est"] or 0
    daily_list = [
        {"date": k[0], "crossing": k[1], **v} for k, v in sorted(daily.items())
    ]

    out = {
        "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_observations": len(rows),
        "n_departures": len(departures),
        "first_snapshot": min((r["snapshot_utc"] for r in rows), default=None),
        "car_capacity_est": {ROUTE_NAMES.get(k, str(k)): v for k, v in cap.items()},
        "route_names": ROUTE_NAMES,
        "departures": departures,
        "daily": daily_list,
        "curves": curves,
    }
    out_path = ROOT / "docs" / "data.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"data.json: {len(departures)} afgange, {len(rows)} observationer.")


if __name__ == "__main__":
    main()
