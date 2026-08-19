# Færgedata – uafhængig monitorering af færger

Dette projekt dokumenterer belægningen på færgeruterne **Kragenæs–Femø**,
**Kragenæs–Askø** og **Kragenæs–Fejø**

Baggrund: Lolland Kommune overvejer at spare afgange væk med henvisning til
"for lidt trafik" — men oplyser samtidig, at man ikke har data for
belægningen. Dette repo opsamler data, så belægning, udsolgte afgange og bookingpres kan dokumenteres.

## Sådan virker det

- Not disclosed

## Opsætning

1. Not disclosed

Ingen afhængigheder ud over Python 3.9+ (kun standardbiblioteket).

## Noter om data

- Bilkapaciteten pr. rute offentliggøres ikke. Den estimeres som det
  største observerede antal ledige bilpladser og er derfor en **nedre grænse**
  for den reelle kapacitet. Kendes den præcist, kan den sættes i
  `CAR_CAPACITY_OVERRIDE` i `scraper/build_dataset.py`.
- Afgange fjernes fra API'et når de er sejlet; den "endelige" belægning er
  derfor den sidste måling før afgang (maks. 2 timer før).
- Passagerkapacitet (`maxPax`) oplyses direkte af API'et.
- Scriptet kalder API'et ca. 15 gange pr. kørsel med 1 sekunds pause — samme
  belastning som en enkelt bruger, der kigger timetable-siden igennem.

## Metode og redelighed

Alle rå data gemmes urørt i `data/raw/`, og hver commit er tidsstemplet af
GitHub. Enhver kan reproducere og efterprøve tallene. Fejl eller indvendinger
modtages gerne som issues.
