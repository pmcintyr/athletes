#!/usr/bin/env python3
"""Make a small FAKE athletics.db with the same layout as alabus_scraper.py, to preview the site
without the real data. Every name, club and result here is invented.

  python make_demo_db.py --out demo.db && python build_site.py --db demo.db --out preview
"""
import argparse
import hashlib
import random
import re
import sqlite3
import unicodedata

SQL = """CREATE TABLE IF NOT EXISTS {t} (
 indoor INT, disci_id TEXT, discipline TEXT, sex TEXT, year TEXT, nr INT,
 mark_raw TEXT, mark_value REAL, flags TEXT, hand_timed INT, lower_better INT,
 place INT, round_code TEXT, name TEXT, club TEXT, nat TEXT, birth_date TEXT,
 meeting TEXT, location TEXT, date TEXT, athlete_key TEXT, wind REAL,
 PRIMARY KEY (indoor, disci_id, sex, year, nr))"""

FIRST_M = ["Luca", "Noah", "Elias", "Jonas", "Mathis", "Yanick", "Basile", "Timo", "Loïc", "Florian", "Matteo", "Simon"]
FIRST_W = ["Léa", "Chloé", "Mia", "Elena", "Noémie", "Sara", "Inès", "Lena", "Julie", "Aline", "Zoé", "Anouk"]
LAST = ["Demierre", "Rochat", "Bühler", "Favre", "Gerber", "Monnier", "Keller", "Perrin", "Bolomey", "Wyss",
        "Chappuis", "Rossier", "Bärtschi", "Pittet", "Schmid", "Vuilleumier", "Huber", "Cuendet", "Brügger", "Jaquet"]
# invented clubs in real towns: the site places each club in a canton from the town in its name
CLUBS = ["CA Démo Lausanne", "LV Beispiel Bern", "FSG Exemple Delémont", "TV Muster Aarau", "CEP Fictif Neuchâtel", "LC Probe Zürich",
         "US Esempio Lugano", "LC Fiktiv St. Gallen", "CA Fictif Sion", "TV Beispiel Luzern", "Stade Démo Genève", "TV Muster Wohlen AG",
         "LA Beispiel Fricktal", "CA Exemple Fribourg", "LC Muster Basel", "Team Sans Lieu"]
MEETS = [("Meeting de démonstration", "Lausanne"), ("Abendmeeting Beispiel", "Bern"), ("Meeting fictif du Léman", "Genève"),
         ("Pfingstmeeting Muster", "Zofingen"), ("Meeting d'été exemple", "La Chaux-de-Fonds"),
         # invented meetings in real towns abroad, written like Alabus does ("Town (GER)" or "Town GER"), for the season planner
         ("Meeting fictif du Rhin", "Karlsruhe (GER)"), ("Soirée exemple des Flandres", "Oordegem BEL"),
         ("Meeting de démonstration du Rhône", "Lyon FRA"), ("Beispiel-Laufnacht", "Regensburg (GER)"),
         ("Meeting exemple de la Thur", "St. Gallen"), ("Meeting fictif de Macolin", "Magglingen/Macolin"),
         ("Championnats vaudois exemple", "Lausanne"), ("Berner Kantonalmeisterschaften Beispiel", "Thun"),
         ("Zürcher Kantonalmeisterschaften Beispiel", "Zürich Letzigrund"), ("Regionenmeisterschaften West Beispiel U18", "Fribourg"),
         ("SM U20/U23 Beispiel", "Frauenfeld"), ("Meeting fictif Masters", "Olten")]
SM = ("Schweizer Meisterschaften Aktive", "Zürich")
# label, indoor, lower_better, sex ('' = both), base value (M), spread
EVENTS = [("100 m", 0, 1, "", 10.9, 0.6), ("200 m", 0, 1, "", 22.1, 1.2), ("400 m", 0, 1, "", 49.5, 3.0),
          ("800 m", 0, 1, "", 113.0, 8.0), ("1500 m", 0, 1, "", 233.0, 18.0), ("5000 m", 0, 1, "", 875.0, 70.0),
          ("110 mH 1.067", 0, 1, "M", 14.6, 1.0), ("100 mH 0.838", 0, 1, "W", 14.2, 1.0),
          ("Weit", 0, 0, "", 7.0, 0.6), ("Hoch", 0, 0, "", 2.0, 0.15), ("Kugel 7.26", 0, 0, "M", 15.0, 2.0),
          ("Kugel 4.00", 0, 0, "W", 13.5, 2.0), ("60 m", 1, 1, "", 7.0, 0.35), ("1500 m", 1, 1, "", 236.0, 18.0)]
W_FACTOR = {"100 m": 1.11, "200 m": 1.12, "400 m": 1.13, "800 m": 1.13, "1500 m": 1.12, "5000 m": 1.13,
            "Weit": 0.86, "Hoch": 0.87, "60 m": 1.1}


def key(name, b):
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return hashlib.sha1(f"{re.sub(r'[^a-z ]', '', n).strip()}|{b}".encode()).hexdigest()[:12]


def fmt(v, lower):
    if not lower:
        return f"{v:.2f}"
    if v >= 60:
        m = int(v // 60)
        return f"{m}:{v - m * 60:05.2f}"
    return f"{v:.2f}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="demo.db")
    p.add_argument("--athletes", type=int, default=300)
    a = p.parse_args()
    random.seed(7)
    con = sqlite3.connect(a.out)
    con.execute("DROP TABLE IF EXISTS allresults")
    con.execute(SQL.format(t="allresults"))
    nr, days = 0, {}
    for i in range(a.athletes):
        sex = random.choice("MW")
        name = f"{random.choice(FIRST_M if sex == 'M' else FIRST_W)} {random.choice(LAST)}"
        born = random.randint(1990, 2011)  # some minors on purpose: the build must leave them out
        bdate = f"{born}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
        club = random.choice(CLUBS)
        evs = [e for e in EVENTS if e[3] in ("", sex)]
        main_ev = random.sample(evs, k=random.randint(1, 3))
        talent = random.gauss(0, 1)
        for label, indoor, lower, _, base, spread in main_ev:
            if sex == "W":
                base = base * W_FACTOR.get(label, 1.0)
            for season in range(max(born + 13, 2008), 2027):
                age = season - born
                gain = max(0.0, min(1.0, (age - 13) / 10))  # improves until ~23
                level = base + spread * (1.6 - 1.8 * gain - 0.4 * talent) * (1 if lower else -1)
                if age > 30:
                    level += spread * 0.04 * (age - 30) * (1 if lower else -1)
                if random.random() < 0.08 and season > born + 18:
                    club = random.choice(CLUBS)
                for _ in range(random.randint(2, 6)):
                    meet, loc = random.choice(MEETS + [SM] * 1)
                    if ("U18" in meet and age > 17) or ("U23" in meet and age > 22) or ("Masters" in meet and age < 30):
                        continue
                    if (meet, indoor) not in days:  # each meeting keeps its place in the calendar from year to year
                        days[(meet, indoor)] = (random.randint(1, 3) if indoor else random.randint(5, 9), random.randint(1, 28))
                    month, day = days[(meet, indoor)]
                    date = f"{season}-{month:02d}-{day:02d}"
                    v = level + random.gauss(0, spread * 0.08) * (1 if lower else -1)
                    v = round(max(v, base * 0.5), 2)
                    wind = round(random.uniform(-1.5, 3.2), 1) if (not indoor and label in ("100 m", "200 m", "Weit", "110 mH 1.067", "100 mH 0.838")) else None
                    place = random.randint(1, 8)
                    rc = random.choice(["f1", "f1", "h2", "h1"])
                    nr += 1
                    con.execute("INSERT INTO allresults VALUES (" + ",".join("?" * 22) + ")",
                                (indoor, f"demo-{label}-{indoor}", label, sex, str(season), nr, fmt(v, lower), v, "",
                                 0, lower, place, rc, name, club, "SUI", bdate, meet, loc, date, key(name, bdate), wind))
    con.commit()
    print(f"{a.out}: {nr} fake results for {a.athletes} fake athletes")


if __name__ == "__main__":
    main()
