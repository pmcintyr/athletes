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
    html = page(c, "https://worldathletics.org/competition/calendar-results", {"startDate": "2026-05-25", "endDate": "2026-05-31"})
    if html:
        tables(html)
        next_data(html)
    html = page(c, "https://worldathletics.org/competitions/world-athletics-continental-tour")
    if html:
        soup = tables(html)
        next_data(html)
        print("links to documents:", [(a.get_text(" ", strip=True)[:60], a.get("href")) for a in soup.find_all("a", href=True)
                                      if re.search(r"\.pdf|/download|requirements|calendar", a["href"], re.I)][:30])
    html = page(c, "https://www.swiss-athletics.ch/vereinsfinder/")
    if html:
        soup = BeautifulSoup(html, "lxml")
        more = [a for a in soup.find_all("a", href=True) if "kochclubfinder" in a["href"]]
        print("club finder links:", len(more), [(" ".join(a.get_text(" ").split())[:30], a["href"][:400]) for a in more[:3]])
        hit = soup.find(string=re.compile("Aarau Track Club"))
        node = hit.parent if hit else None
        for _ in range(4):
            if node is not None and node.parent is not None:
                node = node.parent
        print("club entry markup:", str(node)[:2500] if node is not None else "not found")
