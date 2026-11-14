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


with httpx.Client(headers=UA, timeout=40, follow_redirects=True) as c:
    CAL = "https://worldathletics.org/competition/calendar-results"
    html = page(c, CAL, {"startDate": "2026-05-25", "endDate": "2026-05-31"})
    data = next_data(html) if html else None
    if data:
        ev = data["props"]["pageProps"]["initialEvents"]
        for k in ("regions", "disciplines", "rankingCategories", "permitLevels", "competitionGroups"):
            print(k, json.dumps(ev["options"][k], ensure_ascii=False)[:3000])
    def events(params):
        html = page(c, CAL, params)
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html or "", re.S)
        ev = json.loads(m.group(1))["props"]["pageProps"].get("initialEvents") if m else None
        if not ev:
            print("no events in the page")
            return []
        print("hits", ev["hits"], "returned", len(ev["results"]), "parameters", json.dumps({k: v for k, v in ev["parameters"].items() if v not in (None, False)}))
        for e in ev["results"][:5]:
            print("  ", e["id"], e["startDate"], e["rankingCategory"], "|", e["competitionGroup"], "|", e["competitionSubgroup"], "|", e["name"], "|", e["venue"], "| results", e["hasResults"])
        return ev["results"]

    EU = {"regionType": "area", "regionId": "3", "competitionGroupId": "3773"}
    events({"startDate": "2026-05-01", "endDate": "2026-08-31", **EU})
    events({"startDate": "2026-05-01", "endDate": "2026-08-31", **EU, "offset": "100"})
    events({"startDate": "2027-01-01", "endDate": "2027-12-31", **EU})
    res = events({"startDate": "2026-05-30", "endDate": "2026-05-31", **EU})
    pick = next((e for e in res if e["hasResults"]), None)
    if pick:
        print("results page for", pick["name"], pick["id"])
        for url in (f"{CAL}/results/{pick['id']}",):
            html = page(c, url)
            if html:
                soup = tables(html)
                d = next_data(html)
                print("headings:", [" ".join(h.get_text(" ").split())[:60] for h in soup.find_all(["h1", "h2", "h3"])][:30])
                if d:
                    pp = d["props"]["pageProps"]
                    for k, v in pp.items():
                        if isinstance(v, (dict, list)) and v:
                            print("--", k)
                            shape(v, 1, 9)
