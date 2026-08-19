#!/usr/bin/env python3
"""
Snapshot af ledighed pr. afgang fra Lolland Færgefarts bookingsystem.

Henter den offentlige timetable-API for i dag + de næste DAYS_AHEAD dage
og gemmer:
  1. Rå JSON-svar i data/raw/YYYY-MM/  (fuld audit-trail)
  2. Normaliserede rækker i data/csv/observations-YYYY-MM.csv

Kilde: https://lolland-ferry.teambooking.dk/api/timetable/days/{date}
Ruter: 411 Kragenæs-Femø, 413 Kragenæs-Askø, 414 Kragenæs-Fejø
"""

import csv
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://lolland-ferry.teambooking.dk/api/timetable/days/{date}"
DAYS_AHEAD = 14  # i dag + 14 dage frem => bookingkurver pr. afgang
TZ = ZoneInfo("Europe/Copenhagen")
ROOT = Path(__file__).resolve().parent.parent
HEADERS = {
    "User-Agent": "faergedata-monitor/1.0 (frivillig trafikmonitorering for oeboere; kontakt: se GitHub-repo)",
    "Accept": "application/json",
    "Accept-Language": "da",
}

CSV_FIELDS = [
    "snapshot_utc",      # tidspunkt for målingen (UTC, ISO)
    "service_date",      # den dato der blev spurgt om
    "ferry_route_id",    # 411/413/414
    "crossing",          # fx "Kragenæs > Femø"
    "departure_id",      # bookingsystemets unikke afgangs-id
    "depart",            # afgangstid (lokal, ISO)
    "arrival",           # ankomsttid (lokal, ISO)
    "available_cars",    # ledige bilpladser på måletidspunktet
    "available_pax",     # ledige passagerpladser
    "max_pax",           # passagerkapacitet
    "ferry",             # fx "M/F Femøsund"
    "css_class",         # bookingsystemets status (crossing-normal / almost-fully-booked / ...)
    "is_dangerous_goods",
]


def fetch(date_str: str) -> list:
    req = urllib.request.Request(BASE.format(date=date_str), headers=HEADERS)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            if attempt == 2:
                raise
            print(f"  retry {date_str}: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))


def main() -> None:
    now_utc = datetime.now(timezone.utc)
    snapshot_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    today_local = now_utc.astimezone(TZ).date()

    raw_dir = ROOT / "data" / "raw" / today_local.strftime("%Y-%m")
    raw_dir.mkdir(parents=True, exist_ok=True)
    csv_dir = ROOT / "data" / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    raw_bundle = {"snapshot_utc": snapshot_iso, "days": {}}

    for offset in range(DAYS_AHEAD + 1):
        d = today_local + timedelta(days=offset)
        date_str = d.isoformat()
        data = fetch(date_str)
        raw_bundle["days"][date_str] = data
        for crossing in data:
            for dep in crossing.get("departures", []):
                rows.append({
                    "snapshot_utc": snapshot_iso,
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
        time.sleep(1)  # høflig pause mellem kald

    # 1) råt svar (audit-trail)
    raw_path = raw_dir / f"snapshot-{now_utc.strftime('%Y-%m-%dT%H%M')}Z.json"
    raw_path.write_text(json.dumps(raw_bundle, ensure_ascii=False), encoding="utf-8")

    # 2) CSV-append (månedsfil)
    csv_path = csv_dir / f"observations-{today_local.strftime('%Y-%m')}.csv"
    new_file = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(rows)

    print(f"Snapshot {snapshot_iso}: {len(rows)} afgangs-observationer gemt.")


if __name__ == "__main__":
    main()
