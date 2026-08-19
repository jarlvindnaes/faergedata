# Færgedata – uafhængig monitorering af Lolland Færgefart

Dette projekt dokumenterer belægningen på færgeruterne **Kragenæs–Femø**,
**Kragenæs–Askø** og **Kragenæs–Fejø**, baseret på Lolland Kommunes eget
offentlige online-bookingsystem.

Baggrund: Lolland Kommune overvejer at spare afgange væk med henvisning til
"for lidt trafik" — men oplyser samtidig, at man ikke har data for
belægningen. Bookingsystemet publicerer imidlertid løbende antallet af ledige
bilpladser og passagerpladser pr. afgang. Dette repo opsamler de tal hver
2. time, så belægning, udsolgte afgange og bookingpres kan dokumenteres.

## Sådan virker det

- `scraper/fetch_snapshot.py` henter timetable-API'et
  (`https://lolland-ferry.teambooking.dk/api/timetable/days/{dato}`) for i dag
  + 14 dage frem og gemmer rå JSON i `data/raw/` samt normaliserede rækker i
  `data/csv/observations-ÅÅÅÅ-MM.csv`.
- `scraper/build_dataset.py` beregner pr. afgang den sidste måling før
  afgangstid ("endelig tilstand"), bookingkurver og daglige aggregater, og
  skriver `docs/data.json`.
- `.github/workflows/scrape.yml` kører begge scripts hver 2. time via GitHub
  Actions og committer resultatet — git-historikken er dermed et verificerbart
  audit-trail af hver eneste måling.
- `docs/index.html` er et dashboard (GitHub Pages) der viser udsolgte afgange,
  pres på kommende afgange, bookingkurver og en komplet tabel.

## Opsætning

1. Opret et offentligt GitHub-repo og push indholdet af denne mappe.
2. Aktivér GitHub Pages: *Settings → Pages → Source: Deploy from a branch →
   `main` / `/docs`*.
3. Aktivér Actions (*Actions*-fanen → enable). Workflowet kører herefter
   automatisk hver 2. time; kør det første gang manuelt med *Run workflow*.
4. Dashboardet ligger på `https://<brugernavn>.github.io/<repo>/`.

Ingen afhængigheder ud over Python 3.9+ (kun standardbiblioteket).

## Noter om data

- Bilkapaciteten pr. rute offentliggøres ikke af API'et. Den estimeres som det
  største observerede antal ledige bilpladser og er derfor en **nedre grænse**
  for den reelle kapacitet. Kendes den præcist, kan den sættes i
  `CAR_CAPACITY_OVERRIDE` i `scraper/build_dataset.py`.
- Afgange fjernes fra API'et når de er sejlet; den "endelige" belægning er
  derfor den sidste måling før afgang (maks. 2 timer før).
- Passagerkapacitet (`maxPax`) oplyses direkte af API'et.
- Scriptet kalder API'et ca. 15 gange pr. kørsel med 1 sekunds pause — samme
  belastning som en enkelt bruger, der kigger timetable-siden igennem.

## Metode og redelighed

Alle rå API-svar gemmes urørt i `data/raw/`, og hver commit er tidsstemplet af
GitHub. Enhver kan reproducere og efterprøve tallene. Fejl eller indvendinger
modtages gerne som issues.
