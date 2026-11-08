#!/usr/bin/env python3
"""
Keep athletics.db up to date from Alabus within a time budget. Built for GitHub Actions (one job may run
6 hours at most), works the same on a laptop.

  python update_data.py                    refresh the current season if older than 6 days, then fill
                                           missing past seasons until the time budget runs out
  python update_data.py --force-refresh    refresh the current season now
  python update_data.py --budget-min 60    stop starting new lists after 60 minutes

How it works
  * Uses alabus_scraper.py (same folder) for the actual page downloads and parsing: same polite pace
    (DELAY = 1 second between pages), same tables (bestlist, allresults), same columns.
  * A list = one discipline, sex, season, indoor or outdoor, either "best per athlete" (bestlist) or
    "all results" (allresults). The table done_lists remembers which lists are complete, so a run that
    stops at the time limit continues exactly there next time.
  * Refreshing a list replaces its rows in one transaction, so corrections and removals on Alabus
    are picked up and a failed download never leaves a half-empty list.
  * After 15 failed lists in a row it stops (Alabus down or blocking) and keeps the data it has.

Writes status.json (shown in the repository) and, on GitHub, the outputs `changed` and a run summary.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import alabus_scraper as S

FIRST_YEAR = 2006  # Alabus results are only systematic from 2006
TABLES = ("allresults", "bestlist")
MAX_FAILURES = 15


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def setup(con):
    for t in TABLES:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        if cols and "wind" not in cols:
            con.execute(f"ALTER TABLE {t} ADD COLUMN wind REAL")
        con.execute(S.SQL_CREATE.replace("bestlist", t))
        con.execute(f"CREATE INDEX IF NOT EXISTS {t}_list ON {t}(indoor, disci_id, sex, year)")
    con.execute("""CREATE TABLE IF NOT EXISTS done_lists (tbl TEXT, indoor INT, disci_id TEXT, sex TEXT, year TEXT,
                   fetched TEXT, n INT, PRIMARY KEY (tbl, indoor, disci_id, sex, year))""")
    con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    # A database filled on a laptop has no done_lists yet: past lists that already have rows count as done.
    if not con.execute("SELECT 1 FROM done_lists LIMIT 1").fetchone():
        for t in TABLES:
            con.execute(f"""INSERT OR IGNORE INTO done_lists
                SELECT '{t}', indoor, disci_id, sex, year, 'imported', COUNT(*) FROM {t}
                WHERE CAST(year AS INT) < ? GROUP BY indoor, disci_id, sex, year""", (date.today().year,))
    con.commit()


def meta_get(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def meta_set(con, k, v):
    con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (k, str(v)))
    con.commit()


def load_schema():
    """discover() reads the discipline and category menus from Alabus (2 pages)."""
    try:
        S.discover()
        schema = json.loads(S.SCHEMA.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - anything here means Alabus is unreachable or changed
        log(f"discover failed: {e}")
        return None
    if not schema.get("indoor", {}).get("true") or not schema.get("indoor", {}).get("false"):
        log("discover found no disciplines: Alabus may have changed its page")
        return None
    return schema


def all_lists(schema, year):
    cats = {"M": schema["categories"].get("Alle Männer", "M"), "W": schema["categories"].get("Alle Frauen", "W")}
    for indoor_key, discs in schema["indoor"].items():
        for disci, label in discs.items():
            for sex, blcat in cats.items():
                for t in TABLES:
                    yield t, indoor_key, disci, label, sex, blcat, str(year)


def fetch_list(con, item, top):
    """Download one list and replace its rows. Returns the number of rows, or None on failure."""
    t, indoor_key, disci, label, sex, blcat, year = item
    params = {"blyear": year, "blcat": blcat, "disci": disci, "indoor": indoor_key, "top": top}
    tag = f"{label} {sex} {year} {'in' if indoor_key == 'true' else 'out'} {t}"
    try:
        if t == "bestlist":
            # Alabus remembers "Alle Resultate" in the session after an allresults list: start a fresh one so
            # this list comes back as the default "one result per athlete".
            S.client.cookies.clear()
        page = S.fetch_all_list(params) if t == "allresults" else S.fetch(params)
        rows = S.clean(S.parse_page(page, tag), label, indoor_key == "true", sex, year, disci)
    except Exception as e:  # noqa: BLE001
        log(f"FAILED {tag}: {e}")
        return None
    indoor = int(indoor_key == "true")
    with con:  # one transaction: old rows out, new rows in
        con.execute(f"DELETE FROM {t} WHERE indoor=? AND disci_id=? AND sex=? AND year=?", (indoor, disci, sex, year))
        con.executemany(f"INSERT OR REPLACE INTO {t} VALUES (" + ",".join("?" * 22) + ")", rows)
        con.execute("INSERT OR REPLACE INTO done_lists VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (t, indoor, disci, sex, year, datetime.now(timezone.utc).isoformat(timespec="seconds"), len(rows)))
    cap = "  (cap reached: some results beyond the first {top} are missing)".format(top=top) if len(rows) >= top else ""
    log(f"{tag}: {len(rows)}{cap}")
    return len(rows)


def run(a):
    deadline = time.time() + a.budget_min * 60
    S.DB = a.db
    S.DELAY = a.delay
    S.REFRESH = True  # always ask Alabus, never an old cached page
    con = sqlite3.connect(a.db)
    setup(con)
    today = date.today()
    st = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "scrape_ok": True, "message": ""}
    fetched = failures = 0

    last = meta_get(con, "last_refresh")
    due = a.force_refresh or not last or (today - date.fromisoformat(last)).days >= a.refresh_days
    if not due and (a.no_backfill or meta_get(con, "history_complete") == "1"):
        log(f"Nothing to do: current season refreshed on {last}, history complete.")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
                f.write("changed=false\n")
        return 0
    schema = load_schema()
    if schema is None:
        st.update(scrape_ok=False, message="Alabus could not be read (site down, blocked or changed). Nothing was downloaded.")
    else:
        seasons = [today.year] + ([today.year - 1] if today.month <= 2 else [])  # late results of last season
        queue = []
        if due:
            queue += [("refresh", it) for y in seasons for it in all_lists(schema, y)]
        if not a.no_backfill:
            done = {tuple(r) for r in con.execute("SELECT tbl, indoor, disci_id, sex, year FROM done_lists")}
            for y in range(today.year - 1, a.first_year - 1, -1):  # newest missing seasons first
                if y in seasons and due:
                    continue
                for it in all_lists(schema, y):
                    if (it[0], int(it[1] == "true"), it[2], it[4], it[6]) not in done:
                        queue.append(("backfill", it))
        log(f"{sum(1 for k, _ in queue if k == 'refresh')} lists to refresh, "
            f"{sum(1 for k, _ in queue if k == 'backfill')} past lists still missing")
        refresh_failed, processed = False, 0
        for kind, it in queue:
            if time.time() > deadline:
                st["message"] = "Time budget reached: the next run continues where this one stopped."
                break
            n = fetch_list(con, it, a.top)
            processed += 1
            for f in S.CACHE.glob("*.html"):  # pages are parsed already; a full history would fill the disk
                f.unlink()
            if n is None:
                failures += 1
                refresh_failed |= kind == "refresh"
                if failures >= MAX_FAILURES:
                    st.update(scrape_ok=False, message=f"{MAX_FAILURES} lists failed in a row: stopped, Alabus may be down or blocking.")
                    break
            else:
                failures = 0
                fetched += 1
        if due and not refresh_failed and not any(k == "refresh" for k, _ in queue[processed:]):
            meta_set(con, "last_refresh", today.isoformat())

    # status for the repository page and the run summary
    done = {}
    for (y, n) in con.execute("SELECT year, COUNT(*) FROM done_lists GROUP BY year"):
        done[int(y)] = n
    expected = len(list(all_lists(schema, 0))) if schema else None
    years_complete = sorted(y for y, n in done.items() if expected and n >= expected)
    years_missing = [y for y in range(a.first_year, today.year + 1) if y not in years_complete]
    results = sum(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES)
    st.update(finished=datetime.now(timezone.utc).isoformat(timespec="seconds"), lists_downloaded=fetched,
              last_refresh=meta_get(con, "last_refresh"), results_stored=results,
              seasons_complete=years_complete, seasons_missing=years_missing,
              history_complete=not [y for y in years_missing if y < today.year])
    if schema:
        meta_set(con, "history_complete", int(st["history_complete"]))
    Path(a.status).write_text(json.dumps(st, indent=1) + "\n", encoding="utf-8")
    log(json.dumps(st))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if fetched else 'false'}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"### Data update\n\n- Lists downloaded: {fetched}\n- Results stored: {results:,}\n"
                    f"- Last refresh of the current season: {st['last_refresh']}\n"
                    f"- Seasons still missing: {', '.join(map(str, years_missing)) or 'none'}\n"
                    + (f"- **{st['message']}**\n" if st["message"] else ""))
    return 0 if st["scrape_ok"] or fetched else 1


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="athletics.db")
    p.add_argument("--budget-min", type=float, default=290, help="stop starting new lists after this many minutes")
    p.add_argument("--refresh-days", type=int, default=6, help="refresh the current season when older than this")
    p.add_argument("--force-refresh", action="store_true")
    p.add_argument("--no-backfill", action="store_true", help="do not download missing past seasons")
    p.add_argument("--first-year", type=int, default=FIRST_YEAR)
    p.add_argument("--top", type=int, default=5000)
    p.add_argument("--delay", type=float, default=S.DELAY, help="seconds between pages (be polite)")
    p.add_argument("--status", default="status.json")
    sys.exit(run(p.parse_args()))


if __name__ == "__main__":
    main()
