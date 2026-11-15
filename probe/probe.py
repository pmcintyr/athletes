#!/usr/bin/env python3
"""Prints the structure of a few public pages (one request each, 2 seconds apart) so the scrapers can be written
against what the pages really contain. Development aid, not part of the nightly update."""
import json
import re
import time

import httpx
from bs4 import BeautifulSoup

UA = {"User-Agent": "athletes.mcintyre.ch (non-commercial, a few pages a week)"}


def shape(o, depth=0, max_depth=6, key=""):
    pad = "  " * depth
    if isinstance(o, dict):
        print(f"{pad}{key}{{{len(o)} keys}}")
        if depth < max_depth:
            for k, v in list(o.items())[:40]:
                shape(v, depth + 1, max_depth, f"{k}: ")
    elif isinstance(o, list):
        print(f"{pad}{key}[{len(o)} items]")
        if o and depth < max_depth:
            shape(o[0], depth + 1, max_depth, "0: ")
    else:
        print(f"{pad}{key}{json.dumps(o, ensure_ascii=False)[:160]}")


def page(client, url, params=None):
    print(f"\n===== {url} {params or ''}")
    try:
        r = client.get(url, params=params)
    except httpx.HTTPError as e:
        print("request failed:", e)
        return None
    print("status", r.status_code, "bytes", len(r.text), "final url", str(r.url)[:200])
    time.sleep(2)
    return r.text if r.status_code == 200 else None


def next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        print("no embedded page data")
        return None
    data = json.loads(m.group(1))
    print("embedded page data:")
    shape(data.get("props", {}).get("pageProps", {}), 1)
    return data


def tables(html):
    soup = BeautifulSoup(html, "lxml")
    for i, t in enumerate(soup.find_all("table")[:4]):
        rows = t.find_all("tr")
        print(f"table {i}: class={t.get('class')} rows={len(rows)}")
        for tr in rows[:4]:
            print("   ", [" ".join(c.get_text(" ").split())[:40] for c in tr.find_all(["th", "td"])], [a.get("href") for a in tr.find_all("a")][:3])
    return soup


# Put the pages to look at here, then start the "Probe public pages" workflow: it prints what they contain.
import collections

CAL = "https://worldathletics.org/competition/calendar-results"


def data(c, url, params=None):
    html = page(c, url, params)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html or "", re.S)
    return json.loads(m.group(1))["props"]["pageProps"] if m else None


with httpx.Client(headers=UA, timeout=40, follow_redirects=True) as c:
    pp = data(c, CAL, {"startDate": "2026-02-01", "endDate": "2026-02-07"})
    ev = (pp or {}).get("initialEvents") or {}
    groups = (ev.get("options") or {}).get("competitionGroups") or []
    print("competition groups:", json.dumps(groups, ensure_ascii=False))
    print("disciplines:", json.dumps((ev.get("options") or {}).get("disciplines"), ensure_ascii=False))
    for g in groups:
        if not re.search(r"Indoor Tour|Cross Country Tour|Label Road", g.get("name") or ""):
            continue
        pp = data(c, CAL, {"startDate": "2026-01-01", "endDate": "2026-12-31", "regionType": "area", "regionId": "3", "competitionGroupId": str(g["id"])})
        ev = (pp or {}).get("initialEvents")
        if not ev:
            print(g["name"], ": nothing")
            continue
        res = ev["results"]
        print(g["name"], "| hits", ev["hits"], "| levels", dict(collections.Counter(e["competitionSubgroup"] for e in res)),
              "| categories", dict(collections.Counter(e["rankingCategory"] for e in res)), "| disciplines", dict(collections.Counter(e["disciplines"] for e in res)))
        for e in res[:4]:
            print("   ", e["id"], e["startDate"], e["rankingCategory"], "|", e["competitionSubgroup"], "|", e["name"], "|", e["venue"], "|", e["disciplines"], "| results", e["hasResults"])
        for e in [x for x in res if x["hasResults"]][:2]:
            rp = data(c, f"{CAL}/results/{e['id']}")

            def find(o):
                if isinstance(o, dict):
                    if "eventTitles" in o:
                        return o
                    for v in o.values():
                        r = find(v)
                        if r:
                            return r
                elif isinstance(o, list):
                    for v in o:
                        r = find(v)
                        if r:
                            return r
                return None
            box = find(rp) if rp else None
            if not box:
                print("    no results data for", e["name"])
                continue
            print("    results of", e["name"], "| days", (box.get("options") or {}).get("days"))
            for t in box.get("eventTitles") or []:
                for x in t.get("events") or []:
                    races = x.get("races") or []
                    first = (races[0].get("results") or [{}])[0] if races else {}
                    print("      ", x.get("gender"), "|", x.get("event"), "| races", [(r.get("race"), len(r.get("results") or [])) for r in races][:4],
                          "| first", first.get("mark"), first.get("place"))
