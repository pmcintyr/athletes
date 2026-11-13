# Athlètes suisses

A free website with a profile for every adult Swiss athlete in the Alabus results database: personal
bests, progression by season, Swiss championship podiums, all results, head-to-head comparisons,
season best lists and a season planner. In the spirit of Stathletics. Everything runs on GitHub, for free, with no laptop.

```
Alabus ──> GitHub Actions, every night ──> build_site.py ──> GitHub Pages (pmcintyr.github.io/athletes)
             │  update_data.py + alabus_scraper.py
             └─ encrypted athletics.db in the "data" release (save / restore)
```

## How it runs

The workflow `.github/workflows/update.yml` starts every night at 03:17 (Swiss winter time):

1. Restores the database from the `data` release (encrypted with the `SITE_KEY` secret).
2. Runs `update_data.py`: refreshes the current season when it is 6 or more days old, then downloads
   missing past seasons (newest first) until 290 minutes are used. A new repository downloads its
   whole history this way over about two nights. With nothing to do, it stops without touching Alabus.
3. Saves the database again (keeps the 2 newest copies) and records `status.json` in the repository.
4. Builds the site with `build_site.py` and publishes it on GitHub Pages.

Changing the site code, `build_site.py`, `cadres.json`, `planner.json` or `removals.txt` on the main branch rebuilds and publishes
the site from the stored data, without downloading anything.

## Everyday tasks (all from a phone)

| Task | How |
|---|---|
| See how up to date the data is | Open `status.json` in the repository |
| Remove an athlete on request | Copy the 10 characters after `/a/` or `/p/` in their link into `removals.txt`, commit |
| Download the current season now | Actions > Update data and site > Run workflow, tick "force refresh" |
| Rebuild the site only | Actions > Update data and site > Run workflow, mode "publish" |
| New cadre lists or limits (each autumn) | Edit `cadres.json`: replace the athletes rows, the dates and the PDF links (or ask Claude) |
| New championship dates or entry standards | Edit `planner.json`: the `championships` rows, with the link they were copied from |
| A meeting shows no travel estimate | The build log lists "venues without coordinates": add them under `venues` in `planner.json` |
| Something failed | GitHub emails you; the run page shows the reason at the bottom of the step that failed |

## Privacy

* Only the birth year is published, never the full date. Athletes under 18 are left out entirely.
* The full database (birth dates, minors) is only ever stored encrypted (AES-256). Without the
  `SITE_KEY` secret the release file is unreadable.
* Public ids are salted hashes, so a link reveals nothing about the birth date.
* Never change or delete `SITE_KEY`: the stored database could no longer be opened and every link
  would change. If it happens anyway, delete the `data` release and the history downloads again.

## Files

| File | What it does |
|---|---|
| `alabus_scraper.py` | Downloads and parses Alabus best lists (one page per second) |
| `update_data.py` | Decides what to download, resumes after the time limit, stops if Alabus is down |
| `road_data.py` | Road races of Swiss athletes (5 km to marathon) from the World Athletics season top lists, which only include certified courses |
| `store.py` | Encrypts and saves the database to the `data` release, and restores it |
| `build_site.py` | Turns the database into the static site (streams from SQLite, about 140 MB of memory) |
| `web/` | The page itself: `index.html`, `app.js`, `style.css`, fonts |
| `make_demo_db.py` | Fake database to preview the site without real data |
| `planner.json` | Season planner: championships to pick as objectives, their entry standards, departure cities, venue coordinates the lookup misses, Omio link settings |
| `cadres.json` | Official Swiss Athletics cadre lists (Swiss Starters, Swiss Starters Future) and Swiss Starters limits, shown on profiles and the Cadres page |

## Season planner (`#/plan`)

An athlete picks an event, a current level, a target mark, a departure city and one or more objectives
(Swiss championships, international championships with their entry standards, or any date). For each objective
the page proposes races in three windows: 7 to 3 weeks before, 3 weeks to 6 days before, and up to 3 weeks after.

* Races come from the results themselves: `build_site.py` writes, per discipline, the marks run at every meeting
  of the last 3 seasons (`plan/<d>.json`, numbers only, adults only). A meeting ranks higher when more marks
  there fall between the athlete's level and target. Meetings abroad appear because Alabus holds the results of
  Swiss-licensed athletes abroad; a meeting no Swiss athlete entered is unknown to the planner.
* Next year's dates are the usual weekday of the last edition, shown as "to be confirmed": there is no
  calendar feed yet.
* Travel time and cost are estimates from the straight-line distance (no timetable, no fares), used to filter
  "reachable in a weekend": train for races in Switzerland; train and, from 350 km, plane for races abroad,
  the faster of the two counting for the filter. The formulas are in `travel()` in `web/app.js`. The route link opens Google Maps; the Omio link opens a prefilled search only
  once city ids from Omio's affiliate Deep Link Creator file are added under `omio.ids` in `planner.json`
  (`omio.params` takes the affiliate marker).
* Choices are kept in the visitor's browser only.

## One-time setup (already done for pmcintyr/athletes)

1. Repository secret `SITE_KEY`: Settings > Secrets and variables > Actions > New repository secret,
   any long random text (40+ characters). Without it the workflow skips with a warning.
2. Settings > Pages > Source: **GitHub Actions**.

## Address

The site is published at `https://pmcintyr.github.io/athletes/`. `athletes.mcintyre.ch` is a subdomain in
the mcintyre.ch cPanel (Swizzonic) with a temporary (302) wildcard redirect to that address, so
`athletes.mcintyre.ch/p/<id>.html` lands on the same page on github.io.

To serve the site on `athletes.mcintyre.ch` itself (the address bar then keeps it):

1. Swizzonic Espace Client > mcintyre.ch > Domaine et DNS > Configuration DNS: replace the `A` record of
   `athletes` with a `CNAME` to `pmcintyr.github.io`. (cPanel cannot do this: its DNS editor is disabled.)
2. cPanel > Redirects: delete the athletes.mcintyre.ch redirect.
3. Settings > Pages > Custom domain: `athletes.mcintyre.ch`, then tick **Enforce HTTPS** once the check passes.
4. In `.github/workflows/update.yml`, set `SITE_URL: https://athletes.mcintyre.ch/` and commit.

## Preview locally (optional)

```bash
pip install -r requirements.txt
python make_demo_db.py --out demo.db
python build_site.py --db demo.db
cd site && python -m http.server 8000      # then open http://localhost:8000
```

## Known limits

* Podiums are read from meeting names ("SM Aktive", "SM Halle Nachwuchs", "Schweizer Meisterschaften",
  see `is_medal` in `build_site.py`) and a rank of 1 to 3 in a final. At meetings whose only championship
  is a combined-events one ("Hallenmeeting inkl. SM Halle Mehrkampf"), only the combined events count.
* Road results come from the World Athletics top lists: certified courses only, and only down to each list's
  level limit (e.g. 2:37:00 for the men's marathon). World Athletics' terms require written permission to reuse
  their data; Paul decided to go ahead and takes responsibility. If they object, set `ROAD_BUDGET_MIN` to 0 and
  delete the `road` table.
* An athlete is identified by name and birth date, as in the scraper: a spelling change creates two profiles.
* Alabus lists stop at 5 000 rows; the update log says "cap reached" when a list is cut.
* GitHub Pages sites are limited to 1 GB; the build warns above 900 MB (a realistic test with
  4 million results gave 350 MB).
