#!/usr/bin/env python3
"""
Alabus (Swiss Athletics) bestlist scraper -> SQLite -> canton stats / athlete profiles.

Install:  pip install httpx pandas lxml beautifulsoup4

Workflow:
  discover                      learn discipline/category/year values (once)
  fetch --years 2006-2026       best result per athlete and list (resumable, cached)
  fetch --all-results ...       every result (heats, finals), needed for medals and head-to-head
  clubs / mapclubs [file]       list clubs, merge a club,canton,review file into club_canton.csv
  coverage                      rows per discipline/sex/year (coverage.csv)
  report --canton NE --year Y   one canton/year; reports = every canton and year (reports/ folder)
  records --canton NE           compare an official record list (ne_records_baseline.csv) with the database
  romandie --year 2026          best lists of VD, GE, VS, FR, NE, JU together (romandie/)
  radar [--champs U20WC2026]    who in Romandie reached or is close to a qualification limit (radar.csv)
  track --cadre cadre.csv       progress of named athletes towards the limits (track.csv)
  cadre-audit --cadre cadre.csv who is on the cadre list, who might belong, who to review (cadre_audit.xlsx)
  webdata --year 2026           JSON for the web prototype (adults only by default)
  stats --years 2019,2023-2025  season PDFs "Statistique <année>" in the layout of the ANA files (statistiques/)
  update [--years 2026] [--stats]  refresh the current season from Alabus and rebuild records, reports, Excel
  export --canton NE            French Excel workbook for the association (run records + reports first)
  progression "Name" / h2h "A" "B" / medals
Records (ANA rules, 19.10.2026): Swiss athletes count; a foreign athlete counts for a mark set in Switzerland
after 3 years with a club of the canton; record_overrides.csv holds decisions of the association (Meddy Fouquet
validated). Youth records are skipped unless --with-juniors. Alabus shows the CURRENT nationality, so records adds
a risk note and nationality_overrides.csv (name, swiss_from, note) corrects known cases.

What was verified on the live site (one page, indoor, 50 m):
  * Params: lang, mobile, blyear (ALL or a year), blcat (M = Alle Maenner), disci (opaque ID),
    indoor (true/false), top (10/30/100/500/5000).
  * Server-rendered table with 11 columns: Nr, Resultat, Rang, NH*, Name, Verein, Nat.,
    Geb. Dat. (full birth date), Wettkampf, Ort, Datum.
  * Default list type is "one result per athlete". Results are only systematic from 2006.
  * Names/clubs/meetings are JSF links (href="#"), so there are no athlete or meeting IDs in the HTML.
    Athlete identity is therefore name + full birth date.

NOT verified (check schema.json after `discover`):
  * That the <select> elements expose option values in the HTML. If discover finds no disciplines,
    use DevTools to copy the `disci` values by hand into schema.json.
  * Whether the "Alle Resultate" list type can be set by URL. If not, you need Playwright to change
    that dropdown. Without it, medals and head-to-head only see each athlete's best result per year.
Be polite (DELAY), and get permission before reselling the data.
"""
import argparse
import hashlib
import json
import re
import sqlite3
import os
import sys
import time
import unicodedata
import uuid
from urllib.parse import urljoin
from datetime import datetime
from io import StringIO
from pathlib import Path

import httpx
import pandas as pd
from bs4 import BeautifulSoup

BASE = "https://alabus.swiss-athletics.ch/satweb/faces/bestlist.xhtml"
CACHE = Path("cache")
CACHE.mkdir(exist_ok=True)
DB = "athletics.db"
SCHEMA = Path("schema.json")
DELAY = 1.0
SCRIPT_VERSION = "2026-10-27"
REFRESH = False  # True: ignore cached pages and download again (update command)
SEED_DISCI = "5c4o3k5m-d686mo-j986g2ie-1-j986gc6m-1nc"  # 50 m indoor, seen on the live site
COLS = ["nr", "mark_raw", "rank_raw", "nh", "name", "club", "nat", "birth", "meeting", "location", "date", "wind"]
HIGHER_BETTER = ("Hoch", "Stab", "Weit", "Drei", "Kugel", "Diskus", "Speer", "Hammer", "Gewicht",
                 "Fünfkampf", "Siebenkampf", "Zehnkampf", "Athletic-Cup")
# Edit after checking real meeting names in your data.
CHAMPIONSHIP_PATTERNS = ["Schweizer Meisterschaft", "Championnats suisses", "Campionati svizzeri",
                         "Swiss Championships"]

client = httpx.Client(headers={"User-Agent": "McIntyreDataInsights research script"},
                      timeout=30, follow_redirects=True)


# ---------- HTTP with cache ----------
def fetch(params: dict) -> str:
    params = {"lang": "de", "mobile": "false", **params}
    key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()
    path = CACHE / f"{key}.html"
    if path.exists() and not REFRESH:
        return path.read_text(encoding="utf-8")
    for attempt in range(4):
        try:
            r = client.get(BASE, params=params)
            r.raise_for_status()
            path.write_text(r.text, encoding="utf-8")
            time.sleep(DELAY)
            return r.text
        except httpx.HTTPError as e:
            print(f"  retry {attempt + 1}: {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"failed: {params}")


# ---------- "Alle Resultate" via the JSF ajax call ----------
TYPE_ALL = "0"  # value of form_anonym:bestlistType_input for "Alle Resultate" (seen in the browser request)


def fetch_all_list(params: dict) -> str:
    """Same session, three steps (this mirrors what the browser does):
    1) GET the page with the list params, 2) ajax POST that switches 'Typ der Liste' to
    'Alle Resultate' (the server remembers it), 3) GET the page again, like clicking Anzeigen.
    Returns the HTML of step 3 (cached on disk)."""
    key = hashlib.sha1(("all|" + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    path = CACHE / f"{key}.html"
    if path.exists() and not REFRESH:
        return path.read_text(encoding="utf-8")
    full = {"lang": "de", "mobile": "false", **params}
    for attempt in range(4):
        try:
            client.cookies.clear()  # fresh server session per list
            r = client.get(BASE, params=full)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            vs = soup.find("input", {"name": "javax.faces.ViewState"})
            form = soup.find("form", id="form_anonym")
            if vs is None or form is None:
                raise RuntimeError("ViewState or form_anonym not found in page")
            action = urljoin(str(r.url), form.get("action") or BASE)
            m = re.search(r"aeswindowguid[\"']?\s*[:=]\s*[\"']([0-9a-fA-F-]{36})", r.text)
            guid = m.group(1) if m else str(uuid.uuid4())
            data = {
                "javax.faces.partial.ajax": "true",
                "javax.faces.source": "form_anonym:bestlistType",
                "javax.faces.partial.execute": "form_anonym:bestlistType",
                "javax.faces.partial.render": "form_anonym:bestlistSearches globalMsgs",
                "javax.faces.behavior.event": "valueChange",
                "javax.faces.partial.event": "change",
                "aeswindowguid": guid,
                "form_anonym": "form_anonym",
                "form_anonym:bestlistYear_focus": "",
                "form_anonym:bestlistYear_input": params["blyear"],
                "form_anonym:bestlistSeason_focus": "",
                "form_anonym:bestlistSeason_input": params["indoor"],
                "form_anonym:bestlistCategory_focus": "",
                "form_anonym:bestlistCategory_input": params["blcat"],
                "form_anonym:bestlistDiscipline_focus": "",
                "form_anonym:bestlistDiscipline_input": params["disci"],
                "form_anonym:bestlistType_focus": "",
                "form_anonym:bestlistType_input": TYPE_ALL,
                "form_anonym:bestlistTops_focus": "",
                "form_anonym:bestlistTops_input": str(params["top"]),
                "javax.faces.ViewState": vs["value"],
            }
            headers = {"Faces-Request": "partial/ajax", "X-Requested-With": "XMLHttpRequest",
                       "Accept": "application/xml, text/xml, */*; q=0.01",
                       "Origin": "https://alabus.swiss-athletics.ch", "Referer": str(r.url)}
            p = client.post(action, data=data, headers=headers)
            p.raise_for_status()
            if "<partial-response" not in p.text:
                Path(f"debug_post_{attempt}.txt").write_text(p.text[:5000], encoding="utf-8")
                raise RuntimeError(f"unexpected ajax response, see debug_post_{attempt}.txt")
            final = client.get(BASE, params=full, headers={"Referer": str(r.url)})
            final.raise_for_status()
            path.write_text(final.text, encoding="utf-8")
            time.sleep(DELAY)
            return final.text
        except (httpx.HTTPError, RuntimeError) as e:
            print(f"  retry {attempt + 1}: {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"failed: {params}")


# ---------- discovery ----------
def discover():
    schema = {"indoor": {}, "categories": {}, "years": [], "selects": []}
    for indoor in (True, False):
        params = {"blyear": "ALL", "blcat": "M", "indoor": str(indoor).lower(), "top": 100}
        if indoor:
            params["disci"] = SEED_DISCI
        soup = BeautifulSoup(fetch(params), "lxml")
        for sel in soup.find_all("select"):
            opts = [(o.get("value", ""), o.get_text(strip=True)) for o in sel.find_all("option")]
            labels = [l for _, l in opts]
            schema["selects"].append({"indoor": indoor, "name": sel.get("name") or sel.get("id"), "options": opts})
            if "Disziplin wählen" in labels:
                schema["indoor"][str(indoor).lower()] = {v: l for v, l in opts if v and l != "Disziplin wählen"}
            elif "Alle Männer" in labels:
                schema["categories"] = {l: v for v, l in opts if v}
            elif "2024" in labels:
                schema["years"] = [l for l in labels if l.isdigit()]
    SCHEMA.write_text(json.dumps(schema, indent=1, ensure_ascii=False), encoding="utf-8")
    n_in = len(schema["indoor"].get("true", {}))
    n_out = len(schema["indoor"].get("false", {}))
    print(f"indoor disciplines: {n_in}, outdoor disciplines: {n_out}, categories: {len(schema['categories'])}")
    if not n_in or not n_out:
        print("Missing values: inspect schema.json 'selects' or copy the disci IDs from DevTools by hand.")


# ---------- parsing ----------
HEADER_MAP = {"nr": "nr", "resultat": "mark_raw", "rang": "rank_raw", "nh*": "nh", "nh": "nh",
              "name": "name", "verein": "club", "nat.": "nat", "nat": "nat", "geb. dat.": "birth",
              "geb.dat.": "birth", "wettkampf": "meeting", "ort": "location", "datum": "date", "wind": "wind"}


def parse_page(html: str, tag: str = "") -> pd.DataFrame:
    """Read the results table as plain text (pandas would turn '10.90' into 10.9).
    Columns are mapped by header name because the NH* column is not always present."""
    soup = BeautifulSoup(html, "lxml")
    table = None
    for t in soup.find_all("table"):
        heads = [th.get_text(strip=True).lower() for th in t.find_all("th")]
        if "resultat" in heads and t.find("table") is None:
            table = t
            break
    if table is None:
        return pd.DataFrame(columns=COLS)
    names = [HEADER_MAP.get(h) for h in heads]
    rows, bad = [], []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        if len(tds) != len(names):
            if len(tds) > 1:  # a single-cell row is the "no results" placeholder, ignore it
                bad.append(tr.get_text(" ", strip=True)[:120])
            continue
        rec = {n: td.get_text(" ", strip=True) for n, td in zip(names, tds) if n}
        if rec.get("nr", "").isdigit():
            rows.append(rec)
    if bad:
        with open("skipped.log", "a", encoding="utf-8") as f:
            f.write(f"{tag} {len(bad)} rows skipped, header={heads}, first={bad[0]!r}\n")
    return pd.DataFrame(rows).reindex(columns=COLS)


def parse_mark(raw, lower_better):
    """'5.63_SR' -> (5.63, 'SR', False); '1:42.55' -> 102.55; '6.00m' -> hand timed.
    Combined events (heptathlon/decathlon): Alabus writes the total followed by the splits,
    e.g. '5537 13.71 (+2.7) / 1.62 / ...' -> keep the total only."""
    raw = str(raw)
    base, _, flags = raw.partition("_")
    base = base.strip()
    if not lower_better and " / " in base:
        base = base.split()[0]
    hand = bool(lower_better and base.endswith("m"))
    base = re.sub(r"[^\d:.,]", "", base).replace(",", ".")
    try:
        if ":" in base:
            parts = [float(p) for p in base.split(":")]
            value = 0.0
            for p in parts:
                value = value * 60 + p
        elif base.count(".") == 2:
            m, s, h = base.split(".")
            value = int(m) * 60 + int(s) + int(h) / 100
        else:
            value = float(base)
    except ValueError:
        value = None
    return value, flags.strip(), hand


def parse_wind(raw):
    try:
        return float(str(raw).replace(",", ".").replace("+", "").strip())
    except ValueError:
        return None


def parse_rank(raw):
    m = re.match(r"^\s*(\d+)\s*(.*)$", str(raw))
    return (int(m.group(1)), m.group(2).strip()) if m else (None, str(raw).strip())


def iso(d):
    try:
        return datetime.strptime(str(d).strip(), "%d.%m.%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def athlete_key(name, birth_iso):
    n = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"[^a-z ]", "", n).strip()
    return hashlib.sha1(f"{n}|{birth_iso}".encode()).hexdigest()[:12]


def clean(df, label, indoor, sex, year, disci):
    lower = not label.startswith(HIGHER_BETTER)
    rows = []
    for r in df.itertuples(index=False):
        value, flags, hand = parse_mark(r.mark_raw, lower)
        place, rcode = parse_rank(r.rank_raw)
        b = iso(r.birth)
        rows.append((int(indoor), disci, label, sex, str(year), int(r.nr), str(r.mark_raw), value, flags,
                     int(hand), int(lower), place, rcode, str(r.name), str(r.club), str(r.nat), b,
                     str(r.meeting), str(r.location), iso(r.date), athlete_key(r.name, b),
                     parse_wind(r.wind)))
    return rows


SQL_CREATE = """CREATE TABLE IF NOT EXISTS bestlist (
 indoor INT, disci_id TEXT, discipline TEXT, sex TEXT, year TEXT, nr INT,
 mark_raw TEXT, mark_value REAL, flags TEXT, hand_timed INT, lower_better INT,
 place INT, round_code TEXT, name TEXT, club TEXT, nat TEXT, birth_date TEXT,
 meeting TEXT, location TEXT, date TEXT, athlete_key TEXT, wind REAL,
 PRIMARY KEY (indoor, disci_id, sex, year, nr))"""


# ---------- fetch command ----------
def cmd_fetch(a):
    global REFRESH
    REFRESH = bool(getattr(a, "refresh", False))
    if not SCHEMA.exists():
        sys.exit("run `discover` first")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    years = [str(y) for y in parse_years(a.years)]  # accepts 2024, 2019-2026 or 2019,2023-2026
    cats = {"M": schema["categories"].get("Alle Männer", "M"), "W": schema["categories"].get("Alle Frauen", "W")}
    wanted = [w.strip().lower() for w in a.disciplines.split(",")] if a.disciplines else None
    table = "allresults" if a.all_results else "bestlist"
    con = sqlite3.connect(DB)
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    if cols and "wind" not in cols:
        print("old database layout found, rebuilding (pages come from the cache, nothing is re-downloaded)")
        con.execute(f"DROP TABLE {table}")
    con.execute(SQL_CREATE.replace("bestlist", table))
    for indoor_key, discs in schema["indoor"].items():
        indoor = indoor_key == "true"
        for disci, label in discs.items():
            if wanted and label.lower() not in wanted:
                continue
            for sex, blcat in cats.items():
                for year in years:
                    params = {"blyear": year, "blcat": blcat, "disci": disci,
                              "indoor": indoor_key, "top": a.top}
                    tag = f"{label} {sex} {year} indoor={indoor_key}"
                    if a.all_results:
                        df = parse_page(fetch_all_list(params), tag)
                    else:
                        df = parse_page(fetch(params), tag)
                    rows = clean(df, label, indoor, sex, year, disci)
                    con.executemany(f"INSERT OR REPLACE INTO {table} VALUES (" + ",".join("?" * 22) + ")", rows)
                    con.commit()
                    warn = "  CAP REACHED, raise --top or split" if len(rows) >= a.top else ""
                    if a.all_results and len(rows) >= 20 and len({r[-2] for r in rows}) == len(rows):
                        warn += "  WARNING: one row per athlete, the type switch may not have applied"
                    print(f"{'in' if indoor else 'out'} {label} {sex} {year}: {len(rows)}{warn}")


# ---------- analysis commands ----------
def load_results(con):
    """All downloaded results: best-per-athlete lists plus full 'Alle Resultate' lists, deduplicated."""
    frames = []
    for t in ("allresults", "bestlist"):  # allresults first: its rows carry wind, and the first copy of a duplicate is kept
        if con.execute("SELECT name FROM sqlite_master WHERE name=?", (t,)).fetchone():
            frames.append(pd.read_sql(f"SELECT * FROM {t}", con))
    if not frames:
        sys.exit("no data yet, run `fetch` first")
    df = pd.concat(frames, ignore_index=True)
    return df.drop_duplicates(["indoor", "disci_id", "sex", "athlete_key", "mark_raw", "meeting", "date"])


def cmd_clubs(_):
    con = sqlite3.connect(DB)
    clubs = load_results(con).groupby("club").size().rename("n").reset_index().sort_values("n", ascending=False)
    clubs["canton"] = ""
    if Path("club_canton.csv").exists():
        old = pd.read_csv("club_canton.csv")
        clubs = clubs.drop(columns="canton").merge(old[["club", "canton"]], on="club", how="left")
        new = clubs.canton.isna() | (clubs.canton == "")
        print(f"{int((~new).sum())} clubs already mapped, {int(new.sum())} without canton")
    clubs.to_csv("club_canton_template.csv", index=False)
    print(f"{len(clubs)} clubs written to club_canton_template.csv. Fill 'canton' (NE, VD, ...), save as club_canton.csv")


def cmd_mapclubs(a):
    """Merge an additions file (club,canton,review) into club_canton.csv, filling only blank cantons."""
    tpl = Path("club_canton_template.csv")
    base = pd.read_csv(tpl if tpl.exists() else "club_canton.csv").fillna("")
    add = pd.read_csv(a.file).fillna("")
    review = {}
    if Path("club_canton.csv").exists():
        old = pd.read_csv("club_canton.csv").fillna("")
        if "review" in old.columns:
            review.update(dict(zip(old.club, old.review)))
    amap, arev = dict(zip(add.club, add.canton)), dict(zip(add.club, add.review))
    missing = [c for c in add.club if c not in set(base.club)]
    filled = 0
    for i, r in base.iterrows():
        if r.canton == "" and amap.get(r.club):
            base.at[i, "canton"] = amap[r.club]
            filled += 1
        if arev.get(r.club):
            review[r.club] = arev[r.club]
    base["review"] = base.club.map(review).fillna("")
    base[["club", "canton", "review"]].to_csv("club_canton.csv", index=False)
    blank = base[base.canton == ""]
    print(f"filled {filled} clubs, {len(blank)} still without canton, saved club_canton.csv")
    if missing:
        print(f"{len(missing)} names in the additions file were not in the template (not an error if they are new)")
    if "n" in blank.columns and len(blank):
        print("biggest clubs still blank:")
        print(blank.sort_values("n", ascending=False).head(10)[["club", "n", "review"]].to_string(index=False))


def _prepare(con, include_assisted):
    df = load_results(con)
    cc = pd.read_csv("club_canton.csv")[["club", "canton"]].dropna()
    df = df.merge(cc, on="club")
    df = df[df.canton.astype(str).str.len() > 0]
    df = df[df.mark_value.notna()]
    if not include_assisted:
        df = df[df.wind.isna() | (df.wind <= 2.0)]  # wind-assisted marks are not valid for records
    df = df.copy()
    df["sortv"] = df.mark_value.where(df.lower_better == 1, -df.mark_value)
    return df


def _eligible(df):
    """Records only count for Swiss athletes. The Nat. column of Alabus is the athlete's CURRENT nationality,
    not the one at the time of the mark (checked: Roland shows SUI on 2024/25 rows although French then),
    so nationality_overrides.csv (name, swiss_from, note) corrects known changes:
    swiss_from = an ISO date (Swiss only from that day), 'always' or 'never'."""
    keep = df.nat == "SUI"
    f = Path("nationality_overrides.csv")
    if not f.exists():
        f.write_text("name,swiss_from,note\n", encoding="utf-8")  # empty template: swiss_from = date, always or never
    if f.exists():
        nn = df.name.map(_norm_name)  # "Prénom Nom" and "Nom Prénom" both match
        for r in pd.read_csv(f).fillna("").itertuples():
            m = nn == _norm_name(r.name)
            sf = str(r.swiss_from).strip().lower()
            if sf == "always":
                keep = keep | m
            elif sf in ("", "never"):
                keep = keep & ~m
            else:
                keep = (keep & ~m) | (m & (df.date >= sf))
    return df[keep]


# ---------- record eligibility (cantonal rules confirmed by the ANA, X. Niquille, 19.10.2026) ----------
RESIDENCY_YEARS = 3
LOCATIONS = Path("locations.csv")          # location,country  (CH or anything else), for venues the script misreads
RECORD_OVERRIDES = Path("record_overrides.csv")  # name,canton,eligible (yes/no),note  : decisions of the association
SEED_OVERRIDES = [("Meddy Fouquet", "NE", "yes", "Record validé par l'ANA (X. Niquille, 19.10.2026)")]


def _is_swiss_location(loc, known):
    """True if the venue is in Switzerland. Alabus venues abroad usually carry a country code, e.g. 'Paris (FRA)'.
    Venues without a code are assumed Swiss; locations.csv corrects mistakes (they are listed in the check file)."""
    s = str(loc).strip()
    if s in known:
        return known[s]
    m = re.search(r"\(([A-Z]{3})\)\s*$", s)
    return m.group(1) == "SUI" if m else True


def _record_overrides():
    if not RECORD_OVERRIDES.exists():
        RECORD_OVERRIDES.write_text("name,canton,eligible,note\n", encoding="utf-8")
    ov = pd.read_csv(RECORD_OVERRIDES, dtype=str).fillna("")
    for name, canton, elig, note in SEED_OVERRIDES:
        if not ((ov.name.map(_norm_name) == _norm_name(name)) & (ov.canton == canton)).any():
            ov.loc[len(ov)] = [name, canton, elig, note]
    ov.to_csv(RECORD_OVERRIDES, index=False)
    return ov


def _record_eligible(df, canton):
    """Who may hold a record of the canton. df = results of clubs of that canton.
      1. Swiss athletes always count (nationality_overrides.csv handles nationality changes).
      2. A foreign athlete counts if the mark was set IN SWITZERLAND and he/she had been with a club of the
         canton for at least 3 years on the day of the mark. Club history is approximated by the first result
         for a club of the canton in the database (from 2006, so it can only be too short, never too long).
      3. record_overrides.csv forces the decision of the association (yes/no) for a named athlete.
    Adds a column elig_reason: 'suisse', 'règle des 3 ans' or 'validé par l'association'."""
    df = df.copy()
    df["elig_reason"] = ""
    df.loc[_eligible(df).index, "elig_reason"] = "suisse"
    first = df.groupby("athlete_key").date.transform("min")
    df["canton_since"] = first
    years_in = (pd.to_datetime(df.date, errors="coerce") - pd.to_datetime(first, errors="coerce")).dt.days / 365.25
    known = {}
    if LOCATIONS.exists():
        lc = pd.read_csv(LOCATIONS, dtype=str).fillna("")
        known = {l.strip(): c.strip().upper() in ("CH", "SUI", "SUISSE") for l, c in zip(lc.location, lc.country)}
    in_ch = df.location.map(lambda l: _is_swiss_location(l, known))
    rule = (df.elig_reason == "") & in_ch & (years_in >= RESIDENCY_YEARS)
    df.loc[rule, "elig_reason"] = "règle des 3 ans"
    nn = df.name.map(_norm_name)
    for r in _record_overrides().itertuples():
        if r.canton.strip().upper() != canton.upper():
            continue
        m = nn == _norm_name(r.name)
        df.loc[m, "elig_reason"] = "validé par l'association" if r.eligible.strip().lower() in ("yes", "oui", "1") else ""
    return df[df.elig_reason != ""]


def _top(d, n):
    d = d.sort_values("sortv").drop_duplicates(["indoor", "discipline", "sex", "athlete_key"])
    d = d.groupby(["indoor", "discipline", "sex"]).head(n).copy()
    d["rank"] = d.groupby(["indoor", "discipline", "sex"]).cumcount() + 1
    d["birth_year"] = d.birth_date.astype(str).str[:4]
    return d


OUT_COLS = ["canton", "scope", "indoor", "discipline", "sex", "rank", "mark_raw", "wind", "name",
            "nat", "club", "birth_year", "meeting", "date"]


def cmd_report(a):
    con = sqlite3.connect(DB)
    df = _prepare(con, a.include_assisted)
    if a.swiss_only:
        df = _eligible(df)
    df = df[df.canton == a.canton]
    if a.year:
        df = df[df.year == str(a.year)]
    t = _top(df, a.n)
    t["scope"] = a.year or "alltime"
    fn = f"canton_{a.canton}_{a.year or 'alltime'}.csv"
    t[OUT_COLS].to_csv(fn, index=False)
    print(t[OUT_COLS].head(40).to_string(index=False))
    print(f"\nsaved {fn}")


def cmd_reports(a):
    """One CSV per canton and year (plus all-time), and one combined file, in the reports/ folder."""
    con = sqlite3.connect(DB)
    df = _prepare(con, a.include_assisted)
    if a.swiss_only:
        df = _eligible(df)
    out = Path("reports")
    out.mkdir(exist_ok=True)
    combined = []
    for canton, dc in df.groupby("canton"):
        scopes = [("alltime", dc)] + [(y, g) for y, g in dc.groupby("year")]
        for scope, g in scopes:
            t = _top(g, a.n)
            t["scope"] = scope
            t[OUT_COLS].to_csv(out / f"{canton}_{scope}.csv", index=False)
            combined.append(t[OUT_COLS])
        print(f"{canton}: {len(scopes) - 1} seasons")
    pd.concat(combined).to_csv(out / "all_cantons_top.csv", index=False)
    print(f"saved to {out}/ (one file per canton and year, plus all_cantons_top.csv)")


def _nat_check(b, first_seen):
    """Risk note for a record candidate, because Alabus only knows the current nationality.
    A Swiss career normally leaves youth results in the database (which starts in 2006)."""
    try:
        by = int(str(b.birth_date)[:4])
        age = (pd.Timestamp(first_seen[b.athlete_key]) - pd.Timestamp(b.birth_date)).days / 365.25
    except (ValueError, KeyError, TypeError):
        return "Données insuffisantes: vérifier la nationalité à la date de la performance"
    if by >= 1991:
        if age >= 19:
            return (f"A VERIFIER: première apparition dans la base à {age:.0f} ans, carrière suisse non documentée "
                    f"avant; la nationalité à l'époque peut différer de l'actuelle")
        return f"Carrière documentée dès {age:.0f} ans: nationalité probablement suisse à l'époque"
    return (f"Né en {by}: base incomplète avant 2006, vérifier la nationalité à la date "
            f"sur la feuille de résultats officielle")


def _elig_check(b, first_seen):
    if b.elig_reason == "règle des 3 ans":
        return (f"Athlète étranger éligible par la règle des 3 ans: premier résultat pour un club du canton le "
                f"{b.canton_since}, performance à {b.location}. A VERIFIER: appartenance continue au club et lieu en Suisse")
    if b.elig_reason == "validé par l'association":
        return "Validé par l'association (record_overrides.csv)"
    return _nat_check(b, first_seen)


def cmd_records(a):
    """Compare an official canton record list (baseline CSV) with the database.
    Eligibility follows the cantonal rules (see _record_eligible); --all-nationalities switches it off.
    Statuses: BEATEN (an eligible athlete has a better mark), confirmed_in_db (same mark in the database,
    so the record was set since 2006), standing, no_db_rows, not_in_alabus.
    Youth records (U16/U18/U20) are skipped by default: the ANA already publishes them. --with-juniors adds them."""
    files = [f.strip() for f in str(a.baseline).split(",") if f.strip()]
    if getattr(a, "with_juniors", False) and a.baseline == "ne_records_baseline.csv" \
            and Path("ne_records_baseline_juniors.csv").exists():
        files.append("ne_records_baseline_juniors.csv")
    base = pd.concat([pd.read_csv(f) for f in files], ignore_index=True).fillna("")
    if "cat" not in base:
        base["cat"] = ""
    if not getattr(a, "with_juniors", False):
        n0 = len(base)
        base = base[~base["cat"].astype(str).str.upper().isin(["U16", "U18", "U20"])]
        if len(base) < n0:
            print(f"{n0 - len(base)} youth record rows skipped (use --with-juniors to keep them)")
    AGE_MAX = {"U16": 15, "U18": 17, "U20": 19}  # age = year of performance - year of birth; lists are cumulative
    con = sqlite3.connect(DB)
    full = _prepare(con, include_assisted=False)
    first_seen = full.groupby("athlete_key").date.min()
    allf = full[full.canton == a.canton]
    if a.all_nationalities:
        el = allf.assign(elig_reason="toutes nationalités", canton_since="")
    else:
        el = _record_eligible(allf, a.canton)
        r3 = el[el.elig_reason == "règle des 3 ans"].sort_values("sortv").drop_duplicates("athlete_key")
        if len(r3):
            fn3 = f"{a.canton.lower()}_regle_3_ans_a_verifier.csv"
            r3[["name", "nat", "club", "canton_since", "discipline", "mark_raw", "date", "location", "meeting"]].to_csv(fn3, index=False)
            print(f"{len(r3)} foreign athletes eligible through the 3-year rule, check venues and club history in {fn3}")
    rows = []
    for r in base.to_dict("records"):
        status, b, top3, note = "not_in_alabus", None, "", ""
        if r["db_discipline"]:
            def pick(d):
                m = d[(d.indoor == r["indoor"]) & (d.sex == r["sex"]) & (d.discipline == r["db_discipline"])
                      & (d.hand_timed == r["hand"])]
                if r["cat"] in AGE_MAX:  # junior list: only marks by athletes of that age or younger
                    age = m.date.astype(str).str[:4].astype(int) - pd.to_numeric(m.birth_date.astype(str).str[:4], errors="coerce")
                    m = m[age <= AGE_MAX[r["cat"]]]
                return m
            c = pick(el)
            if c.empty:
                status = "no_db_rows"
            else:
                cs = c.sort_values("sortv").drop_duplicates("athlete_key").head(3)
                top3 = " | ".join(f"{x.mark_raw} {x.name} ({x.club}, {x.date})" for x in cs.itertuples())
                b = cs.iloc[0]
                lower = bool(b.lower_better)
                gain = (r["mark_value"] - b.mark_value) if lower else (b.mark_value - r["mark_value"])
                status = "BEATEN" if gain > 1e-6 else "confirmed_in_db" if abs(gain) <= 1e-6 else "standing"
            if not a.all_nationalities:
                ca = pick(allf)
                if not ca.empty:
                    ba = ca.sort_values("sortv").iloc[0]
                    ga = (r["mark_value"] - ba.mark_value) if ba.lower_better else (ba.mark_value - r["mark_value"])
                    if ga > 1e-6 and (b is None or ba.sortv < b.sortv - 1e-9):
                        note = (f"Meilleure marque par un athlète non éligible (étranger hors règle des 3 ans "
                                f"ou performance hors de Suisse): {ba.mark_raw} {ba['name']} ({ba.nat}, {ba.date}, {ba.location})")
        beaten = status == "BEATEN"
        rows.append({
            "scope": r["scope"], "cat": r["cat"], "sex": r["sex"], "event": r["event_fr"], "alabus_discipline": r["db_discipline"],
            "record_mark": r["mark_raw"], "record_holder": r["holder"], "record_club": r["club"],
            "record_date": r["date"], "status": status,
            "db_best_mark": b.mark_raw if b is not None else "", "db_best_name": b["name"] if b is not None else "",
            "db_best_club": b.club if b is not None else "", "db_best_date": b.date if b is not None else "",
            "db_best_meeting": b.meeting if b is not None else "", "db_best_wind": b.wind if b is not None else "",
            "db_best_birth": b.birth_date if b is not None else "", "db_best_nat": b.nat if b is not None else "",
            "db_top3_ne": top3, "note_ineligible": note,
            "eligibility": b.elig_reason if b is not None else "",
            "nationality_check": _elig_check(b, first_seen) if (b is not None and beaten) else "",
            "current_mark": b.mark_raw if beaten else r["mark_raw"],
            "current_holder": b["name"] if beaten else r["holder"],
            "current_date": b.date if beaten else r["date"],
        })
    out = pd.DataFrame(rows)
    out.to_csv(a.out, index=False)
    print(out.status.value_counts().to_string())
    sub = out[out.status == "BEATEN"]
    if len(sub):
        print("\nBEATEN:")
        for x in sub.itertuples():
            print(f"[{x.scope} {x.sex} {x.cat or 'actifs'}] {x.event}: record {x.record_mark} {x.record_holder} ({x.record_date})")
            print(f"    -> {x.db_best_mark} {x.db_best_name}, {x.db_best_club}, {x.db_best_date}, "
                  f"{x.db_best_meeting}, wind={x.db_best_wind}, born {x.db_best_birth}, nat={x.db_best_nat}")
            print(f"    top 3 NE athletes: {x.db_top3_ne}")
            print(f"    eligibility: {x.eligibility} | {x.nationality_check}")
    nn = out[out.note_ineligible != ""]
    if len(nn):
        print("\nBetter marks that do NOT count (foreign, outside the 3-year rule or set abroad):")
        for x in nn.itertuples():
            print(f"[{x.scope} {x.sex} {x.cat or 'actifs'}] {x.event}: record {x.record_mark} -> {x.note_ineligible}")
    print(f"\nsaved {a.out}")


def cmd_export(a):
    """Build an Excel workbook (in French) from ne_records_updated.csv and reports/all_cantons_top.csv.
    Run `records` and `reports` first. Needs: pip install openpyxl"""
    from openpyxl.styles import Alignment, Font, PatternFill
    rec = pd.read_csv(a.records).fillna("")
    top = pd.read_csv("reports/all_cantons_top.csv")
    top = top[top.canton == a.canton].fillna("")
    cat = {"outdoor": "Plein air", "indoor": "En salle"}
    sex = {"M": "Hommes", "W": "Dames"}
    status = {"BEATEN": "A confirmer (record battu ?)", "confirmed_in_db": "Confirmé par la base de données",
              "standing": "Inchangé", "no_db_rows": "Inchangé (aucune performance éligible depuis 2006)",
              "not_in_alabus": "Non vérifiable (discipline absente de la base)"}

    def fd(x):  # one date format everywhere: dd.mm.yyyy (years only stay as they are)
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(x))
        return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else str(x)

    def official_name(x):  # official lists write "Nom Prénom" (first name last); Alabus writes "Prénom Nom"
        t = str(x).split()
        return " ".join([t[-1]] + t[:-1]) if len(t) > 1 else str(x)

    dd = rec.status == "BEATEN"
    cand = (rec.db_best_mark.astype(str) + " " + rec.db_best_name.astype(str) + " (" +
            rec.db_best_date.map(fd) + ")").where(dd, "")
    r1 = pd.DataFrame({
        "Catégorie": rec.scope.map(cat), "Catégorie d'âge": rec["cat"].replace("", "Actifs") if "cat" in rec else "Actifs",
        "Sexe": rec.sex.map(sex), "Discipline": rec.event,
        "Record officiel": rec.record_mark, "Détenteur officiel": rec.record_holder.map(official_name),
        "Club": rec.record_club, "Date": rec.record_date.map(fd), "Statut": rec.status.map(status),
        "Performance candidate": cand,
        "Remarque": rec["note_ineligible"] if "note_ineligible" in rec else ""})
    b = rec[dd]
    r2 = pd.DataFrame({
        "Catégorie": b.scope.map(cat), "Catégorie d'âge": b["cat"].replace("", "Actifs") if "cat" in b else "Actifs",
        "Sexe": b.sex.map(sex), "Discipline": b.event,
        "Record officiel": b.record_mark, "Détenteur officiel": b.record_holder.map(official_name),
        "Date du record": b.record_date.map(fd),
        "Performance candidate": b.db_best_mark, "Athlète": b.db_best_name, "Club": b.db_best_club,
        "Date": b.db_best_date.map(fd), "Compétition": b.db_best_meeting, "Vent (m/s)": b.db_best_wind,
        "Nationalité (actuelle)": b.db_best_nat, "Année de naissance": b.db_best_birth.astype(str).str[:4],
        "Contrôle nationalité": b["nationality_check"] if "nationality_check" in b else ""})
    multi = top.discipline.astype(str).str.contains("kampf|Athletic-Cup", case=False, regex=True)

    def season_table(t):
        t = t.copy()
        t["_s"] = t.scope.astype(str)
        t["_o"] = t._s.map(lambda v: 9999 if v == "alltime" else int(v) if v.isdigit() else 0)
        t = t.sort_values(["_o", "indoor", "sex", "discipline", "rank"], ascending=[False, True, True, True, True])
        return pd.DataFrame({
            "Saison": t._s.replace({"alltime": "Tous temps"}), "Catégorie": t.indoor.map({0: "Plein air", 1: "En salle"}),
            "Sexe": t.sex.map(sex), "Discipline": t.discipline, "Rang": t["rank"], "Performance": t.mark_raw,
            "Vent (m/s)": t.wind, "Athlète": t["name"], "Nationalité (actuelle)": t["nat"] if "nat" in t else "",
            "Club": t.club, "Date": t.date.map(fd), "Compétition": t.meeting})
    r3, r4 = season_table(top[~multi]), season_table(top[multi])

    lastdate = ""
    try:
        con = sqlite3.connect(DB)
        lastdate = max(x[0] or "" for x in [con.execute(f"SELECT MAX(date) FROM {t}").fetchone()
                                            for t in ("bestlist", "allresults")
                                            if con.execute("SELECT name FROM sqlite_master WHERE name=?", (t,)).fetchone()])
    except Exception:
        pass
    now = datetime.now()
    notes = pd.DataFrame({"Notes": [
        f"Statistiques du canton {a.canton}, générées à partir des résultats publics de Swiss Athletics (base Alabus).",
        f"Généré le {now:%d.%m.%Y %H:%M}; dernière performance dans la base: {fd(lastdate)}; version du script: {SCRIPT_VERSION}.",
        "Fichiers utilisés: club_canton.csv (attribution des clubs aux cantons), nationality_overrides.csv, " + a.records + ".",
        "Les performances de la base de données couvrent la période dès 2006 (2006 est incomplète).",
        "Les records antérieurs proviennent des listes officielles de l'association cantonale; les noms sont écrits Prénom Nom.",
        "'Performance candidate' est une performance trouvée dans la base de données qui bat la liste officielle; le record officiel reste inchangé jusqu'à homologation.",
        "Chaque cas doit être vérifié (résultats officiels, nationalité, conditions de vent) avant d'être homologué.",
        "Les performances avec vent supérieur à 2.0 m/s sont exclues des records.",
        "La nationalité indiquée est l'actuelle; elle peut différer de celle au moment de la performance (colonne Contrôle nationalité).",
        "Règles de l'association pour les records: les athlètes suisses comptent toujours; un athlète étranger compte si la performance est réalisée en Suisse après au moins 3 ans dans un club du canton (estimé par son premier résultat pour un club du canton dans la base). La colonne Remarque signale les meilleures marques d'athlètes non éligibles.",
        "Les records jeunesse (U16, U18, U20) ne sont pas repris ici: ils sont publiés sur le site de l'association.",
        "Deux records à égalité (ex aequo) apparaissent sur deux lignes, une par athlète.",
        "Les concours multiples (points) sont dans une feuille séparée; leurs totaux ne sont pas des distances ou des temps.",
        "Un athlète est identifié par son nom et sa date de naissance; une graphie différente du nom peut créer deux profils.",
        "Les relais, le marathon, l'heure, les 20000 m et le pentathlon en salle dames ne sont pas vérifiés.",
        "Le club d'un athlète est celui indiqué lors de la compétition."]})

    fn = f"{a.canton}_statistiques_{now:%Y-%m-%d}.xlsx"
    with pd.ExcelWriter(fn, engine="openpyxl") as xw:
        for name, d in (("Records", r1), ("A vérifier", r2), ("Top par saison", r3),
                        ("Concours multiples", r4), ("Notes", notes)):
            d.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", fgColor="1F4E78")
                c.alignment = Alignment(vertical="center")
            for col in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
                ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 70)
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
        ws = xw.sheets["Records"]
        for row in ws.iter_rows(min_row=2):
            if str(row[7].value).startswith("A confirmer"):
                for c in row:
                    c.fill = PatternFill("solid", fgColor="FFF2CC")
    print(f"saved {fn}: {len(r1)} records, {len(r2)} to verify, {len(r3)} season rows, {len(r4)} multi-event rows")


# ---------- season statistics (PDF in the layout of the ANA 'Statistique' files) ----------
def parse_years(spec):
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if "-" in part:
            x, y = part.split("-")
            out += list(range(int(x), int(y) + 1))
        elif part:
            out.append(int(part))
    return sorted(set(out))


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
    simple = {"Hoch": "Hauteur", "Stab": "Perche", "Weit": "Longueur", "Drei": "Triple", "1 Meile": "1 mile"}
    if l in simple:
        return simple[l]
    for g, f in (("Siebenkampf", "Heptathlon"), ("Zehnkampf", "Décathlon"), ("Fünfkampf", "Pentathlon")):
        if l.startswith(g):
            return f + l[len(g):]
    return l.replace("Steeple", "steeple")


def discipline_order(label):
    l = str(label)
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
    return (not indoor) and (label in ("100 m", "200 m", "Weit", "Drei") or bool(re.match(r"^(80|100|110) mH", label)))


def build_season(df, year):
    """Return {(indoor, sex): [(label, legal_rows, assisted_rows), ...]} for one season.
    Best legal mark per athlete; a wind-assisted mark is listed separately when it beats the athlete's legal best."""
    d = df[df.date.astype(str).str[:4] == str(year)].copy()
    out = {}
    for (ind, sex, label), g in d.groupby(["indoor", "sex", "discipline"]):
        g = g.sort_values("sortv")
        wind_ev = is_wind_event(label, ind)
        assisted_mask = (g.wind > 2.0) if wind_ev else pd.Series(False, index=g.index)
        legal = g[~assisted_mask].drop_duplicates("athlete_key")
        ass = g[assisted_mask].drop_duplicates("athlete_key")
        lb = dict(zip(legal.athlete_key, legal.sortv))
        ass = ass[[(k not in lb) or (v < lb[k] - 1e-9) for k, v in zip(ass.athlete_key, ass.sortv)]]
        if not legal.empty or not ass.empty:
            out.setdefault((ind, sex), []).append((label, legal, ass))
    for k in out:
        out[k].sort(key=lambda t: discipline_order(t[0]))
    return out


def cmd_stats(a):
    """PDF 'Statistique <année>' per season: every athlete of the canton, best mark per discipline."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    con = sqlite3.connect(DB)
    df = _prepare(con, include_assisted=True)
    df = df[df.canton == a.canton].copy()
    prefix = a.org or ("ANA" if a.canton == "NE" else a.canton)
    org_name = "Association Neuchâteloise d'Athlétisme" if a.canton == "NE" else f"Canton {a.canton}"
    out = Path("statistiques")
    out.mkdir(exist_ok=True)
    today = datetime.now()
    base = ParagraphStyle("b", fontName="Helvetica", fontSize=8, leading=10)
    cellp = ParagraphStyle("c", fontName="Helvetica", fontSize=7.5, leading=8.8)
    from xml.sax.saxutils import escape
    h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=15, leading=18, spaceBefore=6, spaceAfter=4,
                        textColor=colors.HexColor("#1F4E78"), keepWithNext=1)
    h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=9.5, leading=12, spaceBefore=7, spaceAfter=2, keepWithNext=1)
    title = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=colors.HexColor("#1F4E78"))
    widths = [40, 28, 28, 120, 28 if not a.full_birthdate else 46, 24, 100, 80, 38]  # points; portrait A4 (width 595, margins 26)
    from reportlab.pdfbase.pdfmetrics import stringWidth

    def fit(txt, width, font="Helvetica", size=7.5):
        """Shorten text with an ellipsis so it never runs into the next column."""
        txt = str(txt)
        avail = width - 7
        if stringWidth(txt, font, size) <= avail:
            return txt
        while txt and stringWidth(txt + "...", font, size) > avail:
            txt = txt[:-1]
        return txt.rstrip() + "..."
    for year in parse_years(a.years):
        season = build_season(df, year)
        if not season:
            print(f"{year}: no data for canton {a.canton}")
            continue
        partial = year >= today.year
        fn = out / f"Statistique_{prefix}_{year}.pdf"

        def footer(canvas, doc):
            canvas.saveState()
            canvas.setFont("Helvetica", 7)
            canvas.drawString(26, 7 * mm, f"Statistique {year} - {org_name} - source: Swiss Athletics (Alabus), état au {today:%d.%m.%Y}")
            canvas.drawRightString(A4[0] - 26, 7 * mm, f"Page {doc.page}")
            canvas.restoreState()

        doc = SimpleDocTemplate(str(fn), pagesize=A4, leftMargin=26, rightMargin=26,
                                topMargin=12 * mm, bottomMargin=14 * mm, title=f"Statistique {year}",
                                author="McIntyre Data & Insights")
        story = [Paragraph(f"Statistique {year}" + (" (saison en cours)" if partial else ""), title),
                 Paragraph(org_name, base), Paragraph(f"État au {today:%d.%m.%Y}", base),
                 Paragraph("Compilée par McIntyre Data &amp; Insights, offerte à l'ANA", base),
                 Paragraph("Source: résultats publics de Swiss Athletics (base Alabus). Meilleure performance de chaque athlète "
                           "des clubs du canton par discipline"
                           + (", année de naissance seulement" if not a.full_birthdate else "")
                           + ". La nationalité est celle enregistrée actuellement. Résultats dès 2006 (base de données).", base),
                 Spacer(1, 4)]
        sections = [((1, "M"), "Hommes indoor"), ((1, "W"), "Femmes indoor"),
                    ((0, "M"), "Hommes outdoor"), ((0, "W"), "Femmes outdoor")]
        for key, head in sections:
            if key not in season:
                continue
            story.append(Paragraph(head, h1))
            for label, legal, ass in season[key]:
                story.append(Paragraph(fr_label(label), h2))

                relay = discipline_order(label)[0] == 5

                def rows_for(t):
                    rows = []
                    for r in t.itertuples():
                        w = "" if pd.isna(r.wind) else f"{r.wind:+.1f}"
                        pl = (str(int(r.place)) if pd.notna(r.place) else "") + (str(r.round_code) if r.round_code else "")
                        bd = str(r.birth_date)
                        bd = bd[:4] if not a.full_birthdate else (f"{bd[8:10]}.{bd[5:7]}.{bd[:4]}" if len(bd) >= 10 else bd)
                        dt = str(r.date)
                        dt = f"{dt[8:10]}.{dt[5:7]}.{dt[2:4]}" if len(dt) >= 10 else dt
                        blank = lambda v: "" if str(v).strip().lower() in ("nan", "none", "nat", "") else str(v)
                        mark_txt, team_txt = str(r.mark_raw).replace("_", " "), ""
                        if relay:  # relay rows carry the runners after the time in the mark cell: "43.42 Hulmann V., ..."
                            parts = mark_txt.split(None, 1)
                            mark_txt, team_txt = parts[0], (parts[1] if len(parts) > 1 else "")
                        cells = [mark_txt, w, pl, blank(r.name), blank(bd), blank(r.nat),
                                 blank(r.club), blank(r.location), dt]
                        # long text (relay teams, club and venue names) wraps onto several lines instead of being cut
                        row = [Paragraph(escape(c), cellp) if i in (3, 6, 7) else fit(c, widths[i], "Helvetica-Bold" if i == 0 else "Helvetica")
                               for i, c in enumerate(cells)]
                        if team_txt:
                            row[3] = Paragraph(escape(cells[3]) + "<br/>" + escape(team_txt), cellp)
                        rows.append(row)
                    return rows

                data = rows_for(legal)
                if len(ass):
                    data.append(["Avec vent favorable:", "", "", "", "", "", "", "", ""])
                    data += rows_for(ass)
                t = Table(data, colWidths=widths, repeatRows=0)
                st = [("FONT", (0, 0), (-1, -1), "Helvetica", 7.5), ("TOPPADDING", (0, 0), (-1, -1), 1),
                      ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                      ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (0, 0), (0, -1), "RIGHT"), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                      ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD")),
                      ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 7.5)]
                st = [x for x in st]
                for i, row in enumerate(data):
                    if row[0].startswith("Avec vent"):
                        st.append(("SPAN", (0, i), (-1, i)))
                        st.append(("ALIGN", (0, i), (-1, i), "LEFT"))
                        st.append(("FONT", (0, i), (-1, i), "Helvetica-Oblique", 7.5))
                t.setStyle(TableStyle(st))
                story.append(t)
        story += [Spacer(1, 8), Paragraph("Les marques suivies de m sont chronométrées à la main. Place: rang et série (f finale, h série, r course). "
                                          "Une marque avec vent supérieur à 2.0 m/s est listée à part sous « Avec vent favorable ».", base)]
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        n = sum(len(l) for v in season.values() for _, l, _ in v)
        print(f"saved {fn}: {n} entries")


def cmd_update(a):
    """Refresh the current season from Alabus and rebuild everything (records, reports, Excel, optional PDFs)."""
    years = a.years or str(datetime.now().year)
    if not a.skip_fetch:
        cmd_fetch(argparse.Namespace(years=years, top=5000, disciplines=a.disciplines, all_results=False, refresh=True))
        if a.all_results:
            cmd_fetch(argparse.Namespace(years=years, top=5000, disciplines=a.disciplines, all_results=True, refresh=True))
    if Path(a.baseline).exists():
        cmd_records(argparse.Namespace(canton=a.canton, baseline=a.baseline, out="ne_records_updated.csv",
                                       all_nationalities=False, with_juniors=False))
    cmd_reports(argparse.Namespace(n=10, include_assisted=False, swiss_only=False))
    if Path("ne_records_updated.csv").exists():
        cmd_export(argparse.Namespace(canton=a.canton, records="ne_records_updated.csv"))
    if a.stats:
        cmd_stats(argparse.Namespace(canton=a.canton, years=years, org=None, full_birthdate=False))


# ---------- Romandie view, qualification-limit tracking, data export for the web prototype ----------
ROMANDIE = ["VD", "GE", "VS", "FR", "NE", "JU"]
WIND_EVENTS = re.compile(r"^(100 m|200 m|Weit|Drei|\d+ mH)")


def _limits(path, champs=None):
    lim = pd.read_csv(path, dtype={"birth_min": str, "birth_max": str}).fillna("")
    if champs:
        keep = [c.strip().lower() for c in champs.split(",")]
        lim = lim[lim.champ_id.str.lower().isin(keep)]
    if lim.empty:
        sys.exit("no limits found (check --champs and the limits file)")
    return lim


def _norm_name(x):
    n = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower()
    return " ".join(sorted(re.sub(r"[^a-z ]", " ", n).split()))


def _progress(df, lim, athletes=None, cantons=None, within=3.0):
    """For every limit row, the best valid mark of each eligible athlete inside the qualification window.
    Valid = outdoor, electronic timing, no wind above +2.0 m/s. Returns one row per athlete and limit."""
    out = []
    base = df[(df.indoor == 0) & (df.hand_timed == 0) & df.mark_value.notna()].copy()
    base = base[~(base.discipline.str.match(WIND_EVENTS) & (base.wind > 2.0))]
    if cantons:
        base = base[base.canton.isin(cantons)]
    base["by"] = pd.to_numeric(base.birth_date.astype(str).str[:4], errors="coerce")
    base["nn"] = base.name.map(_norm_name)
    for L in lim.itertuples():
        d = base[(base.sex == L.sex) & (base.discipline == L.db_discipline)
                 & (base.date >= L.period_start) & (base.date <= L.period_end)]
        if L.birth_min != "":
            d = d[(d.by >= int(L.birth_min)) & (d.by <= int(L.birth_max))]
        if athletes is not None:
            d = d[[(n, int(b)) in athletes for n, b in zip(d.nn, d.by.fillna(0))]]
        if d.empty:
            continue
        d = d.sort_values("sortv").drop_duplicates("athlete_key")
        for r in d.itertuples():
            lowb = bool(L.lower_better)
            gap = (r.mark_value - L.limit_value) if lowb else (L.limit_value - r.mark_value)  # > 0 means still short
            pct = 100.0 * gap / L.limit_value
            status = "atteinte" if gap <= 1e-9 else "proche" if pct <= within else "loin"
            if athletes is None and status == "loin":
                continue
            out.append({"champ_id": L.champ_id, "champ": L.champ_name, "sex": L.sex, "event": L.event_fr,
                        "athlete": r.name, "birth_year": int(r.by) if pd.notna(r.by) else "", "club": r.club,
                        "canton": getattr(r, "canton", ""), "best": r.mark_raw, "best_value": r.mark_value,
                        "best_date": r.date, "limit": L.limit_raw, "gap": round(gap, 2), "gap_pct": round(pct, 2),
                        "status": status, "verify_limit": L.verify,
                        "window": f"{L.period_start} .. {L.period_end}"})
    return pd.DataFrame(out)


def _romandie_df(con):
    df = _prepare(con, include_assisted=False)
    return df[df.canton.isin(ROMANDIE)]


def cmd_romandie(a):
    """Best lists for the six Romandie cantons together (Excel + CSV)."""
    con = sqlite3.connect(DB)
    df = _romandie_df(con)
    if not a.all_nationalities:
        df = _eligible(df)
    d = df[df.date.astype(str).str[:4] == str(a.year)]
    t = _top(d, a.n)
    t["canton_club"] = t.canton + " / " + t.club
    cols = ["indoor", "sex", "discipline", "rank", "mark_raw", "wind", "name", "birth_year", "canton", "club", "date", "meeting"]
    t = t.sort_values(["indoor", "sex", "discipline", "rank"])[cols]
    if a.min_age:
        t = t[(int(a.year) - pd.to_numeric(t.birth_year, errors="coerce")) >= a.min_age]
    Path("romandie").mkdir(exist_ok=True)
    t.to_csv(f"romandie/romandie_{a.year}.csv", index=False)
    try:
        with pd.ExcelWriter(f"romandie/romandie_{a.year}.xlsx") as xw:
            t[t.indoor == 0].to_excel(xw, sheet_name="Plein air", index=False)
            t[t.indoor == 1].to_excel(xw, sheet_name="En salle", index=False)
    except Exception as e:  # openpyxl missing
        print("Excel not written:", e)
    print(f"Romandie {a.year}: {len(t)} rows ({', '.join(ROMANDIE)}) -> romandie/romandie_{a.year}.csv/.xlsx")


def cmd_track(a):
    """Progress of named athletes (cadre.csv: name,birth_year) towards the published qualification limits."""
    con = sqlite3.connect(DB)
    df = _prepare(con, include_assisted=False)
    lim = _limits(a.limits, a.champs)
    cadre = pd.read_csv(a.cadre, dtype=str).fillna("")
    ath = {(_norm_name(r.name), int(r.birth_year)) for r in cadre.itertuples()}
    res = _progress(df, lim, athletes=ath, within=a.within)
    if res.empty:
        print("no matching results (check names, 'First Last' order does not matter, and birth_year)")
        return
    res = res.sort_values(["athlete", "champ_id", "event"])
    res.to_csv(a.out, index=False)
    print(res.groupby("status").size().to_string())
    print(f"saved {a.out}")
    for r in res[res.status != "loin"].itertuples():
        print(f"{r.athlete} ({r.birth_year}) {r.event}: {r.best} vs limite {r.limit} [{r.champ_id}] -> {r.status}")


def cmd_radar(a):
    """Who in the chosen cantons has reached or is close to a qualification limit (no names needed in advance)."""
    con = sqlite3.connect(DB)
    df = _prepare(con, include_assisted=False)
    if not a.all_nationalities:
        df = _eligible(df)
    lim = _limits(a.limits, a.champs)
    cantons = [c.strip().upper() for c in a.cantons.split(",")]
    res = _progress(df, lim, cantons=cantons, within=a.within)
    if res.empty:
        print("nobody within the margin")
        return
    res = res.sort_values(["champ_id", "sex", "event", "gap_pct"])
    res.to_csv(a.out, index=False)
    print(res.groupby(["champ_id", "status"]).size().to_string())
    print(f"saved {a.out}")


def cmd_webdata(a):
    """JSON file for the web prototype (load it in the page with the 'Charger mes données' button).
    By default only athletes aged min_age or more in the season are named (no minors in a public demo)."""
    con = sqlite3.connect(DB)
    df = _romandie_df(con)
    df = _eligible(df)
    year = str(a.year)
    d = df[df.date.astype(str).str[:4] == year]
    t = _top(d, a.n)
    t["age"] = int(year) - pd.to_numeric(t.birth_year, errors="coerce")
    t = t[t.age >= a.min_age]
    lists = [{"indoor": int(r.indoor), "sex": r.sex, "discipline": r.discipline, "rank": int(r.rank), "mark": str(r.mark_raw),
              "name": r.name, "birth_year": r.birth_year, "canton": r.canton, "club": r.club, "date": r.date}
             for r in t.sort_values(["indoor", "sex", "discipline", "rank"]).itertuples()]
    radar = []
    if a.limits and Path(a.limits).exists():
        res = _progress(_eligible(_prepare(con, include_assisted=False)).pipe(lambda x: x[x.canton.isin(ROMANDIE)]),
                        _limits(a.limits), cantons=ROMANDIE, within=a.within)
        if len(res):
            res["age"] = int(year) - pd.to_numeric(res.birth_year, errors="coerce")
            res = res[res.age >= a.min_age]
            radar = res.drop(columns=["age"]).to_dict("records")
    records = []
    if Path("ne_records_updated.csv").exists():
        rr = pd.read_csv("ne_records_updated.csv").fillna("")
        if "cat" not in rr:
            rr["cat"] = ""
        records = rr[["scope", "cat", "sex", "event", "record_mark", "record_holder", "record_date", "status",
                      "current_mark", "current_holder", "current_date"]].to_dict("records")
    payload = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "year": year, "min_age": a.min_age,
               "source": "Swiss Athletics (Alabus), résultats publics", "lists": lists, "radar": radar, "records_ne": records}
    Path(a.out).write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"saved {a.out}: {len(lists)} list rows, {len(radar)} radar rows, {len(records)} records")


# ---------- cadre audit: who is in the cadre, who might belong, who might be reviewed ----------
LEVELS = {"": 0, "local": 1, "regional": 2, "national": 3}


def _season_ranks(df, year, age_min, age_max, min_cohort):
    """Best valid mark of every athlete in one season, ranked inside their birth-year cohort
    (same sex, same discipline, same year of birth, all of Switzerland, electronic timing, legal wind)."""
    d = df[(df.hand_timed == 0) & df.mark_value.notna()].copy()
    d = d[~(d.discipline.str.match(WIND_EVENTS) & (d.indoor == 0) & (d.wind > 2.0))]
    d = d[d.date.astype(str).str[:4] == str(year)]
    d["by"] = pd.to_numeric(d.birth_date.astype(str).str[:4], errors="coerce")
    d = d[d.by.notna()]
    d["age"] = int(year) - d.by
    d = d[(d.age >= age_min) & (d.age <= age_max)]
    d = d.sort_values("sortv").drop_duplicates(["indoor", "sex", "discipline", "athlete_key"])
    g = d.groupby(["indoor", "sex", "discipline", "by"])
    d["n"] = g.athlete_key.transform("size")
    d["rank"] = g.cumcount() + 1
    d["pct"] = d["rank"] / d["n"]
    return d[d.n >= min_cohort]


def _level_from_rank(rank, a):
    return "national" if rank <= a.national else "regional" if rank <= a.regional else "local" if rank <= a.local else ""


def cmd_cadre_audit(a):
    """Compare the current cadre list with what the public results say.
    Output: current cadre with evidence, athletes who may belong but are not listed, cadre members to review.
    This is a decision aid built from results only. Coach potential, injuries and the relative-age bonus are
    not visible here, so every line is 'to examine', never 'to remove'."""
    con = sqlite3.connect(DB)
    frames = [pd.read_sql(f"SELECT * FROM {t}", con) for t in ("allresults", "bestlist")
              if con.execute("SELECT name FROM sqlite_master WHERE name=?", (t,)).fetchone()]
    df = pd.concat(frames, ignore_index=True).drop_duplicates(["indoor", "disci_id", "sex", "athlete_key", "mark_raw", "meeting", "date"])
    cc = pd.read_csv("club_canton.csv")[["club", "canton"]].dropna().drop_duplicates("club")
    df = df.merge(cc, on="club", how="left")
    df["sortv"] = df.mark_value.where(df.lower_better == 1, -df.mark_value)
    year = int(a.year)
    cur = _season_ranks(df, year, a.age_min, a.age_max, a.min_cohort)
    prev = _season_ranks(df, year - 1, a.age_min, a.age_max + 1, a.min_cohort)[["indoor", "sex", "discipline", "athlete_key", "pct", "rank"]]
    prev = prev.rename(columns={"pct": "pct_prev", "rank": "rank_prev"})
    if cur.empty:
        sys.exit("Aucun résultat classé pour cette saison et cette tranche d'âge : vérifiez --year, --age-min, --age-max, --min-cohort.")
    cur = cur.merge(prev, on=["indoor", "sex", "discipline", "athlete_key"], how="left")
    cur["level_event"] = [_level_from_rank(r, a) for r in cur["rank"]]
    cur["nn"] = cur.name.map(_norm_name)

    best = cur.sort_values(["rank", "pct"]).drop_duplicates("athlete_key")
    cnt = cur[cur["rank"] <= a.regional].groupby("athlete_key").size().rename("events_regional_or_better")
    best = best.merge(cnt, on="athlete_key", how="left").fillna({"events_regional_or_better": 0})
    best["level_suggested"] = best.level_event
    best["trend"] = ["" if pd.isna(p) else ("progression" if p - q >= 0.10 else "recul" if q - p >= 0.15 else "stable")
                     for p, q in zip(best.pct_prev, best.pct)]
    best["born_early"] = best.birth_date.astype(str).str[5:7].isin(["01", "02", "03"])

    cadre = pd.read_csv(a.cadre, dtype=str).fillna("")
    if "level" not in cadre:
        cadre["level"] = ""
    cadre["level"] = cadre.level.str.strip().str.lower().replace({"nationale": "national", "régional": "regional", "régionale": "regional", "locale": "local"})
    cadre["nn"] = cadre.name.map(_norm_name)
    cadre["by"] = pd.to_numeric(cadre.birth_year, errors="coerce")
    allseen = {f"{n}|{int(b)}" for n, b in zip(df.name.map(_norm_name), pd.to_numeric(df.birth_date.astype(str).str[:4], errors="coerce").fillna(0))}

    def keycols(x):
        return [f"{n}|{b}" for n, b in zip(x.nn, x["by"].fillna(0).astype(int))]

    best["key"] = keycols(best)
    cadre["key"] = keycols(cadre)
    ckeys = set(cadre.key)
    rows = []
    for c in cadre.itertuples():
        m = best[best.key == c.key]
        base = {"athlete": c.name, "birth_year": c.birth_year, "level_current": c.level or "(non indiqué)"}
        if m.empty:
            seen = c.key in allseen
            rows.append({**base, "verdict": "à examiner : aucun résultat classé cette saison" if seen else "introuvable dans la base (vérifier nom et année de naissance)",
                         "best_event": "", "best_mark": "", "cohort_rank": "", "cohort_size": "", "level_suggested": "", "trend": "", "note": "Peut être blessé(e) ou actif(ve) dans une épreuve non classée."})
            continue
        r = m.iloc[0]
        cur_l, sug_l = LEVELS.get(c.level, 0), LEVELS[r.level_suggested]
        if sug_l == 0:
            verdict = "à examiner : en dessous des seuils dans toutes ses épreuves" + (", en recul" if r.trend == "recul" else "")
        elif c.level and sug_l > cur_l:
            verdict = f"à examiner : niveau supérieur possible ({r.level_suggested})"
        else:
            verdict = "confirmé par les résultats"
        note = "Né(e) en début d'année : le bonus d'âge relatif peut s'appliquer." if r.born_early and (int(year) - int(r.by)) <= 16 else ""
        rows.append({**base, "club": r.club, "canton": r.canton, "verdict": verdict, "best_event": f"{r.discipline} ({'salle' if r.indoor else 'plein air'})",
                     "best_mark": r.mark_raw, "cohort_rank": int(r["rank"]), "cohort_size": int(r.n), "level_suggested": r.level_suggested or "-",
                     "events_regional_or_better": int(r.events_regional_or_better), "trend": r.trend, "note": note})
    mycadre = pd.DataFrame(rows)

    cantons = [x.strip().upper() for x in a.cantons.split(",")] if a.cantons else None
    cand = best[(best.level_suggested != "") & (~best.key.isin(ckeys))]
    if cantons:
        cand = cand[cand.canton.isin(cantons)]
    cand = cand.sort_values(["rank", "pct"])
    cand_out = pd.DataFrame({"athlete": cand.name, "birth_year": cand.by.astype(int), "age": cand.age.astype(int), "club": cand.club, "canton": cand.canton,
                             "best_event": cand.discipline + cand.indoor.map({0: " (plein air)", 1: " (salle)"}), "best_mark": cand.mark_raw,
                             "cohort_rank": cand["rank"].astype(int), "cohort_size": cand.n.astype(int), "level_suggested": cand.level_suggested,
                             "events_regional_or_better": cand.events_regional_or_better.astype(int), "trend": cand.trend,
                             "note": ["Né(e) en début d'année." if e else "" for e in cand.born_early]})
    lists = {}
    if a.lists and os.path.exists(a.lists):
        ln = pd.read_csv(a.lists, dtype=str).fillna("")
        for n_, b_, l_ in zip(ln.name.map(_norm_name), ln.birth_year, ln.list):
            lists.setdefault(f"{n_}|{b_}", []).append(l_)
    def tag(name, by):
        return ", ".join(lists.get(f"{_norm_name(name)}|{int(float(by))}", []))
    if len(mycadre):
        mycadre["liste_nationale"] = [tag(n_, b_) for n_, b_ in zip(mycadre.athlete, mycadre.birth_year)]
    cand_out["liste_nationale"] = [tag(n_, b_) for n_, b_ in zip(cand_out.athlete, cand_out.birth_year)]
    to_review = mycadre[mycadre.verdict.str.startswith(("à examiner : en dessous", "à examiner : aucun", "introuvable"))] if len(mycadre) else mycadre
    notes = pd.DataFrame({"Notes": [
        f"Saison {year}. Résultats publics de Swiss Athletics (Alabus), généré le {datetime.now():%d.%m.%Y}, version du script {SCRIPT_VERSION}.",
        f"Méthode : meilleure marque électronique de la saison de chaque athlète de {a.age_min} à {a.age_max} ans (année de la saison moins année de naissance), classée parmi toute la Suisse dans sa cohorte (même sexe, même année de naissance, même discipline). Cohortes de moins de {a.min_cohort} athlètes ignorées.",
        f"Niveau suggéré : rang {a.national} ou mieux = national, {a.regional} ou mieux = régional, {a.local} ou mieux = local. Seuils à calibrer avec les athlètes déjà au cadre (voir le résumé à l'écran).",
        "Ce n'est pas le calcul officiel de PISTE. PISTE combine la performance, le développement sur deux ans, l'âge relatif et un potentiel évalué par les entraîneurs, avec des tables de référence de Swiss Athletics. Ici seuls la performance et sa tendance sont mesurées.",
        "Chaque ligne est à examiner, jamais à retirer : blessure, potentiel, épreuves non classées, équipes de relais et résultats hors base ne sont pas visibles.",
        "Les athlètes mineurs sont nommés. Utilisation interne au CNP avec l'accord des athlètes ou de leurs parents."]})
    with pd.ExcelWriter(a.out) as xw:
        mycadre.to_excel(xw, sheet_name="Cadre actuel", index=False)
        cand_out.to_excel(xw, sheet_name="Pourraient en faire partie", index=False)
        to_review.to_excel(xw, sheet_name="A examiner", index=False)
        notes.to_excel(xw, sheet_name="Notes", index=False)
    print(f"cadre listé : {len(mycadre)}  |  confirmés : {int((mycadre.verdict == 'confirmé par les résultats').sum()) if len(mycadre) else 0}"
          f"  |  à examiner : {len(to_review)}  |  pourraient en faire partie : {len(cand_out)}")
    if len(mycadre):
        r = pd.to_numeric(mycadre.cohort_rank, errors="coerce").dropna()
        if len(r):
            print(f"calibrage : rang de cohorte des membres actuels, médiane {r.median():.0f}, 75e percentile {r.quantile(.75):.0f}, max {r.max():.0f} (seuils actuels {a.national}/{a.regional}/{a.local})")
    print(f"saved {a.out}")
    mycadre.to_csv(a.out.replace(".xlsx", "_cadre.csv"), index=False)
    cand_out.to_csv(a.out.replace(".xlsx", "_candidats.csv"), index=False)


def cmd_coverage(_):
    """Rows per discipline/sex/year, to spot empty or suspiciously small lists."""
    con = sqlite3.connect(DB)
    df = pd.read_sql("SELECT indoor, discipline, sex, year, COUNT(*) AS n FROM bestlist "
                     "GROUP BY indoor, discipline, sex, year", con)
    pv = df.pivot_table(index=["indoor", "discipline", "sex"], columns="year", values="n", fill_value=0)
    pv.to_csv("coverage.csv")
    print(pv.head(30).to_string())
    print("\nfull table saved to coverage.csv (open in Excel)")


def cmd_progression(a):
    con = sqlite3.connect(DB)
    df = load_results(con)
    d = df[df.name.str.contains(a.name, case=False, regex=False) & df.mark_value.notna()].copy()
    if d.empty:
        return print("no athlete found")
    d["sortv"] = d.mark_value.where(d.lower_better == 1, -d.mark_value)
    best = d.sort_values("sortv").drop_duplicates(["athlete_key", "indoor", "discipline", "year"])
    best = best.sort_values(["name", "birth_date", "indoor", "discipline", "year"])
    print(best[["name", "birth_date", "indoor", "discipline", "year", "mark_raw", "club", "meeting"]]
          .to_string(index=False))


def results_table(con):
    """Use the full results if they were downloaded, else the one-best-per-athlete lists."""
    has = con.execute("SELECT name FROM sqlite_master WHERE name='allresults'").fetchone()
    if has and con.execute("SELECT COUNT(*) FROM allresults").fetchone()[0]:
        return "allresults"
    return "bestlist"


def cmd_h2h(a):
    con = sqlite3.connect(DB)
    t = results_table(con)
    df = pd.read_sql(f"SELECT * FROM {t} WHERE (name LIKE ? OR name LIKE ?) AND mark_value IS NOT NULL",
                     con, params=(f"%{a.a}%", f"%{a.b}%"))
    df["sortv"] = df.mark_value.where(df.lower_better == 1, -df.mark_value)
    # best mark per athlete at each meeting/day/discipline
    best = df.sort_values("sortv").drop_duplicates(["meeting", "date", "disci_id", "indoor", "athlete_key"])
    ka = best[best.name.str.contains(a.a, case=False, regex=False)]
    kb = best[best.name.str.contains(a.b, case=False, regex=False)]
    m = ka.merge(kb, on=["meeting", "date", "disci_id", "indoor"], suffixes=("_a", "_b"))
    if m.empty:
        return print(f"no shared meetings found (using table {t})")
    m["winner"] = [a.a if x < y else a.b if x > y else "tie" for x, y in zip(m.sortv_a, m.sortv_b)]
    m = m.sort_values("date")
    print(m[["date", "discipline_a", "meeting", "mark_raw_a", "mark_raw_b", "winner"]].to_string(index=False))
    print(m.winner.value_counts().to_string())
    print(f"(source table: {t})")


def cmd_medals(_):
    con = sqlite3.connect(DB)
    t = results_table(con)
    like = " OR ".join("meeting LIKE ?" for _ in CHAMPIONSHIP_PATTERNS)
    q = f"""SELECT year, discipline, sex, place, round_code, name, club, meeting FROM {t}
            WHERE place BETWEEN 1 AND 3 AND ({like}) ORDER BY year, discipline, sex, place"""
    d = pd.read_sql(q, con, params=[f"%{p}%" for p in CHAMPIONSHIP_PATTERNS])
    print(d.to_string(index=False))
    print("\nround codes seen:", d.round_code.value_counts().to_dict(), f"(source table: {t})")
    print("Check round_code values (finals vs heats) and meeting names before trusting medal counts.")


def main():
    p = argparse.ArgumentParser()
    s = p.add_subparsers(dest="cmd", required=True)
    s.add_parser("discover").set_defaults(f=lambda a: discover())
    f = s.add_parser("fetch")
    f.add_argument("--years", default="2006-2026")
    f.add_argument("--top", type=int, default=5000, choices=[10, 30, 100, 500, 5000])
    f.add_argument("--disciplines", default="", help="comma list of labels, e.g. '100 m,200 m'")
    f.add_argument("--refresh", action="store_true", help="download again even if the page is cached")
    f.add_argument("--all-results", action="store_true",
                   help="every result (heats, finals, all meetings) instead of one best per athlete; "
                        "stored in table 'allresults'. Needs 2 requests per list.")
    f.set_defaults(f=cmd_fetch)
    s.add_parser("clubs").set_defaults(f=cmd_clubs)
    s.add_parser("coverage").set_defaults(f=cmd_coverage)
    mc = s.add_parser("mapclubs")
    mc.add_argument("file", nargs="?", default="club_canton_additions.csv")
    mc.set_defaults(f=cmd_mapclubs)
    rc = s.add_parser("records")
    rc.add_argument("--canton", default="NE")
    rc.add_argument("--baseline", default="ne_records_baseline.csv")
    rc.add_argument("--out", default="ne_records_updated.csv")
    rc.add_argument("--all-nationalities", action="store_true", help="switch off the eligibility rules")
    rc.add_argument("--with-juniors", action="store_true", help="also check U16/U18/U20 records (skipped by default)")
    rc.set_defaults(f=cmd_records)
    ex = s.add_parser("export")
    ex.add_argument("--canton", default="NE")
    ex.add_argument("--records", default="ne_records_updated.csv")
    ex.set_defaults(f=cmd_export)
    st_ = s.add_parser("stats")
    st_.add_argument("--canton", default="NE")
    st_.add_argument("--years", required=True, help="e.g. 2019,2023-2025")
    st_.add_argument("--org", default=None, help="file name prefix, default ANA for NE")
    st_.add_argument("--full-birthdate", action="store_true", help="print dd.mm.yyyy instead of the birth year")
    st_.set_defaults(f=cmd_stats)
    up = s.add_parser("update")
    up.add_argument("--canton", default="NE")
    up.add_argument("--years", default="", help="default: current year, e.g. 2025-2026")
    up.add_argument("--disciplines", default="")
    up.add_argument("--all-results", action="store_true", help="also refresh the full results (slower)")
    up.add_argument("--skip-fetch", action="store_true", help="only rebuild outputs from the database")
    up.add_argument("--stats", action="store_true", help="also write the season PDF")
    up.add_argument("--baseline", default="ne_records_baseline.csv")
    up.set_defaults(f=cmd_update)
    rm = s.add_parser("romandie")
    rm.add_argument("--year", default=str(datetime.now().year))
    rm.add_argument("--n", type=int, default=10)
    rm.add_argument("--min-age", type=int, default=0, help="only athletes of at least this age in the season")
    rm.add_argument("--all-nationalities", action="store_true")
    rm.set_defaults(f=cmd_romandie)
    tk = s.add_parser("track")
    tk.add_argument("--cadre", default="cadre.csv", help="CSV with columns name,birth_year")
    tk.add_argument("--limits", default="limits_2026_draft.csv")
    tk.add_argument("--champs", default="", help="e.g. EC2026,U20WC2026")
    tk.add_argument("--within", type=float, default=3.0, help="percent from the limit counted as 'proche'")
    tk.add_argument("--out", default="track.csv")
    tk.set_defaults(f=cmd_track)
    ra = s.add_parser("radar")
    ra.add_argument("--cantons", default=",".join(ROMANDIE))
    ra.add_argument("--limits", default="limits_2026_draft.csv")
    ra.add_argument("--champs", default="")
    ra.add_argument("--within", type=float, default=3.0)
    ra.add_argument("--all-nationalities", action="store_true")
    ra.add_argument("--out", default="radar.csv")
    ra.set_defaults(f=cmd_radar)
    ca = s.add_parser("cadre-audit")
    ca.add_argument("--cadre", default="cadre.csv", help="CSV: name,birth_year,level (national, regional, local, optional)")
    ca.add_argument("--year", default=str(datetime.now().year))
    ca.add_argument("--cantons", default=",".join(ROMANDIE), help="where to look for athletes who are not listed; empty = all Switzerland")
    ca.add_argument("--age-min", type=int, default=13)
    ca.add_argument("--age-max", type=int, default=20)
    ca.add_argument("--national", type=int, default=5)
    ca.add_argument("--regional", type=int, default=20)
    ca.add_argument("--local", type=int, default=50)
    ca.add_argument("--min-cohort", type=int, default=30)
    ca.add_argument("--lists", default="cadre_national.csv", help="optional CSV name,birth_year,list (Swiss Starters, Swiss Starters Future)")
    ca.add_argument("--out", default="cadre_audit.xlsx")
    ca.set_defaults(f=cmd_cadre_audit)
    wd = s.add_parser("webdata")
    wd.add_argument("--year", default=str(datetime.now().year))
    wd.add_argument("--n", type=int, default=10)
    wd.add_argument("--min-age", type=int, default=20)
    wd.add_argument("--limits", default="limits_2026_draft.csv")
    wd.add_argument("--within", type=float, default=3.0)
    wd.add_argument("--out", default="webdata.json")
    wd.set_defaults(f=cmd_webdata)
    rs = s.add_parser("reports")
    rs.add_argument("--n", type=int, default=10)
    rs.add_argument("--include-assisted", action="store_true")
    rs.add_argument("--swiss-only", action="store_true", help="only Swiss athletes (see nationality_overrides.csv)")
    rs.set_defaults(f=cmd_reports)
    r = s.add_parser("report")
    r.add_argument("--canton", required=True)
    r.add_argument("--year", type=int)
    r.add_argument("--n", type=int, default=10)
    r.add_argument("--include-assisted", action="store_true", help="keep marks with wind above 2.0 m/s")
    r.add_argument("--swiss-only", action="store_true", help="only Swiss athletes (see nationality_overrides.csv)")
    r.set_defaults(f=cmd_report)
    g = s.add_parser("progression")
    g.add_argument("name")
    g.set_defaults(f=cmd_progression)
    h = s.add_parser("h2h")
    h.add_argument("a")
    h.add_argument("b")
    h.set_defaults(f=cmd_h2h)
    s.add_parser("medals").set_defaults(f=cmd_medals)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
