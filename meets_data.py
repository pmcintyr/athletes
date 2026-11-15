#!/usr/bin/env python3
"""
International competitions in Europe from the World Athletics calendar: the Continental Tour (outdoor meetings), the
Indoor Tour, the Label Road Races and the Cross Country Tour. For each: the calendar, and for past editions the marks
of each event, so the site can show the level needed.

  python meets_data.py                 refresh the calendars, then read up to 120 results pages not yet stored
  python meets_data.py --budget 200    read more results pages in this run
  python meets_data.py --years 2       how many past seasons to keep results for (default 3)

Source: the public calendar and results pages of worldathletics.org (the data each page carries for its own table).
One page every 2 seconds; a results page is read once and kept, so after the first weeks a run is a dozen pages.
Writes meets.json (no athlete names: competition, date, place, level, and per event the marks, the best 80 at most).
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
EUROPE = "3"  # ids of the calendar's own filters: area Europe, and the competition groups below
TOURS = {"ct": "3773", "indoor": "3677", "road": "3775", "xc": "3664"}  # Continental Tour, Indoor Tour, Label Road Races, Cross Country Tour
KEEP = 80  # marks kept per event: road and cross country results can run to hundreds of finishers
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


def calendar(client, tour, start, end, delay):
    """Competitions of one tour in Europe between two dates -> {id: competition}."""
    out, offset = {}, 0
    while True:
        params = {"startDate": start, "endDate": end, "regionType": "area", "regionId": EUROPE, "competitionGroupId": TOURS[tour]}
        if offset:
            params["offset"] = str(offset)
        pp = page_data(client, CAL, params)
        time.sleep(delay)
        ev = (pp or {}).get("initialEvents")
        if not ev or not ev.get("results"):
            return out if offset else None
        for e in ev["results"]:
            m = re.match(r"^(?:(.*),\s*)?([^,]+?)\s*\(([A-Z]{3})\)\s*$", e.get("venue") or "")
            out[str(e["id"])] = {"tour": tour, "name": e["name"], "venue": e.get("venue") or "", "stadium": (m.group(1) or "") if m else "",
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
        on_page = {}
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
                    # two races of the same name on one page (long and short race of a cross country) stay apart;
                    # the same event on another day (heats, then final) is added to the first
                    on_page[name, ev["gender"]] = on_page.get((name, ev["gender"]), 0) + 1
                    n = on_page[name, ev["gender"]]
                    events.setdefault(f"{ev['gender']}|{name}" + (f" ({n})" if n > 1 else ""), []).extend(rows[:KEEP])
        day += 1
    return events


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--budget", type=int, default=120, help="results pages to read in this run")
    ap.add_argument("--years", type=int, default=3, help="past seasons to keep results for")
    ap.add_argument("--delay", type=float, default=2.0)
    a = ap.parse_args()
    today = date.today()
    store = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    meets = store.get("meets", {})
    for m in meets.values():
        m.setdefault("tour", "ct")  # stored before the other tours were added
    done_cal = store.setdefault("calendar_done", {})
    for k in [k for k in done_cal if ":" not in k]:
        done_cal[f"ct:{k}"] = done_cal.pop(k)
    before = json.dumps(meets, sort_keys=True)
    with httpx.Client(headers=UA, timeout=40, follow_redirects=True) as client:
        for tour in TOURS:
            for year in range(today.year - a.years + 1, today.year + 2):
                if year < today.year and done_cal.get(f"{tour}:{year}"):
                    continue  # a past season's calendar no longer changes
                got = calendar(client, tour, f"{year}-01-01", f"{year}-12-31", a.delay)
                if got is None:
                    log(f"calendar {tour} {year}: nothing, or could not be read")
                    continue
                log(f"calendar {tour} {year}: {len(got)} competitions")
                for mid, m in got.items():
                    m["events"] = meets.get(mid, {}).get("events")
                    m["read"] = meets.get(mid, {}).get("read")
                    meets[mid] = m
                if year < today.year and got:
                    done_cal[f"{tour}:{year}"] = today.isoformat()
        # results: finished meetings not read yet, most recent first; a meeting read within 10 days of its end is read
        # once more later, in case results were completed
        todo = [mid for mid, m in meets.items() if m["has_results"] and m["end"] < today.isoformat()
                and (m.get("events") is None or (m.get("read") and m["read"] < (date.fromisoformat(m["end"]) + timedelta(days=10)).isoformat()
                                                 and today > date.fromisoformat(m["end"]) + timedelta(days=10)))]
        todo.sort(key=lambda mid: meets[mid]["end"], reverse=True)
        left = {t: sum(1 for mid in todo if meets[mid]["tour"] == t) for t in TOURS}
        log(f"{len(todo)} results pages to read ({', '.join(f'{t} {n}' for t, n in left.items())}), {min(len(todo), a.budget)} in this run")
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
                  "_about": "Competitions in Europe of the World Athletics Continental Tour (ct), Indoor Tour (indoor), Label Road Races "
                            "(road) and Cross Country Tour (xc), from the public calendar and results pages of worldathletics.org. Per "
                            "competition: tour, level, dates, place, and per event [mark, place, race] of the results (best 80). "
                            "No names. Written by meets_data.py, do not edit by hand."})
    OUT.write_text(json.dumps(store, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    with_results = sum(1 for m in meets.values() if m.get("events"))
    log(f"wrote {OUT.name}: {len(meets)} competitions, {with_results} with results")


if __name__ == "__main__":
    main()
