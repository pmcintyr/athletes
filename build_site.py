#!/usr/bin/env python3
"""
Build the static athlete-profile site from athletics.db (the database made by alabus_scraper.py).

  python build_site.py --db athletics.db
  python build_site.py --db athletics.db --base-url https://athletes.mcintyre.ch/

Output (site/ by default) is plain static files for GitHub Pages: no server, no database online.

  index.html, app.js, style.css   the interactive site (search, profiles, head-to-head, lists)
  a/<xyz>.json                    athlete profiles, sharded by the first 3 characters of the public id
  idx/<ab>.json                   search index, sharded by the first 2 letters of each name part
  lists/<year>.json               top N per discipline, sex, indoor/outdoor and season
  plan/<d>.json, planner.json     season planner: marks run at each meeting over the last seasons (no names),
                                  venue coordinates, championships and entry standards from planner.json
  rk/<xyz>.json                   national, cantonal and club rank of each active athlete, and the nearest rivals
  club/<id>.json, clubs.json      one page of data per club: athletes, season bests, the club's rank in each event
  canton/<XX>.json, region/<id>.json, geo.json, towns.json
                                  cantons and regions (geo.json: which canton each club is in), Swiss places
  p/<id>.html                     plain HTML page per recently active athlete, for Google and link previews
  sitemap*.xml, robots.txt        written when --base-url is given
  meta.json                       disciplines, seasons, counts, build date

Memory: the heavy work (merging, de-duplicating, sorting) runs inside SQLite on disk and athletes are
streamed one by one, so millions of results build in well under 1 GB of RAM.

Privacy defaults
  * Birth YEAR only. The full birth date never leaves the database.
  * Athletes younger than --min-age (default 18) in the current year are left out completely.
  * removals.txt lists public ids (the 10 characters after /a/ or /p/ in a link) to leave out.
  * Public ids are salted hashes. The Alabus athlete_key is sha1(name|birth date), so publishing it
    would reveal the birth date. The salt is in .site_salt (on GitHub it comes from the SITE_KEY secret).

No extra packages needed (standard library only). With the optional geonamescache package installed, the
venues of the season planner get coordinates (travel estimates); without it the planner still lists the meetings.
"""
import argparse
import hashlib
import html
import json
import re
import secrets
import shutil
import sqlite3
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import date as Date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Same lists as alabus_scraper.py. Edit after checking the real meeting names in your data.
CHAMPIONSHIP_PATTERNS = ["schweizer meisterschaft", "championnats suisses", "campionati svizzeri", "swiss championships"]
# Alabus mostly writes "SM Aktive", "SM Halle Nachwuchs (WRC)", "SM U20/U23", "Masters-SM ...", "Team SM".
SM_RE = re.compile(r"(^|[\s/(+])(masters[\s-]?)?sm([\s-]|$)")
NOT_SWISS = ("kilpail", "saul")  # Finnish "SM-kilpailut" are Finnish championships
# "Hallenmeeting inkl. SM Halle Mehrkampf": only the combined events of such a meeting are championships.
SM_MK_RE = re.compile(r"(masters[\s-]?)?sm[\s-]*(halle[\s-]*)?(mehrkampf|mk)\b")
COLS = ["indoor", "disci_id", "discipline", "sex", "year", "mark_raw", "mark_value", "hand_timed", "lower_better",
        "place", "round_code", "name", "club", "nat", "birth_date", "meeting", "location", "date", "athlete_key", "wind"]


# ---------- labels and ordering (copied from alabus_scraper.py so both stay in step) ----------
def fr_label(label):
    l = str(label)
    m = re.match(r"^(Kugel|Diskus|Hammer|Gewicht)\s+([\d.]+)$", l)
    if m:
        n = {"Kugel": "Poids", "Diskus": "Disque", "Hammer": "Marteau", "Gewicht": "Poids lesté"}[m.group(1)]
        return f"{n} {float(m.group(2)):.2f} kg"
    m = re.match(r"^Speer\s+(\d+)$", l)
    if m:
        return f"Javelot {m.group(1)} g"
    m = re.match(r"^(\d+) mH ([\d.]+)$", l)
    if m:
        return f"{m.group(1)} m haies {m.group(2)}"
    simple = {"Hoch": "Hauteur", "Stab": "Perche", "Weit": "Longueur", "Drei": "Triple", "1 Meile": "1 mile",
              "5 km Strasse": "5 km route", "10 km Strasse": "10 km route", "15 km Strasse": "15 km route",
              "10 Meilen Strasse": "10 miles route", "20 km Strasse": "20 km route", "Halbmarathon": "Semi-marathon"}
    if l in simple:
        return simple[l]
    for g, f in (("Siebenkampf", "Heptathlon"), ("Zehnkampf", "Décathlon"), ("Fünfkampf", "Pentathlon")):
        if l.startswith(g):
            return f + l[len(g):]
    return l.replace("Steeple", "steeple")


ROAD_KM = {"5 km Strasse": 5, "10 km Strasse": 10, "15 km Strasse": 15, "10 Meilen Strasse": 16.09, "20 km Strasse": 20,
           "Halbmarathon": 21.1, "Marathon": 42.2}


def discipline_order(label):
    l = str(label)
    if l in ROAD_KM:
        return (0.5, ROAD_KM[l], l)  # road races right after the track running events
    num = re.search(r"[\d.]+", l)
    n = float(num.group(0)) if num else 0.0
    if re.match(r"^\d+\s*x", l, re.I) or "Staffel" in l or "relais" in l.lower():
        g = 5
    elif "kampf" in l.lower() or "Athletic-Cup" in l:
        g = 4
    elif l.startswith(("Kugel", "Diskus", "Speer", "Hammer", "Gewicht")):
        g = 3
    elif l in ("Hoch", "Stab", "Weit", "Drei"):
        g = 2
    elif "mH" in l or "Steeple" in l:
        g = 1
    else:
        g = 0
    return (g, n, l)


def is_wind_event(label, indoor):
    return (not indoor) and (label in ("100 m", "200 m", "Weit", "Drei") or bool(re.match(r"^(80|100|110) mH", str(label))))


def norm_name(x):
    n = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower()
    return " ".join(sorted(re.sub(r"[^a-z ]", " ", n).split()))


def name_tokens(x):
    n = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]+", " ", n).split()


def slug(x):
    return "-".join(name_tokens(x))


def mark_text(raw):
    """'10.90_SR' -> '10.90'; combined events and relays keep only the first token (total or time)."""
    base = str(raw or "").partition("_")[0].strip()
    return base.split()[0] if base else ""


def s(v):
    return "" if v is None or str(v).lower() in ("nan", "none", "nat") else str(v)


def r1(v, nd=1):
    return None if v is None else round(float(v), nd)


# International championships: Olympics, World and European championships of every age group, Universiade,
# youth Olympic events, masters, para and military world championships. Team events, cups, tours and trials are not.
INTL_RE = re.compile(r"""(
   \bolympic\ games\b | youth\ olympic\ games | european\ youth\ olympic\ festival | \beyof\b
 | world\ (athletics\ )?(indoor\ )?championships? | world\ (athletics\ )?u(18|20)\ championships | u(18|20)\ world\ championships
 | world\ (junior|youth)\ championships | world\ indoor\ championships?
 | european\ (athletics\ )?(indoor\ )?championships? | u(18|20|23)\ european\ championships | european\ athletics\ u(18|20|23)\ championships
 | european\ (junior|youth)\ championships
 | universiade | world\ university\ games | military\ world\ games | cism\ -\ weltmeisterschaften | jeux\ de\ la\ francophonie
 | world\ masters?\ (athletics\ |atl\.\ |athl\.\ )?(indoor\ )?champ | masters\ world\ (indoor\ )?championships? | wma\ world
 | european\ masters\ athletics\ (stadia\ )?championships | masters\ european\ (indoor\ )?championships? | master\ european\ championship
 | european\ veterans\ (athletics\ )?champ | evaa\ european\ veterans
 | para\ athletics\ european\ championships | world\ para\ athletics\ championships | ipc\ athletics\ world | world\ deaf\ athletics
)""", re.X)
INTL_NOT = re.compile(r"team|club|cup|tour|relays|trials|private|virtual|ultra|iaum|post-|meeting|transplant|police|small states|"
                      r"games 20|masters games|master european games")


def is_international(meeting):
    m = s(meeting).lower()
    return bool(INTL_RE.search(m)) and not INTL_NOT.search(m)


def is_championship(m):
    return any(p in m for p in CHAMPIONSHIP_PATTERNS) or bool(SM_RE.search(m))


def medal_kind(meeting, place, round_code, label=""):
    """1 = podium at an international championship, 0 = at a Swiss championship, None = no podium."""
    if place is None or not 1 <= int(place) <= 3:
        return None
    rc = s(round_code).lower()
    if not (rc == "" or rc.startswith("f")):  # finals only: heats (h) and series (r) are not medals
        return None
    if is_international(meeting):
        return 1
    m = s(meeting).lower()
    if any(x in m for x in NOT_SWISS) or not is_championship(m):
        return None
    if not is_championship(SM_MK_RE.sub(" ", m)) and "kampf" not in s(label).lower():
        return None  # the meeting's only championship is a combined-events one, and this is not a combined event
    return 0


def is_medal(meeting, place, round_code, label=""):
    return medal_kind(meeting, place, round_code, label) == 0


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


# ---------- database preparation (all inside SQLite, on disk) ----------
def prepare(con, min_age):
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    parts = []
    for prio, t in enumerate(("allresults", "bestlist")):
        if t not in have:
            continue
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
        def col(c):
            if c not in cols:
                return f"NULL AS {c}"
            # Combined events are stored as "6138 13.85 (0.0) / 1.76 / ...": keep the points, which the scraper leaves unparsed.
            if c == "mark_value":
                return "COALESCE(mark_value, CASE WHEN discipline LIKE '%kampf%' THEN CAST(mark_raw AS INTEGER) END) AS mark_value"
            if c == "mark_raw":
                return ("CASE WHEN discipline LIKE '%kampf%' AND instr(trim(mark_raw), ' ') > 1 "
                        "THEN substr(trim(mark_raw), 1, instr(trim(mark_raw), ' ') - 1) ELSE mark_raw END AS mark_raw")
            return c
        sel = ", ".join(col(c) for c in COLS)
        parts.append(f"SELECT {prio} AS src, {sel} FROM {t}")
    if "road" in have:  # road races from the World Athletics top lists (certified courses only), see road_data.py
        parts.append("""SELECT 2 AS src, 0 AS indoor, 'road-' || event AS disci_id, label AS discipline, sex, CAST(season AS TEXT) AS year,
                        mark_raw, mark_value, 0 AS hand_timed, 1 AS lower_better, place, '' AS round_code, name, NULL AS club, nat,
                        birth_date, venue AS meeting, NULL AS location, date, athlete_key, NULL AS wind FROM road""")
    if not parts:
        sys.exit("no 'allresults' or 'bestlist' table in the database")
    max_by = datetime.now().year - min_age
    con.execute("PRAGMA temp_store=FILE")
    con.execute("DROP TABLE IF EXISTS temp.r")
    # One row per real result: the same result can sit in both tables (allresults wins, it carries wind).
    # SQLite takes the other columns from the row with MIN(src).
    con.execute(f"""
        CREATE TEMP TABLE r AS
        SELECT MIN(src) AS src, {", ".join(COLS)},
               CAST(substr(birth_date, 1, 4) AS INTEGER) AS by,
               COALESCE(CAST(substr(date, 1, 4) AS INTEGER), CAST(year AS INTEGER)) AS season
        FROM ({" UNION ALL ".join(parts)})
        WHERE mark_value IS NOT NULL AND athlete_key IS NOT NULL AND birth_date GLOB '[12][0-9][0-9][0-9]*'
        GROUP BY indoor, disci_id, sex, athlete_key, mark_raw, meeting, date""")
    total = con.execute("SELECT COUNT(DISTINCT athlete_key) FROM r").fetchone()[0]
    con.execute("DELETE FROM r WHERE by > ?", (max_by,))
    adults = con.execute("SELECT COUNT(DISTINCT athlete_key) FROM r").fetchone()[0]
    return have, total - adults


def public_ids(con, salt_path, removed_ids, removed_names):
    p = Path(salt_path)
    if not p.exists():
        p.write_text(secrets.token_hex(16), encoding="utf-8")
        print(f"new salt written to {p}: keep this file (stable links) and never commit it")
    salt = p.read_text(encoding="utf-8").strip()
    con.execute("DROP TABLE IF EXISTS temp.ath")
    con.execute("CREATE TEMP TABLE ath (athlete_key TEXT PRIMARY KEY, pid TEXT)")
    rows, gone = [], 0
    for key, name, by in con.execute("SELECT athlete_key, MAX(name), MAX(by) FROM r GROUP BY athlete_key"):
        pid = hashlib.sha1(f"{salt}|{key}".encode()).hexdigest()[:10]
        if pid in removed_ids or (norm_name(name), str(by)) in removed_names:
            gone += 1
            continue
        rows.append((key, pid))
    con.executemany("INSERT INTO ath VALUES (?, ?)", rows)
    con.execute("CREATE INDEX temp.ath_pid ON ath(pid)")
    return len(rows), gone


def disciplines(con):
    rows = con.execute("SELECT indoor, discipline, MAX(lower_better) FROM r GROUP BY indoor, discipline").fetchall()
    rows.sort(key=lambda x: (x[0], discipline_order(x[1])))
    con.execute("DROP TABLE IF EXISTS temp.disc")
    con.execute("CREATE TEMP TABLE disc (indoor INT, discipline TEXT, d INT, PRIMARY KEY (indoor, discipline))")
    con.executemany("INSERT INTO disc VALUES (?, ?, ?)", [(i, d, n) for n, (i, d, _) in enumerate(rows)])
    return [[int(i), d, fr_label(d), int(lb), int(is_wind_event(d, i))] for i, d, lb in rows]


def read_removals(ids_path, names_path):
    ids, names = set(), set()
    if ids_path and Path(ids_path).exists():
        for line in Path(ids_path).read_text(encoding="utf-8").splitlines():
            m = re.search(r"\b([0-9a-f]{10})\b", line.split("#")[0])
            if m:
                ids.add(m.group(1))
    if names_path and Path(names_path).exists():
        for line in Path(names_path).read_text(encoding="utf-8").splitlines()[1:]:
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 2 and parts[0]:
                names.add((norm_name(parts[0]), parts[1]))
    return ids, names


# ---------- one athlete ----------
def best_by(rows, keyf):
    """Best legal mark per key: electronic timing first (hand times only if no electronic one), earliest on a tie."""
    best = {}
    for r in rows:
        if r["assisted"]:
            continue
        k = keyf(r)
        rank = (r["hand"], r["sortv"], r["date"] or "9999")
        if k not in best or rank < best[k][0]:
            best[k] = (rank, r)
    return {k: v[1] for k, v in best.items()}


class Dict:
    """Per-shard string table: repeated meetings, venues and clubs are stored once and referenced by number."""
    def __init__(self):
        self.items, self.pos = [], {}

    def __call__(self, v):
        if v not in self.pos:
            self.pos[v] = len(self.items)
            self.items.append(v)
        return self.pos[v]


def athlete(rows, M, L, C):
    """rows: newest first. Meetings (M), venues (L) and clubs (C) become numbers into the shard's tables.
    Result rows carry the mark text only; the browser reads its numeric value from it."""
    latest = rows[0]
    clubs = list(dict.fromkeys(r["club"] for r in reversed(rows) if r["club"]))
    last = {}  # latest season per event: events still done come first, children's events of long ago last
    for r in rows:
        last.setdefault(r["d"], r["season"])
    pbr = sorted(best_by(rows, lambda r: r["d"]).values(), key=lambda r: (-int(last[r["d"]] or 0), r["d"]))
    pb = [[r["d"], mark_text(r["mark"]), r1(r["wind"]), r["date"], M(r["meeting"]), L(r["loc"]), r["hand"]] for r in pbr]
    sb = [[d, season, mark_text(r["mark"]), r1(r["value"], 3)]
          for (d, season), r in sorted(best_by(rows, lambda r: (r["d"], r["season"])).items())]
    medals, seen = [], set()
    for r in rows:
        kind = medal_kind(r["meeting"], r["place"], r["round"], r["label"])
        if kind is not None:
            k = (r["season"], r["d"], r["meeting"])
            if k not in seen:
                seen.add(k)
                meet = re.sub(r"\s*\((WRC|WCR)\)", "", r["meeting"]).strip()  # World Ranking Competition tag
                medals.append([r["season"], r["d"], int(r["place"]), meet, kind, mark_text(r["mark"])])
    medals.sort(key=lambda m: (-m[4], -m[0], m[2]))
    res = [[r["d"], r["date"], mark_text(r["mark"]), r1(r["wind"]), r["place"], r["round"], M(r["meeting"]),
            L(r["loc"]), C(r["club"]), r["assisted"]] for r in rows]
    prof = {"n": latest["name"], "by": latest["by"], "s": latest["sex"], "c": next((r["club"] for r in rows if r["club"]), ""), "cs": clubs[-6:],
            "nat": latest["nat"], "pb": pb, "sb": sb, "m": medals, "r": res}
    page = {"pb": [[r["d"], mark_text(r["mark"]), r1(r["wind"]), r["date"], r["meeting"], r["loc"]] for r in pbr]}
    return prof, page


# ---------- official cadres (Swiss Athletics lists) and Swiss Starters limits ----------
def limit_value(t):
    parts = str(t).strip().split(":")
    v = 0.0
    for x in parts:
        v = v * 60 + float(x)
    return v


class Cadres:
    """cadres.json: the official cadre lists (name, birth year, club, group) and the Swiss Starters limits.
    Athletes are matched by birth year and name (same words, or one name contained in the other with 2+ words)."""

    def __init__(self, path):
        f = Path(path) if path else None
        self.data = json.loads(f.read_text(encoding="utf-8")) if f and f.exists() else {}
        self.lists = self.data.get("cadres", [])
        self.by_year = defaultdict(list)
        for ci, c in enumerate(self.lists):
            for ei, e in enumerate(c["athletes"]):
                self.by_year[int(e[1])].append((ci, ei, frozenset(name_tokens(e[0]))))
        self.members, self.matched, self.achieved = defaultdict(list), set(), []
        lim = self.data.get("limits") or {}
        self.year = lim.get("year")
        self.events = [(n, ev["name"], sex, label, ev[sex], limit_value(ev[sex]))
                       for n, ev in enumerate(lim.get("events", [])) for sex in ("M", "W") for label in ev["labels"].get(sex, [])]

    def match(self, names, by):
        toks = [frozenset(name_tokens(n)) for n in names]
        found = []
        for ci, ei, lt in self.by_year.get(int(by or 0), []):
            if any(t == lt or (len(t & lt) >= 2 and (t <= lt or lt <= t)) for t in toks):
                found.append((ci, ei))
        return found

    def limits_for(self, rows, sex, disc):
        """Best mark per limit event in the limit year: legal wind, electronic timing, indoor marks included."""
        best = {}
        for r in rows:
            if r["season"] != self.year or r["assisted"] or r["hand"]:
                continue
            indoor, lower, wind_event = disc[r["d"]][0], disc[r["d"]][3] == 1, disc[r["d"]][4]
            if wind_event and not indoor and r["wind"] is None:
                continue  # no wind reading: cannot be checked
            for n, name, esex, label, text, val in self.events:
                if esex == sex and label == r["label"] and (r["value"] <= val if lower else r["value"] >= val):
                    if n not in best or r["sortv"] < best[n][0]:
                        shown = name.split(" / ")[0 if sex == "M" else -1]  # "110 m haies / 100 m haies"
                        best[n] = (r["sortv"], [n, shown[:1].upper() + shown[1:], mark_text(r["mark"]), text, r["date"], indoor])
        return [v[1] for _, v in sorted(best.items())]


# ---------- season planner: which marks were run at which meeting, and where that meeting is ----------
# Alabus writes venues abroad as "Karlsruhe (GER)": IOC codes to the ISO codes of the city list.
IOC_ISO = {"SUI": "CH", "GER": "DE", "FRA": "FR", "ITA": "IT", "AUT": "AT", "BEL": "BE", "NED": "NL", "LUX": "LU", "LIE": "LI",
           "ESP": "ES", "POR": "PT", "GBR": "GB", "IRL": "IE", "DEN": "DK", "SWE": "SE", "NOR": "NO", "FIN": "FI", "ISL": "IS",
           "POL": "PL", "CZE": "CZ", "SVK": "SK", "HUN": "HU", "SLO": "SI", "CRO": "HR", "SRB": "RS", "BIH": "BA", "MNE": "ME",
           "MKD": "MK", "ALB": "AL", "GRE": "GR", "BUL": "BG", "ROU": "RO", "MDA": "MD", "UKR": "UA", "EST": "EE", "LAT": "LV",
           "LTU": "LT", "TUR": "TR", "CYP": "CY", "MLT": "MT", "MON": "MC", "AND": "AD", "SMR": "SM", "ISR": "IL", "USA": "US",
           "CAN": "CA", "MAR": "MA", "RSA": "ZA", "KEN": "KE", "ETH": "ET", "QAT": "QA", "UAE": "AE", "JPN": "JP", "CHN": "CN",
           "AUS": "AU", "NZL": "NZ", "BRA": "BR", "MEX": "MX", "TUN": "TN", "ALG": "DZ", "KOR": "KR", "IND": "IN"}
EUROPE = {v for k, v in IOC_ISO.items() if k not in ("USA", "CAN", "MAR", "RSA", "KEN", "ETH", "QAT", "UAE", "JPN", "CHN", "AUS",
                                                     "NZL", "BRA", "MEX", "TUN", "ALG", "KOR", "IND")}


def geo_norm(x):
    n = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", n).split())


CAPITALS = {"AG": "Aarau", "AI": "Appenzell", "AR": "Herisau", "BE": "Bern", "BL": "Liestal", "BS": "Basel", "FR": "Fribourg",
            "GE": "Genève", "GL": "Glarus", "GR": "Chur", "JU": "Delémont", "LU": "Luzern", "NE": "Neuchâtel", "NW": "Stans",
            "OW": "Sarnen", "SG": "St. Gallen", "SH": "Schaffhausen", "SO": "Solothurn", "SZ": "Schwyz", "TG": "Frauenfeld",
            "TI": "Bellinzona", "UR": "Altdorf", "VD": "Lausanne", "VS": "Sion", "ZG": "Zug", "ZH": "Zürich"}
# Words of club names that are not the town, even where a Swiss place has that name.
CLUB_STOP = {"stade", "club", "sport", "sports", "team", "athletics", "athletisme", "leichtathletik", "atletica", "athletic", "run",
             "more", "track", "old", "boys", "olympic", "satus", "gym", "turnverein", "les", "des", "see", "lac", "berg", "tal"}


class Gazetteer:
    """Venue or club name -> place. planner.json 'venues' first, then the GeoNames city list of the optional
    geonamescache package (places of 1000+ inhabitants, with their names in other languages)."""

    def __init__(self, manual):
        self.manual = {geo_norm(k): v for k, v in (manual or {}).items()}
        self.idx, self.swiss, self.cache, self.missed = None, defaultdict(list), {}, defaultdict(int)
        try:
            import geonamescache
        except ImportError:
            print("geonamescache is not installed: venues and clubs get no coordinates (pip install geonamescache)")
            return
        idx = {}
        for c in geonamescache.GeonamesCache(min_city_population=1000).get_cities().values():
            cc, pop = c["countrycode"], int(c.get("population") or 0)
            lat, lon = round(float(c["latitude"]), 3), round(float(c["longitude"]), 3)
            adm = s(c.get("admin1code")) if cc == "CH" else ""
            names = [(c["name"], 1)] + ([(n, 0) for n in c.get("alternatenames") or []] if cc in EUROPE else [])
            for n, primary in names:
                k = (cc, geo_norm(n))
                if not k[1]:
                    continue
                if k not in idx or (primary, pop) > idx[k][0]:
                    idx[k] = ((primary, pop), lat, lon, adm, c["name"])
                if cc == "CH":
                    self.swiss[k[1]].append(((primary, pop), lat, lon, adm, c["name"]))
        self.idx = idx

    def towns(self):
        """Swiss places for the planner's departure field: [name, lat, lon, canton], one per name."""
        best = {}
        for hits in self.swiss.values():
            for (primary, pop), lat, lon, adm, name in hits:
                if primary and adm in CAPITALS and (name not in best or pop > best[name][0]):
                    best[name] = (pop, [name, lat, lon, adm])
        return [v[1] for _, v in sorted(best.items(), key=lambda x: geo_norm(x[0]))]

    def swiss_place(self, name, canton=None):
        """A Swiss place by name -> (lat, lon, canton, name, inhabitants); with several places of that name, the one
        in `canton`, else the largest."""
        n = geo_norm(name)
        m = self.manual.get(n)
        if m:
            return (m[0], m[1], m[2] if len(m) > 2 else (canton or ""), name, 10 ** 9)
        hits = []
        for cand in (n, re.sub(r"^st ", "sankt ", n), re.sub(r"^st ", "saint ", n)):
            hits += self.swiss.get(cand, [])
        if canton and any(h[3] == canton for h in hits):
            hits = [h for h in hits if h[3] == canton]
        if not hits:
            return None
        h = max(hits, key=lambda h: h[0])
        return (h[1], h[2], h[3], h[4], h[0][1])

    def find(self, venue):
        """-> [lat, lon, country (IOC code), canton] or None."""
        v = s(venue).strip()
        if v not in self.cache:
            self.cache[v] = self._find(v)
        if v and self.cache[v] is None:
            self.missed[v] += 1
        return self.cache[v]

    def _find(self, v):
        # Alabus writes "Karlsruhe (GER)" or "Madrid ESP"; no code means Switzerland. "Chailly-sur-Montreux (VD)" is a canton.
        ioc, base = "SUI", v
        m = re.search(r"(?:\(([A-Z]{3})\)|\s([A-Z]{3}))\s*$", v)
        if m and (m.group(1) or m.group(2)) in IOC_ISO:
            ioc, base = m.group(1) or m.group(2), v[:m.start()]
        for text in (v, base):
            hit = self.manual.get(geo_norm(text))
            if hit:
                return [hit[0], hit[1], ioc, hit[2] if len(hit) > 2 else ""]
        canton = None
        m = re.search(r"\(([A-Z]{2})\)\s*$", base)
        if m and m.group(1) in CAPITALS:
            canton, base = m.group(1), base[:m.start()]
        parts = [base] + [x for x in re.split(r"[/,()]| - ", base) if x.strip()]
        if ioc == "SUI":
            toks = geo_norm(base).split()
            parts += [" ".join(toks[:n]) for n in range(len(toks) - 1, 0, -1)]  # "Zürich Letzigrund" -> "Zürich"
            parts += [t for t in toks if len(t) >= 4 and t not in CLUB_STOP]    # "Sportanlage Wintersried Ibach" -> "Ibach"
            for text in parts:
                hit = self.swiss_place(text, canton)
                if hit:
                    return [hit[0], hit[1], ioc, hit[2]]
            # "Berlin", "Sindelfingen": a venue abroad written without its country, if it is a town of 15,000+ in Europe
            hits = [(h[0][1], h, k) for k, cc in IOC_ISO.items() if cc in EUROPE and cc != "CH"
                    for h in [(self.idx or {}).get((cc, geo_norm(base)))] if h and h[0][1] >= 15000]
            if hits:
                _, h, code = max(hits, key=lambda x: x[0])
                return [h[1], h[2], code, ""]
            return None
        cc = IOC_ISO.get(ioc)
        if self.idx is None:
            return None
        for text in parts:
            hit = self.idx.get((cc, geo_norm(text)))
            if hit:
                return [hit[1], hit[2], ioc, ""]
        return None

    def club(self, club, cfg):
        """Where a club is, from the town in its name -> [canton, town, lat, lon] (town parts None if only the canton
        is known) or None. geo.json 'clubs' decides first, then towns, then the regional words of 'areas'."""
        ov = (cfg.get("clubs") or {}).get(club)
        if ov:
            canton, town = (ov, None) if isinstance(ov, str) else (ov[0], ov[1] if len(ov) > 1 else None)
            if not isinstance(ov, str) and len(ov) > 3:
                return [canton, town, ov[2], ov[3]]
            hit = self.swiss_place(town, canton) if town else None
            return [canton, town if hit else None, hit[0] if hit else None, hit[1] if hit else None]
        m = re.search(r"(?:^|\s)\(?([A-Z]{2})\)?\s*$", club)  # "TV Wohlen AG"
        code = m.group(1) if m and m.group(1) in CAPITALS else None
        toks = geo_norm(club[:m.start()] if code else club).split()
        for n in range(min(4, len(toks)), 0, -1):
            found = []
            for i in range(len(toks) - n + 1):
                gram = " ".join(toks[i:i + n])
                if n == 1 and (len(gram) < 3 or gram in CLUB_STOP):
                    continue
                hit = self.swiss_place(gram, code)
                if hit:
                    found.append(((bool(code) and hit[2] == code, hit[4]), hit))
            if found:
                hit = max(found, key=lambda x: x[0])[1]
                return [hit[2], hit[3], hit[0], hit[1]]
        areas = cfg.get("areas") or {}
        for n in (3, 2, 1):
            for i in range(len(toks) - n + 1):
                if " ".join(toks[i:i + n]) in areas:
                    return [areas[" ".join(toks[i:i + n])], None, None, None]
        return [code, None, None, None] if code else None


REGIONAL_RE = re.compile(r"regionen ?meisterschaft|\brm\b|championnats? regiona|championnats? romand|campionati regionali|"
                         r"\b(ost|inner|zentral|nordwest|west)schweizer (hallen ?)?meisterschaft")
CANTONAL_RE = re.compile(r"kantonal ?meisterschaft|kantonale? (hallen ?)?meisterschaft|\bkm\b|kantonalfinal|"
                         r"championnats? (cantona|vaudois|genevois|fribourgeois|valaisan|neuchatelois|jurassien|bernois|tessinois)|"
                         r"campionati (ticinesi|cantonali)|"
                         r"\b(berner|zurcher|aargauer|basler|luzerner|st galler|thurgauer|bundner|solothurner|schaffhauser|schwyzer|zuger|"
                         r"urner|glarner|appenzeller|walliser|freiburger|tessiner|nidwaldner|obwaldner|baselbieter)\b.{0,14}meisterschaft")


def champ_kind(meeting):
    """0 = meeting, 1 = Swiss championship, 2 = regional championship, 3 = cantonal championship (from the name)."""
    low = s(meeting).lower()
    if is_championship(low) and not any(x in low for x in NOT_SWISS):
        return 1
    n = geo_norm(meeting)
    return 2 if REGIONAL_RE.search(n) else 3 if CANTONAL_RE.search(n) else 0


def age_limit(meeting, ages):
    """Age category a competition is reserved for, read from its name and confirmed by who took part:
    'U16' to 'U23', 'M' (masters), '' = open. `ages`: ages of the athletes with a result there."""
    n = geo_norm(meeting)
    if re.search(r"\bmasters?\b|senioren|veteran", n):
        return "M" if ages and min(ages) >= 30 else ""
    found = [int(x) for x in re.findall(r"\bu ?(14|16|18|20|23)\b", n)]
    if not found and re.search(r"nachwuchs|jeunesse|releve|giovanil|schuler|ecoliers|jugend|junior", n):
        found = [20]
    if not found:
        return ""
    top = max(found)
    return f"U{top}" if ages and max(ages) < top else ""  # older athletes took part: open to them too


def series_key(meeting):
    """Same meeting from year to year: '42. Pfingstmeeting 2026 (WRC)' and '41. Pfingstmeeting 2025' are one series."""
    m = re.sub(r"\((wrc|wcr)\)", " ", geo_norm(re.sub(r"\((WRC|WCR)\)", " ", s(meeting))))
    return " ".join(re.sub(r"\d+", " ", m).split())


def planner(con, disc, out, cfg, gaz):
    """plan/<d>.json per discipline: for each meeting series of the last seasons, the marks run there (numbers only).
    The browser turns them into race suggestions around an athlete's objectives."""
    shutil.rmtree(out / "plan", ignore_errors=True)
    (out / "plan").mkdir(parents=True, exist_ok=True)
    last = con.execute("SELECT MAX(season) FROM r WHERE src < 2").fetchone()[0]
    if not cfg or last is None:
        return None
    since = int(last) - int(cfg.get("seasons", 3)) + 1
    q = """SELECT d.d, r.sex, r.meeting, r.location, r.date, r.mark_value, r.season - r.by
           FROM r JOIN ath a ON a.athlete_key = r.athlete_key
                  JOIN disc d ON d.indoor = r.indoor AND d.discipline = r.discipline
           WHERE r.src < 2 AND r.season >= ? AND COALESCE(r.hand_timed, 0) = 0 AND r.sex IN ('M', 'W')
             AND r.meeting IS NOT NULL AND r.date GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'
           ORDER BY d.d"""
    written, n_series, kinds = [], 0, defaultdict(int)

    def flush(d, groups):
        nonlocal n_series
        if d is None or discipline_order(disc[d][1])[0] == 5:  # no relays
            return
        if sum(len(v) for g in groups.values() for v in g["days"].values()) < 20:
            return
        lower, venues, doc = disc[d][3] == 1, Dict(), {"M": [], "W": []}
        for (sex, _, venue), g in groups.items():
            days, editions = sorted(g["days"]), []
            for day in days:  # heats and finals on consecutive days are one edition, dated on its busiest day
                prev = editions[-1] if editions else None
                if prev and (Date.fromisoformat(day) - Date.fromisoformat(prev["last"])).days <= 2:
                    prev["last"] = day
                    prev["vals"] += g["days"][day]
                    if len(g["days"][day]) > prev["n"]:
                        prev["day"], prev["n"] = day, len(g["days"][day])
                else:
                    editions.append({"day": day, "last": day, "n": len(g["days"][day]), "vals": list(g["days"][day])})
            kind = champ_kind(g["name"])
            kinds[kind] += 1
            doc[sex].append([g["name"], venues(venue), kind,
                             [[e["day"], sorted((round(v, 2) for v in e["vals"]), reverse=not lower)] for e in editions],
                             age_limit(g["name"], g["ages"])])
        for sex in doc:
            doc[sex].sort(key=lambda x: -sum(len(e[1]) for e in x[3]))
            n_series += len(doc[sex])
        doc["v"] = [[v] + (gaz.find(v) or []) for v in venues.items]
        dump(out / "plan" / f"{d}.json", doc)
        written.append(d)

    cur, groups = None, {}
    for d, sex, meeting, loc, day, value, age in con.execute(q, (since,)):
        if d != cur:
            flush(cur, groups)
            cur, groups = d, {}
        if is_international(meeting):
            continue  # championships with entry standards are objectives, not races to enter
        try:
            Date.fromisoformat(day)
        except ValueError:
            continue
        g = groups.setdefault((sex, series_key(meeting), s(loc).strip()), {"name": "", "latest": "", "days": defaultdict(list), "ages": set()})
        if day >= g["latest"]:
            g["latest"], g["name"] = day, re.sub(r"\s*\((WRC|WCR)\)", "", s(meeting)).strip()
        g["days"][day].append(value)
        if age is not None:
            g["ages"].add(int(age))
    flush(cur, groups)
    missed = sorted(gaz.missed.items(), key=lambda x: -x[1])
    print(f"planner: {len(written)} disciplines, {n_series:,} meeting series since {since} "
          f"({kinds[1]} Swiss, {kinds[2]} regional, {kinds[3]} cantonal championships)"
          + (f", {len(missed)} venues without coordinates: {', '.join(v for v, _ in missed[:150])}"
             + (f" and {len(missed) - 150} more" if len(missed) > 150 else "") if missed and gaz.idx is not None else ""))
    dump(out / "planner.json", {k: v for k, v in cfg.items() if k in ("homes", "championships", "omio")})
    return {"disc": written, "from": since, "to": int(last)}


# ---------- clubs, cantons and regions ----------
def rank_groups(entries):
    """entries: [(sortv, key...)] of one event -> the same sorted, each with its rank (equal marks share a rank)."""
    entries.sort(key=lambda e: e[0])
    out, prev, rk = [], None, 0
    for pos, e in enumerate(entries, 1):
        if e[0] != prev:
            rk, prev = pos, e[0]
        out.append((rk, e))
    return out


def geography(out, disc, active, cfg, gaz, club_geo, years, min_field=20):
    """active: athletes with a result in the last two seasons, each with its club, canton and season bests.
    Writes the rank shards (rk/), one file per club, canton and region, and the comparison of cantons (geo.json).
    `years` = (outdoor, indoor) season the ranks are for: the latest one with enough results."""
    for sub in ("rk", "club", "canton", "region"):
        shutil.rmtree(out / sub, ignore_errors=True)
        (out / sub).mkdir(parents=True, exist_ok=True)
    region_of = {c: r["id"] for r in cfg.get("regions", []) for c in r["cantons"]}
    ref = lambda d: years[disc[d][0]]
    events = defaultdict(list)
    for i, a in enumerate(active):
        for (d, season), (sv, mark) in a["sb"].items():
            if season == ref(d) and discipline_order(disc[d][1])[0] != 5:
                events[(d, a["sex"])].append((sv, i, mark))

    ranks = defaultdict(list)          # athlete -> [d, season, national rank, of, canton rank, of, club rank, of]
    rivals = {}                        # athlete -> (score, d, neighbours)
    club_ev = defaultdict(list)        # club id -> [d, sex, rank among clubs, clubs, mark, pid, name, athletes of the club]
    best = {"canton": defaultdict(lambda: defaultdict(list)), "region": defaultdict(lambda: defaultdict(list))}
    stat = defaultdict(lambda: defaultdict(int))
    for (d, sex), entries in sorted(events.items()):
        ranked = rank_groups(entries)
        n, main = len(ranked), len(ranked) >= min_field
        seen = {"canton": defaultdict(int), "club": defaultdict(int)}
        sizes = {"canton": defaultdict(int), "club": defaultdict(int)}
        for _, (_, i, _) in ranked:
            for kind in sizes:
                if active[i][kind]:
                    sizes[kind][active[i][kind]] += 1
        club_order = []
        for pos, (rk, (sv, i, mark)) in enumerate(ranked):
            a, row = active[i], [d, ref(d), rk, n]
            for kind in ("canton", "club"):
                k = a[kind]
                if k:
                    seen[kind][k] += 1
                    row += [seen[kind][k], sizes[kind][k]]
                else:
                    row += [None, None]
            ranks[i].append(row)
            if a["club"] and a["club"] not in club_order:
                club_order.append(a["club"])
                club_ev[a["club"]].append([d, sex, len(club_order), 0, mark, a["pid"], a["n"], sizes["club"][a["club"]]])
            if main:
                score = (rk / n, -n)
                if i not in rivals or score < rivals[i][0]:
                    near = [ranked[j] for j in range(max(0, pos - 3), min(n, pos + 4)) if j != pos]
                    rivals[i] = (score, d, [[active[e[1]]["pid"], active[e[1]]["n"], e[2], r] for r, e in near])
                can = a["canton"]
                if can:
                    for kind, key in (("canton", can), ("region", region_of.get(can))):
                        if key and len(best[kind][key][(sex, d)]) < 5:
                            best[kind][key][(sex, d)].append([len(best[kind][key][(sex, d)]) + 1, mark, a["pid"], a["n"], a["by"], a["c"], rk])
                    if rk <= 10:
                        stat[can]["top10"] += 1
                    if rk <= 3:
                        stat[can]["top3"] += 1
                    if rk == 1:
                        stat[can]["lead"] += 1
        for club in club_order:
            club_ev[club][-1][3] = len(club_order)

    shards = defaultdict(dict)
    for i, a in enumerate(active):
        if ranks.get(i):
            e = {"r": sorted(ranks[i], key=lambda r: (r[2] / r[3], r[0]))}
            if i in rivals:
                e["v"] = [rivals[i][1], rivals[i][2]]
            shards[a["pid"][:3]][a["pid"]] = e
    for k, v in shards.items():
        dump(out / "rk" / f"{k}.json", v)

    clubs = defaultdict(list)
    for i, a in enumerate(active):
        if a["club"]:
            clubs[a["club"]].append(i)
        if a["canton"]:
            stat[a["canton"]]["ath"] += 1
            stat[a["canton"]]["pod"] += a["pod"]
    index, by_canton, unplaced = [], defaultdict(list), [0, 0]
    for cid, members in sorted(clubs.items()):
        name = active[max(members, key=lambda i: active[i]["last"])]["c"]
        g = club_geo(name)
        canton = g[0] if g else None
        rows = []
        for i in sorted(members, key=lambda i: norm_name(active[i]["n"])):
            a, nat = active[i], {r[0]: r[2] for r in ranks.get(i, [])}
            evs = sorted({d for d, _ in a["sb"]}, key=lambda d: (nat.get(d, 10 ** 6), d))
            rows.append([a["pid"], a["n"], a["by"], a["sex"],
                         [[d, (a["sb"].get((d, ref(d))) or [0, None])[1], (a["sb"].get((d, ref(d) - 1)) or [0, None])[1],
                           a["pb"].get(d), nat.get(d)] for d in evs]])
        dump(out / "club" / f"{cid}.json", {"n": name, "g": g, "a": rows, "ev": sorted(club_ev.get(cid, []), key=lambda e: (e[2] / max(e[3], 1), e[0]))})
        index.append([cid, name, canton, len(members)])
        if canton:
            stat[canton]["clubs"] += 1
            by_canton[canton].append([cid, name, len(members)])
        else:
            unplaced[0] += 1
            unplaced[1] += len(members)

    def unit_file(kind, key, cantons):
        b = best[kind].get(key, {})
        dump(out / kind / f"{key}.json", {
            "clubs": sorted((c for k in cantons for c in by_canton.get(k, [])), key=lambda c: (-c[2], c[1])),
            "best": {sex: [[d, rows] for (sx, d), rows in sorted(b.items(), key=lambda x: x[0][1]) if sx == sex] for sex in ("M", "W")}})
    names = cfg.get("cantons") or {}
    for canton in names:
        unit_file("canton", canton, [canton])
    for r in cfg.get("regions", []):
        unit_file("region", r["id"], r["cantons"])
    keys = ("clubs", "ath", "top10", "top3", "lead", "pod")
    dump(out / "clubs.json", index)
    dump(out / "geo.json", {
        "years": list(years), "min_field": min_field,
        "regions": [{k: r.get(k) for k in ("id", "name", "short", "cantons", "source")} for r in cfg.get("regions", [])],
        "cantons": {c: [names[c], region_of.get(c)] + [stat[c][k] for k in keys] for c in names},
        "capitals": {c: (lambda h: [CAPITALS[c], h[0], h[1]] if h else None)(gaz.swiss_place(CAPITALS[c], c)) for c in names if c in CAPITALS},
        "unplaced": unplaced})
    dump(out / "towns.json", gaz.towns())
    lost = sorted(((n, name) for cid, name, canton, n in index if not canton), reverse=True)
    print(f"clubs: {len(index)} with active athletes, {len(index) - unplaced[0]} placed in a canton"
          + (f"; not placed ({unplaced[1]} athletes): {', '.join(f'{name} ({n})' for n, name in lost[:120])}"
             + (f" and {len(lost) - 120} more" if len(lost) > 120 else "") if lost else ""))
    return {"years": list(years), "clubs": len(index)}


# ---------- static HTML page per athlete (for search engines and link previews) ----------
PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} ({by}) – {title}</title><meta name="description" content="{desc}">{canonical}
<meta property="og:title" content="{name} ({by})"><meta property="og:description" content="{desc}"><link rel="stylesheet" href="../style.css"></head>
<body><header class="top"><div class="wrap top-in"><a class="brand" href="../#/">{title}</a></div></header><main class="wrap">
<section class="profile-head"><h1>{name}</h1>{badges}<div class="facts"><span>{born} en <strong class="num">{by}</strong></span>{club}</div>
<p class="cta"><a class="more" href="../#/a/{pid}/{slug}">Progression, tous les résultats et confrontations</a></p></section>
<h2>Records personnels</h2><div class="cols">{lanes}</div>{medals}</main>
<footer class="foot"><div class="wrap"><p>Site indépendant et non officiel. Résultats publics de Swiss Athletics (Alabus), mis à jour le {updated}.{contact}</p></div></footer></body></html>
"""


def profile_page(pid, p, page, disc, title, base_url, contact, updated, cad_names=None):
    e = html.escape
    names = {i: (d[2] + (" (salle)" if d[0] else "")) for i, d in enumerate(disc)}

    def lanes(rows, head):
        if not rows:
            return ""
        items = "".join(
            f'<li class="lane"><span class="ev">{e(disc[r[0]][2])}</span><span class="mk">{e(r[1])}'
            f'{f"<small>{r[2]:+.1f}</small>" if disc[r[0]][4] and r[2] is not None else ""}</span>'
            f'<span class="where">{fmt_date(r[3])}, {e(r[4])}</span></li>' for r in rows)
        return f'<div><h3>{head}</h3><ul class="lanes">{items}</ul></div>'

    out = [r for r in page["pb"] if disc[r[0]][0] == 0]
    ins = [r for r in page["pb"] if disc[r[0]][0] == 1]
    best = ", ".join(f"{names[r[0]]} {r[1]}" for r in (out + ins)[:5])
    desc = f"Records personnels de {p['n']}" + (f" ({p['c']})" if p["c"] else "") + (f" : {best}." if best else ".")
    medals = ""
    if p["m"]:
        def li(rows):
            return "".join(f'<li><span class="medal p{m[2]}">{m[2]}</span><span><strong class="num">{m[0]}</strong> '
                           f'{e(names[m[1]])}, {e(m[3])} <span class="num">{e(m[5])}</span></span></li>' for m in rows)
        intl, nat = [m for m in p["m"] if m[4]], [m for m in p["m"] if not m[4]]
        medals = ("<h2>Palmarès</h2>" + (f'<h3>Championnats internationaux</h3><ul class="medals">{li(intl)}</ul>' if intl else "")
                  + (f'<h3>Championnats suisses</h3><ul class="medals">{li(nat)}</ul>' if nat else ""))
    badges = "".join(f'<span class="badge">{e((cad_names or {}).get(k[0], k[0]))}</span>' for k in p.get("k", []))
    badges = f'<div class="badges">{badges}</div>' if badges else ""
    return PAGE.format(
        name=e(p["n"]), by=p["by"], title=e(title), desc=e(desc), pid=pid, slug=slug(p["n"]),
        canonical=f'<link rel="canonical" href="{e(base_url)}p/{pid}.html">' if base_url else "",
        born="Née" if p["s"] == "W" else "Né", club=f'<span>Club <strong>{e(p["c"])}</strong></span>' if p["c"] else "",
        lanes=lanes(out, "Plein air") + lanes(ins, "En salle") or '<p class="empty">Aucune performance régulière.</p>',
        medals=medals, badges=badges, updated=fmt_date(updated),
        contact=f' Corriger ou retirer ce profil : <a href="mailto:{e(contact)}">{e(contact)}</a>.' if contact else "")


def fmt_date(iso):
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s(iso))
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else s(iso)


# ---------- build ----------
def build(a):
    t0 = time.time()
    out = Path(a.out)
    if not Path(a.db).exists():
        sys.exit(f"{a.db} not found. Run alabus_scraper.py fetch first, or pass --db.")
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    have, minors = prepare(con, a.min_age)
    removed_ids, removed_names = read_removals(a.removals_ids, a.removals)
    n_ath, gone = public_ids(con, a.salt, removed_ids, removed_names)
    if not n_ath:
        sys.exit("nothing left to publish after the privacy filters")
    disc = disciplines(con)
    cadres = Cadres(a.cadres)
    max_by = datetime.now().year - a.min_age
    print(f"{n_ath:,} athletes to publish ({minors:,} under {a.min_age} left out, {gone} removed on request)")

    pf = Path(a.planner) if a.planner else None
    pcfg = json.loads(pf.read_text(encoding="utf-8")) if pf and pf.exists() else None
    gf = Path(a.geo) if a.geo else None
    gcfg = json.loads(gf.read_text(encoding="utf-8")) if gf and gf.exists() else None
    gaz = Gazetteer((pcfg or {}).get("venues"))
    geo_cache = {}

    def club_geo(club):
        if club not in geo_cache:
            geo_cache[club] = gaz.club(club, gcfg) if gcfg and club else None
        return geo_cache[club]
    # Season the ranks are for, outdoor and indoor: the latest one, unless it has barely started.
    latest = con.execute("SELECT MAX(season) FROM r WHERE src < 2").fetchone()[0] or datetime.now().year
    counts = dict(((i, y), n) for i, y, n in con.execute(
        "SELECT indoor, season, COUNT(*) FROM r WHERE src < 2 AND season >= ? GROUP BY indoor, season", (latest - 1,)))
    ref_years = tuple(latest if counts.get((i, latest), 0) >= 0.3 * counts.get((i, latest - 1), 0) else latest - 1 for i in (0, 1))
    active = []

    for sub in ("a", "idx", "lists", "p"):  # plan/ is rebuilt in planner()
        shutil.rmtree(out / sub, ignore_errors=True)
        (out / sub).mkdir(parents=True, exist_ok=True)
    for f in out.glob("sitemap*.xml"):
        f.unlink()
    updated = datetime.now().strftime("%Y-%m-%d")

    # Stream every result, sorted by public id, newest first: one athlete in memory at a time.
    q = """SELECT a.pid, d.d, r.date, r.mark_raw, r.mark_value, r.wind, r.place, r.round_code, r.meeting, r.location,
                  r.club, r.name, r.by, r.sex, r.nat, r.hand_timed, r.lower_better, r.indoor, r.discipline, r.season, r.src
           FROM r JOIN ath a ON a.athlete_key = r.athlete_key
                  JOIN disc d ON d.indoor = r.indoor AND d.discipline = r.discipline
           ORDER BY a.pid, r.date DESC, d.d"""
    shard, shard_id, cur, cur_pid = {}, None, [], None
    tables = [Dict(), Dict(), Dict()]

    def write_shard():
        dump(out / "a" / f"{shard_id}.json", {"_": [t.items for t in tables], **shard})
    index = defaultdict(list)
    n_res, pids = 0, []
    page_from = datetime.now().year - a.pages_years + 1

    def finish(pid, rows):
        nonlocal n_res
        prof, page = athlete(rows, *tables)
        n_res += len(rows)
        ks = []
        for ci, ei in cadres.match({r["name"] for r in rows if r["name"]}, prof["by"]):
            c, e = cadres.lists[ci], cadres.lists[ci]["athletes"][ei]
            ks.append([c["code"], e[3], e[4]])
            cadres.members[ci].append([pid, prof["n"], prof["by"], prof["c"], e[3], e[4]])
            cadres.matched.add((ci, ei))
        if ks:
            prof["k"] = ks
        lim = cadres.limits_for(rows, prof["s"], disc) if cadres.year and prof["nat"] == "SUI" else []  # cadres are for Swiss athletes
        if lim:
            prof["lim"] = [x[1:] for x in lim]
            cadres.achieved.extend([pid, prof["n"], prof["by"], prof["c"], prof["s"], *x, [k[0] for k in ks]] for x in lim)
        g = club_geo(prof["c"])
        if g:
            prof["g"] = g
        last_season = max(r["season"] for r in rows)
        if last_season >= latest - 1:
            recent = best_by([r for r in rows if r["season"] >= latest - 2 and not r["hand"] and r["src"] < 2], lambda r: (r["d"], r["season"]))
            active.append({"pid": pid, "n": prof["n"], "by": prof["by"], "sex": prof["s"], "c": prof["c"], "club": slug(prof["c"]),
                           "canton": g[0] if g else None, "last": rows[0]["date"],
                           "sb": {k: (r["sortv"], mark_text(r["mark"])) for k, r in recent.items()},
                           "pb": {r[0]: r[1] for r in prof["pb"]},
                           "pod": sum(1 for m in prof["m"] if m[4] == 0 and m[0] == latest)})
        shard[pid] = prof
        entry = [pid, prof["n"], prof["by"], prof["c"], prof["s"]]
        for pref in {t[:2] for t in name_tokens(prof["n"]) if len(t) >= 2}:
            index[pref].append(entry)
        if max(r["season"] for r in rows) >= page_from:  # a plain page for Google only if active recently
            prof["pg"] = 1
            pids.append(pid)
            (out / "p" / f"{pid}.html").write_text(
                profile_page(pid, prof, page, disc, a.title, a.base_url, a.contact, updated,
                             {c["code"]: c["name"] for c in cadres.lists}), encoding="utf-8")

    for row in con.execute(q):
        (pid, d, date, mark, value, wind, place, rnd, meeting, loc, club, name, by, sex, nat, hand, lower, indoor,
         label, season, src) = row
        if pid != cur_pid:
            if cur:
                finish(cur_pid, cur)
            if shard_id is not None and pid[:3] != shard_id:
                write_shard()
                shard, tables[:] = {}, [Dict(), Dict(), Dict()]
            cur, cur_pid, shard_id = [], pid, pid[:3]
        cur.append({"d": d, "date": s(date), "mark": mark, "value": value, "wind": wind, "place": place,
                    "round": s(rnd), "meeting": s(meeting), "loc": s(loc), "club": s(club), "name": s(name),
                    "by": by, "sex": s(sex), "nat": s(nat), "hand": int(hand or 0), "season": int(season), "label": label, "src": src,
                    "sortv": value if lower == 1 else -value,
                    "assisted": int(bool(is_wind_event(label, indoor) and wind is not None and wind > 2.0))})
    if cur:
        finish(cur_pid, cur)
    if shard:
        write_shard()
    for pref, entries in index.items():
        dump(out / "idx" / f"{pref}.json", sorted(entries, key=lambda e: e[1]))
    print(f"profiles written ({time.time() - t0:.0f}s)")

    # Cadres page: members with a public profile (adults), and who reached a Swiss Starters limit.
    cad = {"cadres": [], "limits": None}
    for ci, c in enumerate(cadres.lists):
        adults = [(ei, e) for ei, e in enumerate(c["athletes"]) if int(e[1]) <= max_by]
        missing = [e[0] for ei, e in adults if (ci, ei) not in cadres.matched]
        cad["cadres"].append({k: c.get(k) for k in ("code", "name", "desc", "date", "source", "marker")} | {
            "members": sorted(cadres.members[ci], key=lambda m: (m[4], norm_name(m[1]))),
            "listed": len(c["athletes"]) + c.get("minors_not_stored", 0),
            "minors": len(c["athletes"]) - len(adults) + c.get("minors_not_stored", 0), "missing": len(missing)})
        print(f"{c['name']}: {len(cadres.members[ci])} profiles matched, "
              f"{len(c['athletes']) - len(adults) + c.get('minors_not_stored', 0)} minors not shown"
              + (f", not found: {', '.join(missing)}" if missing else ""))
    if cadres.year:
        L = cadres.data["limits"]
        cad["limits"] = {k: L.get(k) for k in ("name", "year", "for", "source")} | {
            "events": [[e["name"], e["M"], e["W"], int(bool(e["labels"].get("M") or e["labels"].get("W"))),
                        e["labels"].get("M", []), e["labels"].get("W", [])] for e in L["events"]],
            "achieved": sorted(cadres.achieved, key=lambda x: (x[5], x[4], norm_name(x[1])))}
        print(f"{L['name']} {cadres.year}: {len(cadres.achieved)} limits reached")
    dump(out / "cadres.json", cad)

    # Season lists: best legal electronic mark per athlete, top N, equal marks share a rank.
    con.create_function("iswind", 2, lambda l, i: int(is_wind_event(l, i)), deterministic=True)
    lq = """
      WITH b AS (
        SELECT r.*, a.pid, d.d, CASE r.lower_better WHEN 1 THEN r.mark_value ELSE -r.mark_value END AS sortv
        FROM r JOIN ath a ON a.athlete_key = r.athlete_key
               JOIN disc d ON d.indoor = r.indoor AND d.discipline = r.discipline
        WHERE COALESCE(r.hand_timed, 0) = 0 AND NOT (iswind(r.discipline, r.indoor) AND COALESCE(r.wind, 0) > 2.0)),
      one AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY season, d, sex, athlete_key ORDER BY sortv, date) AS rn FROM b),
      ranked AS (SELECT *, ROW_NUMBER() OVER w AS pos, RANK() OVER w AS rk FROM one WHERE rn = 1
                 WINDOW w AS (PARTITION BY season, d, sex ORDER BY sortv))
      SELECT season, d, sex, rk, mark_raw, wind, pid, name, by, club, date, meeting FROM ranked
      WHERE pos <= ? ORDER BY season, sex, d, pos"""
    lists = defaultdict(dict)
    for season, d, sex, rk, mark, wind, pid, name, by, club, date, meeting in con.execute(lq, (a.list_n,)):
        lists[season].setdefault(s(sex), {}).setdefault(str(d), []).append(
            [rk, mark_text(mark), r1(wind), pid, s(name), by, s(club), s(date), s(meeting)])
    years = sorted(lists, reverse=True)
    for y in years:
        dump(out / "lists" / f"{y}.json", lists[y])

    plan = planner(con, disc, out, pcfg, gaz) if pcfg else None
    geo = geography(out, disc, active, gcfg, gaz, club_geo, ref_years) if gcfg else None

    first_year, last_result = con.execute("SELECT MIN(season), MAX(date) FROM r").fetchone()
    dump(out / "meta.json", {
        "title": a.title, "contact": a.contact, "min_age": a.min_age, "list_n": a.list_n, "generated": updated,
        "last_result": s(last_result), "first_year": first_year, "years": years, "athletes": n_ath,
        "results": n_res, "full_results": "allresults" in have, "disc": disc,
        "cadres": {c["code"]: c["name"] for c in cadres.lists}, "limit_name": (cadres.data.get("limits") or {}).get("name"),
        "limit_year": cadres.year, "plan": plan, "geo": geo})

    # Static files, search engines, custom domain.
    for f in (HERE / "web").rglob("*"):
        if f.is_file():
            dest = out / f.relative_to(HERE / "web")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
    page = (out / "index.html").read_text(encoding="utf-8").replace("{{TITLE}}", html.escape(a.title))
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    if a.domain:
        (out / "CNAME").write_text(a.domain.strip() + "\n", encoding="utf-8")
    if a.base_url:
        base = a.base_url.rstrip("/") + "/"
        urls = [f"{base}p/{p}.html" for p in pids]
        maps = []
        for i in range(0, len(urls), 45000):
            name = f"sitemap-{i // 45000 + 1}.xml"
            body = "".join(f"<url><loc>{html.escape(u)}</loc><lastmod>{updated}</lastmod></url>" for u in urls[i:i + 45000])
            (out / name).write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                                    f'<url><loc>{base}</loc><lastmod>{updated}</lastmod></url>{body}</urlset>\n', encoding="utf-8")
            maps.append(name)
        (out / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<sitemap><loc>{base}{m}</loc><lastmod>{updated}</lastmod></sitemap>" for m in maps)
            + "</sitemapindex>\n", encoding="utf-8")
        (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}sitemap.xml\n", encoding="utf-8")

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    files = sum(1 for f in out.rglob("*") if f.is_file())
    print(f"built {out}/: {n_ath:,} athletes, {n_res:,} results, {len(years)} seasons, "
          f"{len(pids):,} plain pages, {files:,} files, {size / 1e6:.0f} MB, {time.time() - t0:.0f}s")
    if size > 900e6:
        print("WARNING: GitHub Pages sites are limited to 1 GB. Raise --min-age, lower --list-n or drop old seasons.")
        return 2
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="athletics.db")
    p.add_argument("--out", default="site")
    p.add_argument("--domain", default="", help="custom domain written to CNAME, e.g. athletes.mcintyre.ch")
    p.add_argument("--base-url", default="", help="full address, e.g. https://athletes.mcintyre.ch/ (for sitemaps and link previews)")
    p.add_argument("--title", default="Athlètes suisses")
    p.add_argument("--contact", default="paullucmcintyre@gmail.com", help="shown in the footer for corrections and removals")
    p.add_argument("--min-age", type=int, default=18, help="leave out athletes younger than this in the current year")
    p.add_argument("--list-n", type=int, default=20, help="athletes per season list")
    p.add_argument("--removals-ids", default="removals.txt", help="public ids to leave out, one per line")
    p.add_argument("--removals", default="removals.csv", help="optional name,birth_year file (keep it out of git)")
    p.add_argument("--pages-years", type=int, default=10,
                   help="write a plain HTML page (for Google and link previews) for athletes with a result in the last N seasons")
    p.add_argument("--salt", default=".site_salt")
    p.add_argument("--cadres", default=str(HERE / "cadres.json"), help="official cadre lists and limits")
    p.add_argument("--geo", default=str(HERE / "geo.json"), help="regions, canton names and club corrections")
    p.add_argument("--planner", default=str(HERE / "planner.json"), help="season planner: championships, standards, departure cities")
    sys.exit(build(p.parse_args()))


if __name__ == "__main__":
    main()
