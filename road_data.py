#!/usr/bin/env python3
"""
Road running results of Swiss athletes (5 km to marathon) from the World Athletics season top lists.

  python road_data.py                  refresh the current season if due, then fill missing past seasons
  python road_data.py --budget-min 20  stop starting new lists after 20 minutes

Why the top lists: World Athletics only lists road races on certified (measured) courses, so every result
here counts for personal bests and progression. The lists are filtered on Swiss nationality and include
races abroad. They stop at a level limit (e.g. 2:37:00 for the men's marathon), so they cover competitive
runners, not every finisher.

Each result is linked to the athlete's Alabus profile when the name and birth date match; runners without
track results get a profile of their own. One page every 2 seconds; a season list is 1 to 3 pages.
Writes the table `road` in athletics.db and, on GitHub, the output `changed`.
"""
import argparse
import os
import re
import sqlite3
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timezone

import httpx
from bs4 import BeautifulSoup

URL = os.environ.get("ROAD_URL", "https://worldathletics.org/records/toplists/road-running/{slug}/all/{sex}/senior/{season}")
EVENTS = [("5-kilometres", "5 km Strasse"), ("10-kilometres", "10 km Strasse"), ("15-kilometres", "15 km Strasse"),
          ("10-miles", "10 Meilen Strasse"), ("20-kilometres", "20 km Strasse"), ("half-marathon", "Halbmarathon"),
          ("marathon", "Marathon")]
SEXES = [("men", "M"), ("women", "W")]
MONTHS = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}
FIRST_YEAR = 2006
MAX_FAILURES = 10


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def norm(name):
    n = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", n).strip()


def athlete_key(name, birth_iso):
    """Same formula as alabus_scraper.athlete_key, so the same person gets the same key."""
    import hashlib
    return hashlib.sha1(f"{norm(name)}|{birth_iso}".encode()).hexdigest()[:12]


def tokens(name):
    return frozenset(norm(re.sub(r"[-']", " ", str(name))).split())


def display_name(raw):
    """'Oria LIACI' -> 'Oria Liaci' (World Athletics writes family names in capitals)."""
    out = []
    for w in str(raw).split():
        out.append("-".join(p[:1].upper() + p[1:].lower() for p in w.split("-")) if w.isupper() and len(w) > 1 else w)
    return " ".join(out)


def parse_time(t):
    t = str(t).strip().replace(",", ".")
    if not re.fullmatch(r"\d{1,2}(:\d{2}){1,2}(\.\d+)?", t):
        return None
    v = 0.0
    for p in t.split(":"):
        v = v * 60 + float(p)
    return v


def parse_day(t):
    m = re.fullmatch(r"(\d{1,2}) ([A-Z]{3}) (\d{4})", str(t).strip().upper())
    if m and m.group(2) in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"
    m = re.fullmatch(r"(\d{4})", str(t).strip())
    return m.group(1) if m else None


def parse_page(html):
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for tr in soup.select("table.records-table tbody tr"):
        c = {td.get("data-th", "").strip(): td for td in tr.find_all("td")}
        if "Mark" not in c or "Competitor" not in c:
            continue
        a = c["Competitor"].find("a")
        href = a.get("href", "") if a else ""
        wa = re.search(r"(\d{6,})\s*$", href)
        txt = {k: " ".join(v.get_text(" ").split()) for k, v in c.items()}
        rank = re.match(r"\d+", txt.get("Rank", ""))
        rows.append({"rank": int(rank.group(0)) if rank else None, "mark": txt["Mark"], "name": display_name(txt["Competitor"]),
                     "wa_id": wa.group(1) if wa else None, "dob": parse_day(txt.get("DOB", "")), "nat": txt.get("Nat", "").split()[-1:] or [""],
                     "pos": int(txt["Pos"]) if txt.get("Pos", "").isdigit() else None, "venue": txt.get("Venue", ""),
                     "date": parse_day(txt.get("Date", "")), "score": int(txt["ResultScore"]) if txt.get("ResultScore", "").isdigit() else None})
        rows[-1]["nat"] = rows[-1]["nat"][0]
    return rows


class Matcher:
    """Finds the Alabus athlete for a road result: same key (name and birth date), else same birth date and a
    shared family name, else same birth year and two shared names. Otherwise the runner gets a key of their own."""

    def __init__(self, con):
        self.by_key, self.by_dob, self.by_year = {}, defaultdict(list), defaultdict(list)
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("allresults", "bestlist"):
            if t not in have:
                continue
            for key, name, bd in con.execute(f"SELECT athlete_key, MAX(name), MAX(birth_date) FROM {t} WHERE nat='SUI' GROUP BY athlete_key"):
                if not key or key in self.by_key or not bd:
                    continue
                self.by_key[key] = name
                self.by_dob[bd].append((key, tokens(name), name))
                self.by_year[bd[:4]].append((key, tokens(name), name))

    def find(self, name, dob):
        if not dob:
            return None, name
        k = athlete_key(name, dob)
        if k in self.by_key:
            return k, self.by_key[k]
        t = tokens(name)
        if len(dob) == 10:
            c = [(k2, n) for k2, lt, n in self.by_dob.get(dob, []) if len(t & lt) >= 1 and (t <= lt or lt <= t or len(t & lt) >= 2)]
        else:
            c = [(k2, n) for k2, lt, n in self.by_year.get(dob, []) if len(t & lt) >= 2]
        if len({x[0] for x in c}) == 1:
            return c[0]
        return athlete_key(name, dob), name


def setup(con):
    con.execute("""CREATE TABLE IF NOT EXISTS road (event TEXT, label TEXT, sex TEXT, season INT, rank INT, mark_raw TEXT,
                   mark_value REAL, name TEXT, wa_id TEXT, birth_date TEXT, nat TEXT, place INT, venue TEXT, date TEXT,
                   score INT, athlete_key TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS road_list ON road(event, sex, season)")
    con.execute("CREATE TABLE IF NOT EXISTS road_done (event TEXT, sex TEXT, season INT, fetched TEXT, n INT, PRIMARY KEY (event, sex, season))")
    con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    con.commit()


def fetch_list(client, slug, wsex, season, delay):
    """All pages of one season list (100 rows per page). Returns rows, or None if the list could not be read."""
    out = []
    for page in range(1, 30):
        params = {"regionType": "countries", "region": "sui", "page": page, "bestResultsOnly": "false"}
        for attempt in range(3):
            try:
                r = client.get(URL.format(slug=slug, sex=wsex, season=season), params=params)
                if r.status_code == 404:
                    return out
                r.raise_for_status()
                break
            except httpx.HTTPError as e:
                log(f"  retry {attempt + 1}: {e}")
                time.sleep(5 * (attempt + 1))
        else:
            return None
        rows = parse_page(r.text)
        time.sleep(delay)
        if not rows:
            break
        out += rows
        if len(rows) < 100:
            break
    return out


def run(a):
    con = sqlite3.connect(a.db)
    setup(con)
    matcher = Matcher(con)
    today = date.today()
    deadline = time.time() + a.budget_min * 60
    last = (con.execute("SELECT v FROM meta WHERE k='road_last_refresh'").fetchone() or [None])[0]
    due = a.force_refresh or not last or (today - date.fromisoformat(last)).days >= a.refresh_days
    done = {tuple(r) for r in con.execute("SELECT event, sex, season FROM road_done")}
    current = [today.year] + ([today.year - 1] if today.month <= 2 else [])
    queue = [(slug, label, ws, sx, y) for y in current for slug, label in EVENTS for ws, sx in SEXES] if due else []
    queue += [(slug, label, ws, sx, y) for y in range(today.year - 1, a.first_year - 1, -1) for slug, label in EVENTS
              for ws, sx in SEXES if (slug, sx, y) not in done and not (due and y in current)]
    log(f"{len(queue)} road lists to read ({'current season due' if due else 'current season up to date'})")
    client = httpx.Client(headers={"User-Agent": "athletes.mcintyre.ch road results (non-commercial, weekly)"},
                          timeout=30, follow_redirects=True)
    fetched = failures = n_rows = 0
    refresh_ok = True
    for i, (slug, label, ws, sx, y) in enumerate(queue):
        if time.time() > deadline:
            log("time budget reached: the next run continues")
            if any(q[4] in current for q in queue[i:]):
                refresh_ok = False
            break
        rows = fetch_list(client, slug, ws, y, a.delay)
        if rows is None:
            failures += 1
            refresh_ok = refresh_ok and y not in current
            log(f"FAILED {slug} {sx} {y}")
            if failures >= MAX_FAILURES:
                log("too many failures: World Athletics may be down or blocking; stopping")
                break
            continue
        failures = 0
        recs = []
        for r in rows:
            v = parse_time(r["mark"])
            if v is None or r["nat"] != "SUI" or not r["date"]:
                continue
            key, name = matcher.find(r["name"], r["dob"])
            recs.append((slug, label, sx, y, r["rank"], r["mark"], v, name, r["wa_id"], r["dob"], r["nat"], r["pos"],
                         r["venue"], r["date"], r["score"], key))
        with con:
            con.execute("DELETE FROM road WHERE event=? AND sex=? AND season=?", (slug, sx, y))
            con.executemany("INSERT INTO road VALUES (" + ",".join("?" * 16) + ")", recs)
            con.execute("INSERT OR REPLACE INTO road_done VALUES (?, ?, ?, ?, ?)",
                        (slug, sx, y, datetime.now(timezone.utc).isoformat(timespec="seconds"), len(recs)))
        fetched += 1
        n_rows += len(recs)
        log(f"{label} {sx} {y}: {len(recs)}")
    if due and refresh_ok and fetched:
        con.execute("INSERT OR REPLACE INTO meta VALUES ('road_last_refresh', ?)", (today.isoformat(),))
        con.commit()
    total = con.execute("SELECT COUNT(*), COUNT(DISTINCT athlete_key) FROM road").fetchone()
    log(f"road: {fetched} lists read, {n_rows} results; stored {total[0]} results of {total[1]} athletes")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if fetched else 'false'}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"### Road results (World Athletics)\n\n- Lists read: {fetched}\n- Results stored: {total[0]:,} "
                    f"({total[1]:,} athletes)\n" + (f"- **{failures} lists failed**\n" if failures else ""))
    return 0 if fetched or not queue else 1


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="athletics.db")
    p.add_argument("--budget-min", type=float, default=40)
    p.add_argument("--refresh-days", type=int, default=6)
    p.add_argument("--force-refresh", action="store_true")
    p.add_argument("--first-year", type=int, default=FIRST_YEAR)
    p.add_argument("--delay", type=float, default=2.0, help="seconds between pages")
    sys.exit(run(p.parse_args()))


if __name__ == "__main__":
    main()
