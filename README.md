# Athlètes suisses

A free website with a profile for every adult Swiss athlete in the Alabus results database: personal
bests, progression by season, Swiss championship podiums, all results, head-to-head comparisons and
season best lists. In the spirit of Stathletics. Everything runs on GitHub, for free, with no laptop.

```
Alabus ──> GitHub Actions, every night ──> build_site.py ──> GitHub Pages (athletes.mcintyre.ch)
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

Changing the site code, `build_site.py` or `removals.txt` on the main branch rebuilds and publishes
the site from the stored data, without downloading anything.

## Everyday tasks (all from a phone)

| Task | How |
|---|---|
| See how up to date the data is | Open `status.json` in the repository |
| Remove an athlete on request | Copy the 10 characters after `/a/` or `/p/` in their link into `removals.txt`, commit |
| Download the current season now | Actions > Update data and site > Run workflow, tick "force refresh" |
| Rebuild the site only | Actions > Update data and site > Run workflow, mode "publish" |
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
| `store.py` | Encrypts and saves the database to the `data` release, and restores it |
| `build_site.py` | Turns the database into the static site (streams from SQLite, about 140 MB of memory) |
| `web/` | The page itself: `index.html`, `app.js`, `style.css`, fonts |
| `make_demo_db.py` | Fake database to preview the site without real data |

## One-time setup (already done for pmcintyr/athletes)

1. Repository secret `SITE_KEY`: Settings > Secrets and variables > Actions > New repository secret,
   any long random text (40+ characters).
2. Settings > Pages > Source: **GitHub Actions**. Custom domain: `athletes.mcintyre.ch`, then tick
   **Enforce HTTPS** once the DNS check passes.
3. At the DNS host of mcintyre.ch: a `CNAME` record, name `athletes`, value `pmcintyr.github.io`.

## Preview locally (optional)

```bash
pip install -r requirements.txt
python make_demo_db.py --out demo.db
python build_site.py --db demo.db
cd site && python -m http.server 8000      # then open http://localhost:8000
```

## Known limits

* Podiums are guessed from meeting names (`CHAMPIONSHIP_PATTERNS` in `build_site.py`) and a final
  rank of 1 to 3. Check the real Swiss championship meeting names and adjust.
* An athlete is identified by name and birth date, as in the scraper: a spelling change creates two profiles.
* Alabus lists stop at 5 000 rows; the update log says "cap reached" when a list is cut.
* GitHub Pages sites are limited to 1 GB; the build warns above 900 MB (a realistic test with
  4 million results gave 350 MB).
