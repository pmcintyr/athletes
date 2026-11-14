#!/usr/bin/env python3
"""
Member clubs of Swiss Athletics with their town and canton, from the federation's club finder
(https://www.swiss-athletics.ch/vereinsfinder/). Writes clubs_swiss_athletics.json, which build_site.py uses as the
verified canton of each club.

  python clubs_data.py            read every page of the club finder (9 clubs per page, one page every 2 seconds)
  python clubs_data.py --max 5    first 5 pages only (to try it out)

The list changes a few times a year: the workflow "Club list" runs this once a week and commits the file when it
changed. If the page layout changes and fewer than 100 clubs are read, the existing file is kept.
"""
import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

START = "https://www.swiss-athletics.ch/vereinsfinder/"
OUT = Path(__file__).resolve().parent / "clubs_swiss_athletics.json"
CANTONS = {"AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR", "JU", "LU", "NE", "NW", "OW", "SG", "SH", "SO", "SZ",
           "TG", "TI", "UR", "VD", "VS", "ZG", "ZH", "FL"}


def parse(html):
    """-> ([name, postcode, town, canton], next page link or None)."""
    soup = BeautifulSoup(html, "lxml")
    clubs = []
    for item in soup.select("li.c-clubs-list__item"):
        h, p = item.find("h3"), item.select_one(".c-club-teaser__content p")
        if not h or not p:
            continue
        name, place = " ".join(h.get_text(" ").split()), " ".join(p.get_text(" ").split())
        m = re.match(r"^(\d{4})\s+(.*?)(?:,\s*([A-Z]{2}))?$", place)
        plz, town, canton = (m.group(1), m.group(2).strip(), m.group(3) or "") if m else ("", place, "")
        if name:
            clubs.append([name, plz, town, canton if canton in CANTONS else ""])
    more = next((a["href"] for a in soup.find_all("a", href=True)
                 if "kochclubfinder" in a["href"] and "currentPage" in a["href"]), None)
    return clubs, more


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--max", type=int, default=400, help="stop after this many pages")
    ap.add_argument("--delay", type=float, default=2.0)
    a = ap.parse_args()
    clubs, seen, url = [], set(), START
    with httpx.Client(headers={"User-Agent": "athletes.mcintyre.ch club list (non-commercial, weekly)"}, timeout=40,
                      follow_redirects=True) as client:
        for page in range(1, a.max + 1):
            try:
                r = client.get(url)
                r.raise_for_status()
            except httpx.HTTPError as e:
                print(f"page {page}: {e}", flush=True)
                break
            rows, more = parse(r.text)
            new = [c for c in rows if c[0] not in seen]
            seen.update(c[0] for c in new)
            clubs += new
            if page == 1 or page % 10 == 0:
                print(f"page {page}: {len(clubs)} clubs so far", flush=True)
            if not more or not new:
                break
            url = urljoin(START, more)
            time.sleep(a.delay)
    no_canton = [c[0] for c in clubs if not c[3]]
    print(f"{len(clubs)} clubs read, {len(no_canton)} without a canton: {', '.join(no_canton[:40])}")
    if len(clubs) < 100 and a.max >= 400:
        sys.exit("fewer than 100 clubs: the page may have changed, clubs_swiss_athletics.json was left as it is")
    clubs.sort(key=lambda c: c[0].lower())
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    if old.get("clubs") == clubs:
        print("no change")
        return
    OUT.write_text(json.dumps({"source": START, "fetched": date.today().isoformat(),
                               "_about": "Member clubs of Swiss Athletics as listed by its club finder: [name, postcode, town, canton]. "
                                         "Written by clubs_data.py, do not edit by hand (corrections go in geo.json).",
                               "clubs": clubs}, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"wrote {OUT.name}")


if __name__ == "__main__":
    main()
