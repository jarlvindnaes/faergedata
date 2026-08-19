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

CSV_FIELDS = [
    "snapshot_utc", "service_date", "ferry_route_id", "crossing",
    "departure_id", "depart", "arrival", "available_cars", "available_pax",
    "max_pax", "ferry", "css_class", "is_dangerous_goods",
]


def reconcile_raw_into_csv():
    """Selvhelbredende: de rå snapshot-filer er kilden til sandheden.

    Hvis en rå snapshot-fil findes i data/raw/ men dens snapshot_utc mangler
    i måneds-CSV'en (fx fordi en manuel upload har overskrevet CSV'en),
    genskabes rækkerne fra den rå fil og appendes. Ingenting slettes.
    """
    csv_dir = ROOT / "data" / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)

    for month_dir in sorted((ROOT / "data" / "raw").glob("*")):
        if not month_dir.is_dir():
            continue
        csv_path = csv_dir / f"observations-{month_dir.name}.csv"
        have = set()
        if csv_path.exists():
            with csv_path.open(encoding="utf-8") as f:
                have = {r["snapshot_utc"] for r in csv.DictReader(f)}

        restored = []
        for raw_path in sorted(month_dir.glob("snapshot-*.json")):
            try:
                bundle = json.loads(raw_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                print(f"reconcile: kunne ikke læse {raw_path.name}: {e}")
                continue
            snap = bundle.get("snapshot_utc")
            if not snap or snap in have:
                continue
            for date_str, data in sorted(bundle.get("days", {}).items()):
                for crossing in data or []:
                    for dep in crossing.get("departures", []):
                        restored.append({
                            "snapshot_utc": snap,
                            "service_date": date_str,
                            "ferry_route_id": crossing.get("ferryRouteId"),
                            "crossing": (crossing.get("crossingName") or "").strip(),
                            "departure_id": dep.get("departureId"),
                            "depart": dep.get("depart"),
                            "arrival": dep.get("arrival"),
                            "available_cars": dep.get("availableCars"),
                            "available_pax": dep.get("availablePax"),
                            "max_pax": dep.get("maxPax"),
                            "ferry": (dep.get("note") or "").strip(),
                            "css_class": dep.get("cssClass"),
                            "is_dangerous_goods": dep.get("isDangerousGoods"),
                        })
            have.add(snap)
            print(f"reconcile: genskabte snapshot {snap} fra {raw_path.name}")

        if restored:
            new_file = not csv_path.exists()
            with csv_path.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                if new_file:
                    w.writeheader()
                w.writerows(restored)
            print(f"reconcile: {len(restored)} rækker appendet til {csv_path.name}")



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
    reconcile_raw_into_csv()
    rows = load_rows()
    now = datetime.now(timezone.utc)

    by_dep = defaultdict(list)
    for r in rows:
        by_dep[r["departure_id"]].append(r)
    for obs in by_dep.values():
        obs.sort(key=lambda r: r["snapshot_dt"])

    # globalt sorteret liste af snapshots (til aflysningsdetektion)
    all_snaps = sorted({r["snapshot_dt"] for r in rows})

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

        # AFLYSNINGS-RADAR: afgangen er forsvundet fra bookingsystemet FØR sin
        # afgangstid, selvom vi har senere snapshots der dækkede datoen.
        # (Sejlede afgange forsvinder først EFTER afgang — det er normalt.)
        last_seen = obs[-1]["snapshot_dt"]
        margin = timedelta(minutes=30)
        later_snaps = [s for s in all_snaps
                       if last_seen < s < depart_utc - margin
                       and (last["depart_dt"].date() - s.astimezone(TZ).date()).days <= 14]
        cancelled = len(later_snaps) > 0
        detected = later_snaps[0] if cancelled else None
        notice_h = round((depart_utc - detected).total_seconds() / 3600, 1) if detected else None

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
            "cancelled": cancelled,
            "cancel_detected_utc": detected.strftime("%Y-%m-%dT%H:%M:%SZ") if detected else None,
            "cancel_notice_hours": notice_h,
        })
        # individuelle kurver beholdes for de seneste 30 dage + kommende afgange
        if depart_utc >= now - timedelta(days=30):
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

    # AGGREGERET BOOKINGKURVE pr. rute: gennemsnitligt antal ledige bilpladser
    # som funktion af timer-til-afgang (bucket = 6 timer, op til 14 døgn).
    # Beregnes over ALLE observationer nogensinde — kurven skærpes med tiden.
    agg = defaultdict(lambda: defaultdict(list))
    for r in rows:
        rid = int(r["ferry_route_id"])
        h = (r["depart_dt"].astimezone(timezone.utc) - r["snapshot_dt"]).total_seconds() / 3600
        if h < 0 or h > 336:
            continue
        bucket = int(h // 6) * 6
        agg[rid][bucket].append(r["available_cars"])
    agg_curves = {
        str(rid): [
            {"h": b, "avg": round(sum(v) / len(v), 2), "n": len(v)}
            for b, v in sorted(buckets.items())
        ]
        for rid, buckets in agg.items()
    }

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
        "agg_curves": agg_curves,
    }
    out_path = ROOT / "docs" / "data.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"data.json: {len(departures)} afgange, {len(rows)} observationer.")


if __name__ == "__main__":
    main()
