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
URL = "https://web.ais.dk/aisdata/aisdk-{d}.zip"
SAMPLE_SECONDS = 30


def main() -> None:
    if len(sys.argv) > 1:
        day = sys.argv[1]
    else:
        day = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    out_dir = ROOT / "data" / "ais"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ais-{day}.csv"

    url = URL.format(d=day)
    print(f"Henter {url} …", flush=True)
    # web.ais.dk kører periodevis med udløbet certifikat; arkivet er offentligt.
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(url, headers={"User-Agent": "faergedata-monitor/1.0 (AIS-punktlighed for oefaergerne; se GitHub-repo)"})

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=True) as tmp:
        with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                tmp.write(chunk)
        tmp.flush()
        size_mb = tmp.tell() / 1e6
        print(f"Downloadet {size_mb:.0f} MB, filtrerer {len(FERRIES)} MMSI'er …", flush=True)

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


if __name__ == "__main__":
    main()
