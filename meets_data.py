#!/usr/bin/env python3
"""
World Athletics Continental Tour meetings in Europe (Gold, Silver, Bronze, Challenger): the calendar, and for past
editions the marks of each event, so the site can show the level needed at each meeting.

  python meets_data.py                 refresh the calendar, then read up to 60 results pages not yet stored
  python meets_data.py --budget 200    read more results pages in this run
  python meets_data.py --years 2       how many past seasons to keep results for (default 3)

Source: the public calendar and results pages of worldathletics.org (the data each page carries for its own table).
One page every 2 seconds; a results page is read once and kept, so after the first weeks a run is a dozen pages.
Writes meets.json (no athlete names: meeting, date, place, level, and per event the list of marks).
"""
import argparse
import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

CAL = "https://worldathletics.org/competition/calendar-results"
OUT = Path(__file__).resolve().parent / "meets.json"
EUROPE, CONTINENTAL_TOUR = "3", "3773"  # ids of the calendar's own filters (area Europe, group Continental Tour)
UA = {"User-Agent": "athletes.mcintyre.ch meeting levels (non-commercial, a few pages a night)"}


def log(msg):
    print(msg, flush=True)


def page_data(client, url, params=None):
    """The data a worldathletics.org page carries for its own display, or None."""
    for attempt in range(3):
        try:
            r = client.get(url, params=params)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
            return json.loads(m.group(1)).get("props", {}).get("pageProps") if m else None
        except (httpx.HTTPError, ValueError) as e:
            log(f"  retry {attempt + 1}: {e}")
            time.sleep(5 * (attempt + 1))
    return None


def calendar(client, start, end, delay):
    """Continental Tour meetings in Europe between two dates -> {id: meeting}."""
    out, offset = {}, 0
    while True:
        params = {"startDate": start, "endDate": end, "regionType": "area", "regionId": EUROPE, "competitionGroupId": CONTINENTAL_TOUR}
        if offset:
            params["offset"] = str(offset)
        pp = page_data(client, CAL, params)
        time.sleep(delay)
        ev = (pp or {}).get("initialEvents")
        if not ev or not ev.get("results"):
            return out if offset else None
        for e in ev["results"]:
            m = re.match(r"^(?:(.*),\s*)?([^,]+?)\s*\(([A-Z]{3})\)\s*$", e.get("venue") or "")
            out[str(e["id"])] = {"name": e["name"], "venue": e.get("venue") or "", "stadium": (m.group(1) or "") if m else "",
                                 "city": m.group(2) if m else "", "country": m.group(3) if m else "",
                                 "level": e.get("competitionSubgroup") or "", "cat": e.get("rankingCategory") or "",
                                 "start": e["startDate"], "end": e.get("endDate") or e["startDate"],
                                 "disciplines": e.get("disciplines") or "", "has_results": bool(e.get("hasResults"))}
        offset += len(ev["results"])
        if offset >= ev.get("hits", 0) or len(ev["results"]) < 100:
            return out


def find(o, key):
    """First dict under o that has `key`."""
    if isinstance(o, dict):
        if key in o:
            return o
        for v in o.values():
            hit = find(v, key)
            if hit:
                return hit
    elif isinstance(o, list):
        for v in o:
            hit = find(v, key)
            if hit:
                return hit
    return None


def results(client, mid, delay, sample):
    """Marks per event of one meeting -> {"M|100 Metres": [[mark, place], ...]} (best race first: final, else all races)."""
    events, days, day = {}, [1], 0
    while day < len(days):
        pp = page_data(client, f"{CAL}/results/{mid}", {"day": str(days[day])} if day else None)
        time.sleep(delay)
        box = find(pp, "eventTitles") if pp else None
        if not box:
            return None if not events else events
        if day == 0:
            opts = (box.get("options") or {}).get("days") or []
            days = sorted({int(d.get("day") or 1) for d in opts} | {1})
        if day and (box.get("parameters") or {}).get("day") not in (days[day], str(days[day])):
            break  # the address did not select another day: stop instead of storing day 1 twice
        for title in box.get("eventTitles") or []:
            for ev in title.get("events") or []:
                if ev.get("isRelay"):
                    continue
                name = re.sub(r"^(Men's|Women's|Mixed)\s+", "", ev.get("event") or "").strip()
                rows = []
                for race in ev.get("races") or []:
                    for r in race.get("results") or []:
                        if sample and not sample[0]:
                            sample[0] = True
                            log(f"  a result as the page gives it: {json.dumps({k: (v if not isinstance(v, (dict, list)) else '...') for k, v in r.items()}, ensure_ascii=False)[:600]}")
                        mark = str(r.get("mark") or "").strip()
                        if mark and re.match(r"^\d", mark):
                            rows.append([mark, str(r.get("place") or "").strip().rstrip("."), race.get("race") or ""])
                if rows and ev.get("gender") in ("M", "W"):
                    events.setdefault(f"{ev['gender']}|{name}", []).extend(rows)
        day += 1
    return events


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--budget", type=int, default=60, help="results pages to read in this run")
    ap.add_argument("--years", type=int, default=3, help="past seasons to keep results for")
    ap.add_argument("--delay", type=float, default=2.0)
    a = ap.parse_args()
    today = date.today()
    store = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    meets = store.get("meets", {})
    before = json.dumps(meets, sort_keys=True)
    with httpx.Client(headers=UA, timeout=40, follow_redirects=True) as client:
        for year in range(today.year - a.years + 1, today.year + 2):
            if year < today.year and store.get("calendar_done", {}).get(str(year)):
                continue  # a past season's calendar no longer changes
            got = calendar(client, f"{year}-01-01", f"{year}-12-31", a.delay)
            if got is None:
                log(f"calendar {year}: could not be read")
                continue
            log(f"calendar {year}: {len(got)} meetings")
            for mid, m in got.items():
                m["events"] = meets.get(mid, {}).get("events")
                m["read"] = meets.get(mid, {}).get("read")
                meets[mid] = m
            if year < today.year and got:
                store.setdefault("calendar_done", {})[str(year)] = today.isoformat()
        # results: finished meetings not read yet, most recent first; a meeting read within 10 days of its end is read
        # once more later, in case results were completed
        todo = [mid for mid, m in meets.items() if m["has_results"] and m["end"] < today.isoformat()
                and (m.get("events") is None or (m.get("read") and m["read"] < (date.fromisoformat(m["end"]) + timedelta(days=10)).isoformat()
                                                 and today > date.fromisoformat(m["end"]) + timedelta(days=10)))]
        todo.sort(key=lambda mid: meets[mid]["end"], reverse=True)
        log(f"{len(todo)} results pages to read, {min(len(todo), a.budget)} in this run")
        sample, done = [False], 0
        for mid in todo[:a.budget]:
            ev = results(client, mid, a.delay, sample)
            if ev is None:
                log(f"  {meets[mid]['start']} {meets[mid]['name']}: no results could be read")
                continue
            meets[mid]["events"], meets[mid]["read"] = ev, today.isoformat()
            done += 1
            if done <= 5 or done % 20 == 0:
                log(f"  {meets[mid]['start']} {meets[mid]['name']} ({meets[mid]['level']}): {len(ev)} events, {sum(len(v) for v in ev.values())} marks")
    if not meets:
        sys.exit("no meetings read: meets.json was left as it is")
    if json.dumps(meets, sort_keys=True) == before:
        log("no change")
        return
    store.update({"source": CAL, "updated": today.isoformat(), "meets": meets,
                  "_about": "World Athletics Continental Tour meetings in Europe, from the public calendar and results pages of "
                            "worldathletics.org. Per meeting: level, dates, place, and per event [mark, place, race] of every result. "
                            "No names. Written by meets_data.py, do not edit by hand."})
    OUT.write_text(json.dumps(store, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    with_results = sum(1 for m in meets.values() if m.get("events"))
    log(f"wrote {OUT.name}: {len(meets)} meetings, {with_results} with results")


if __name__ == "__main__":
    main()
