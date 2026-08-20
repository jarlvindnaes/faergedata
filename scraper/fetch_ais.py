#!/usr/bin/env python3
"""
Henter AIS-spor for Lolland Færgefarts tre færger fra Søfartsstyrelsens
åbne, historiske AIS-arkiv (https://web.ais.dk/aisdata/, dagsfiler, CC-frit).

Brug:  python scraper/fetch_ais.py [YYYY-MM-DD]   (standard: i går, UTC)

Skriver data/ais/ais-YYYY-MM-DD.csv med kolonnerne
  ts_utc,mmsi,lat,lon,sog,nav  — nedsamplet til én position pr. 30 s pr. skib.
"""

import csv
import io
import ssl
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FERRIES = {
    "219000809": "femosund",   # M/F Femøsund (Kragenæs-Femø)
    "219000811": "asko",       # M/F Askø     (Kragenæs-Askø)
    "219002177": "christine",  # M/F Christine (Kragenæs-Fejø)
}
URLS = [
    "https://web.ais.dk/aisdata/aisdk-{d}.zip",
    "http://web.ais.dk/aisdata/aisdk-{d}.zip",
]
SAMPLE_SECONDS = 30
BACKFILL_DAYS = 7  # hent op til så mange manglende dage pr. kørsel


def download(day: str, tmp) -> bool:
    """Prøv alle mirrors/protokoller med retries. False hvis arkivet er nede."""
    import time
    # web.ais.dk kører periodevis med udløbet certifikat; arkivet er offentligt.
    ctx = ssl._create_unverified_context()
    headers = {"User-Agent": "faergedata-monitor/1.0 (AIS-punktlighed for oefaergerne; se GitHub-repo)"}
    for attempt in range(3):
        for base in URLS:
            url = base.format(d=day)
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
                    tmp.seek(0); tmp.truncate()
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        tmp.write(chunk)
                tmp.flush()
                if tmp.tell() > 1000:
                    print(f"  hentet {url} ({tmp.tell()/1e6:.0f} MB)", flush=True)
                    return True
            except Exception as e:
                print(f"  {url}: {type(e).__name__}: {e}", flush=True)
        time.sleep(20 * (attempt + 1))
    return False


def process_day(day: str) -> bool:
    out_dir = ROOT / "data" / "ais"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ais-{day}.csv"

    print(f"Henter AIS for {day} …", flush=True)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=True) as tmp:
        if not download(day, tmp):
            print(f"  arkivet svarer ikke for {day} — springer over (hentes senere).")
            return False
        rows, last_kept, n_scanned = [], {}, 0
        with zipfile.ZipFile(tmp.name) as z:
            name = z.namelist()[0]
            with z.open(name) as f:
                reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
                header = next(reader)
                idx = {c.strip("# ").lower(): i for i, c in enumerate(header)}
                i_ts, i_mmsi = idx["timestamp"], idx["mmsi"]
                i_lat, i_lon = idx["latitude"], idx["longitude"]
                i_sog = idx.get("sog"); i_nav = idx.get("navigational status")
                for row in reader:
                    n_scanned += 1
                    mmsi = row[i_mmsi]
                    if mmsi not in FERRIES:
                        continue
                    try:
                        ts = datetime.strptime(row[i_ts], "%d/%m/%Y %H:%M:%S")
                    except ValueError:
                        continue
                    prev = last_kept.get(mmsi)
                    if prev and (ts - prev).total_seconds() < SAMPLE_SECONDS:
                        continue
                    last_kept[mmsi] = ts
                    rows.append([
                        ts.strftime("%Y-%m-%dT%H:%M:%SZ"), mmsi,
                        row[i_lat], row[i_lon],
                        row[i_sog] if i_sog is not None else "",
                        row[i_nav] if i_nav is not None else "",
                    ])

    rows.sort()
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts_utc", "mmsi", "lat", "lon", "sog", "nav"])
        w.writerows(rows)
    per = {m: sum(1 for r in rows if r[1] == m) for m in FERRIES}
    print(f"Scannede {n_scanned:,} AIS-rækker; gemte {len(rows)} positioner "
          f"({', '.join(FERRIES[m] + '=' + str(n) for m, n in per.items())}) i {out_path.name}")
    return True


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1].strip():
        days = [sys.argv[1].strip()]
    else:
        # backfill: alle manglende dage inden for BACKFILL_DAYS (senest først = i går)
        have = {p.stem[4:] for p in (ROOT / "data" / "ais").glob("ais-*.csv")}
        today = datetime.now(timezone.utc).date()
        days = [(today - timedelta(days=o)).strftime("%Y-%m-%d")
                for o in range(1, BACKFILL_DAYS + 1)]
        days = [d for d in days if d not in have]
    if not days:
        print("Ingen manglende AIS-dage.")
        return
    got = sum(1 for d in days if process_day(d))
    print(f"{got} af {len(days)} dage hentet.")


if __name__ == "__main__":
    main()
