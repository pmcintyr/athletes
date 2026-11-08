#!/usr/bin/env python3
"""
Build the static athlete-profile site from athletics.db (the database made by alabus_scraper.py).

  python build_site.py --db athletics.db
  python build_site.py --db athletics.db --base-url https://athletes.mcintyre.ch/

Output (site/ by default) is plain static files for GitHub Pages: no server, no database online.

  index.html, app.js, style.css   the interactive site (search, profiles, head-to-head, lists)
  a/<xyz>.json                    athlete profiles, sharded by the first 3 characters of the public id
  idx/<ab>.json                   search index, sharded by the first 2 letters of each name part
  lists/<year>.json               top N per discipline, sex, indoor/outdoor and season
  p/<id>.html                     plain HTML page per recently active athlete, for Google and link previews
  sitemap*.xml, robots.txt        written when --base-url is given
  meta.json                       disciplines, seasons, counts, build date

Memory: the heavy work (merging, de-duplicating, sorting) runs inside SQLite on disk and athletes are
streamed one by one, so millions of results build in well under 1 GB of RAM.

Privacy defaults
  * Birth YEAR only. The full birth date never leaves the database.
  * Athletes younger than --min-age (default 18) in the current year are left out completely.
  * removals.txt lists public ids (the 10 characters after /a/ or /p/ in a link) to leave out.
  * Public ids are salted hashes. The Alabus athlete_key is sha1(name|birth date), so publishing it
    would reveal the birth date. The salt is in .site_salt (on GitHub it comes from the SITE_KEY secret).

No extra packages needed (standard library only).
"""
import argparse
import hashlib
import html
import json
import re
import secrets
import shutil
import sqlite3
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Same lists as alabus_scraper.py. Edit after checking the real meeting names in your data.
CHAMPIONSHIP_PATTERNS = ["schweizer meisterschaft", "championnats suisses", "campionati svizzeri", "swiss championships"]
# Alabus mostly writes "SM Aktive", "SM Halle Nachwuchs (WRC)", "SM U20/U23", "Masters-SM ...", "Team SM".
SM_RE = re.compile(r"(^|[\s/(+])(masters[\s-]?)?sm([\s-]|$)")
NOT_SWISS = ("kilpail", "saul")  # Finnish "SM-kilpailut" are Finnish championships
# "Hallenmeeting inkl. SM Halle Mehrkampf": only the combined events of such a meeting are championships.
SM_MK_RE = re.compile(r"(masters[\s-]?)?sm[\s-]*(halle[\s-]*)?(mehrkampf|mk)\b")
COLS = ["indoor", "disci_id", "discipline", "sex", "year", "mark_raw", "mark_value", "hand_timed", "lower_better",
        "place", "round_code", "name", "club", "nat", "birth_date", "meeting", "location", "date", "athlete_key", "wind"]


# ---------- labels and ordering (copied from alabus_scraper.py so both stay in step) ----------
def fr_label(label):
    l = str(label)
    m = re.match(r"^(Kugel|Diskus|Hammer|Gewicht)\s+([\d.]+)$", l)
    if m:
        n = {"Kugel": "Poids", "Diskus": "Disque", "Hammer": "Marteau", "Gewicht": "Poids lesté"}[m.group(1)]
        return f"{n} {float(m.group(2)):.2f} kg"
    m = re.match(r"^Speer\s+(\d+)$", l)
    if m:
        return f"Javelot {m.group(1)} g"
    m = re.match(r"^(\d+) mH ([\d.]+)$", l)
    if m:
        return f"{m.group(1)} m haies {m.group(2)}"
    simple = {"Hoch": "Hauteur", "Stab": "Perche", "Weit": "Longueur", "Drei": "Triple", "1 Meile": "1 mile"}
    if l in simple:
        return simple[l]
    for g, f in (("Siebenkampf", "Heptathlon"), ("Zehnkampf", "Décathlon"), ("Fünfkampf", "Pentathlon")):
        if l.startswith(g):
            return f + l[len(g):]
    return l.replace("Steeple", "steeple")


def discipline_order(label):
    l = str(label)
    num = re.search(r"[\d.]+", l)
    n = float(num.group(0)) if num else 0.0
    if re.match(r"^\d+\s*x", l, re.I) or "Staffel" in l or "relais" in l.lower():
        g = 5
    elif "kampf" in l.lower() or "Athletic-Cup" in l:
        g = 4
    elif l.startswith(("Kugel", "Diskus", "Speer", "Hammer", "Gewicht")):
        g = 3
    elif l in ("Hoch", "Stab", "Weit", "Drei"):
        g = 2
    elif "mH" in l or "Steeple" in l:
        g = 1
    else:
        g = 0
    return (g, n, l)


def is_wind_event(label, indoor):
    return (not indoor) and (label in ("100 m", "200 m", "Weit", "Drei") or bool(re.match(r"^(80|100|110) mH", str(label))))


def norm_name(x):
    n = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower()
    return " ".join(sorted(re.sub(r"[^a-z ]", " ", n).split()))


def name_tokens(x):
    n = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]+", " ", n).split()


def slug(x):
    return "-".join(name_tokens(x))


def mark_text(raw):
    """'10.90_SR' -> '10.90'; combined events and relays keep only the first token (total or time)."""
    base = str(raw or "").partition("_")[0].strip()
    return base.split()[0] if base else ""


def s(v):
    return "" if v is None or str(v).lower() in ("nan", "none", "nat") else str(v)


def r1(v, nd=1):
    return None if v is None else round(float(v), nd)


def is_championship(m):
    return any(p in m for p in CHAMPIONSHIP_PATTERNS) or bool(SM_RE.search(m))


def is_medal(meeting, place, round_code, label=""):
    m = s(meeting).lower()
    if any(x in m for x in NOT_SWISS) or not is_championship(m):
        return False
    if not is_championship(SM_MK_RE.sub(" ", m)) and "kampf" not in s(label).lower():
        return False  # the meeting's only championship is a combined-events one, and this is not a combined event
    if place is None or not 1 <= int(place) <= 3:
        return False
    rc = s(round_code).lower()
    return rc == "" or rc.startswith("f")  # finals only: heats (h) and series (r) are not medals


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


# ---------- database preparation (all inside SQLite, on disk) ----------
def prepare(con, min_age):
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    parts = []
    for prio, t in enumerate(("allresults", "bestlist")):
        if t not in have:
            continue
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
        sel = ", ".join(c if c in cols else f"NULL AS {c}" for c in COLS)
        parts.append(f"SELECT {prio} AS src, {sel} FROM {t}")
    if not parts:
        sys.exit("no 'allresults' or 'bestlist' table in the database")
    max_by = datetime.now().year - min_age
    con.execute("PRAGMA temp_store=FILE")
    con.execute("DROP TABLE IF EXISTS temp.r")
    # One row per real result: the same result can sit in both tables (allresults wins, it carries wind).
    # SQLite takes the other columns from the row with MIN(src).
    con.execute(f"""
        CREATE TEMP TABLE r AS
        SELECT MIN(src) AS src, {", ".join(COLS)},
               CAST(substr(birth_date, 1, 4) AS INTEGER) AS by,
               COALESCE(CAST(substr(date, 1, 4) AS INTEGER), CAST(year AS INTEGER)) AS season
        FROM ({" UNION ALL ".join(parts)})
        WHERE mark_value IS NOT NULL AND athlete_key IS NOT NULL AND birth_date GLOB '[12][0-9][0-9][0-9]*'
        GROUP BY indoor, disci_id, sex, athlete_key, mark_raw, meeting, date""")
    total = con.execute("SELECT COUNT(DISTINCT athlete_key) FROM r").fetchone()[0]
    con.execute("DELETE FROM r WHERE by > ?", (max_by,))
    adults = con.execute("SELECT COUNT(DISTINCT athlete_key) FROM r").fetchone()[0]
    return have, total - adults


def public_ids(con, salt_path, removed_ids, removed_names):
    p = Path(salt_path)
    if not p.exists():
        p.write_text(secrets.token_hex(16), encoding="utf-8")
        print(f"new salt written to {p}: keep this file (stable links) and never commit it")
    salt = p.read_text(encoding="utf-8").strip()
    con.execute("DROP TABLE IF EXISTS temp.ath")
    con.execute("CREATE TEMP TABLE ath (athlete_key TEXT PRIMARY KEY, pid TEXT)")
    rows, gone = [], 0
    for key, name, by in con.execute("SELECT athlete_key, MAX(name), MAX(by) FROM r GROUP BY athlete_key"):
        pid = hashlib.sha1(f"{salt}|{key}".encode()).hexdigest()[:10]
        if pid in removed_ids or (norm_name(name), str(by)) in removed_names:
            gone += 1
            continue
        rows.append((key, pid))
    con.executemany("INSERT INTO ath VALUES (?, ?)", rows)
    con.execute("CREATE INDEX temp.ath_pid ON ath(pid)")
    return len(rows), gone


def disciplines(con):
    rows = con.execute("SELECT indoor, discipline, MAX(lower_better) FROM r GROUP BY indoor, discipline").fetchall()
    rows.sort(key=lambda x: (x[0], discipline_order(x[1])))
    con.execute("DROP TABLE IF EXISTS temp.disc")
    con.execute("CREATE TEMP TABLE disc (indoor INT, discipline TEXT, d INT, PRIMARY KEY (indoor, discipline))")
    con.executemany("INSERT INTO disc VALUES (?, ?, ?)", [(i, d, n) for n, (i, d, _) in enumerate(rows)])
    return [[int(i), d, fr_label(d), int(lb), int(is_wind_event(d, i))] for i, d, lb in rows]


def read_removals(ids_path, names_path):
    ids, names = set(), set()
    if ids_path and Path(ids_path).exists():
        for line in Path(ids_path).read_text(encoding="utf-8").splitlines():
            m = re.search(r"\b([0-9a-f]{10})\b", line.split("#")[0])
            if m:
                ids.add(m.group(1))
    if names_path and Path(names_path).exists():
        for line in Path(names_path).read_text(encoding="utf-8").splitlines()[1:]:
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 2 and parts[0]:
                names.add((norm_name(parts[0]), parts[1]))
    return ids, names


# ---------- one athlete ----------
def best_by(rows, keyf):
    """Best legal mark per key: electronic timing first (hand times only if no electronic one), earliest on a tie."""
    best = {}
    for r in rows:
        if r["assisted"]:
            continue
        k = keyf(r)
        rank = (r["hand"], r["sortv"], r["date"] or "9999")
        if k not in best or rank < best[k][0]:
            best[k] = (rank, r)
    return {k: v[1] for k, v in best.items()}


class Dict:
    """Per-shard string table: repeated meetings, venues and clubs are stored once and referenced by number."""
    def __init__(self):
        self.items, self.pos = [], {}

    def __call__(self, v):
        if v not in self.pos:
            self.pos[v] = len(self.items)
            self.items.append(v)
        return self.pos[v]


def athlete(rows, M, L, C):
    """rows: newest first. Meetings (M), venues (L) and clubs (C) become numbers into the shard's tables.
    Result rows carry the mark text only; the browser reads its numeric value from it."""
    latest = rows[0]
    clubs = list(dict.fromkeys(r["club"] for r in reversed(rows) if r["club"]))
    last = {}  # latest season per event: events still done come first, children's events of long ago last
    for r in rows:
        last.setdefault(r["d"], r["season"])
    pbr = sorted(best_by(rows, lambda r: r["d"]).values(), key=lambda r: (-int(last[r["d"]] or 0), r["d"]))
    pb = [[r["d"], mark_text(r["mark"]), r1(r["wind"]), r["date"], M(r["meeting"]), L(r["loc"]), r["hand"]] for r in pbr]
    sb = [[d, season, mark_text(r["mark"]), r1(r["value"], 3)]
          for (d, season), r in sorted(best_by(rows, lambda r: (r["d"], r["season"])).items())]
    medals, seen = [], set()
    for r in rows:
        if is_medal(r["meeting"], r["place"], r["round"], r["label"]):
            k = (r["season"], r["d"], r["meeting"])
            if k not in seen:
                seen.add(k)
                medals.append([r["season"], r["d"], r["place"], r["meeting"]])
    res = [[r["d"], r["date"], mark_text(r["mark"]), r1(r["wind"]), r["place"], r["round"], M(r["meeting"]),
            L(r["loc"]), C(r["club"]), r["assisted"]] for r in rows]
    prof = {"n": latest["name"], "by": latest["by"], "s": latest["sex"], "c": latest["club"], "cs": clubs[-6:],
            "nat": latest["nat"], "pb": pb, "sb": sb, "m": sorted(medals, reverse=True), "r": res}
    page = {"pb": [[r["d"], mark_text(r["mark"]), r1(r["wind"]), r["date"], r["meeting"], r["loc"]] for r in pbr]}
    return prof, page


# ---------- static HTML page per athlete (for search engines and link previews) ----------
PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} ({by}) – {title}</title><meta name="description" content="{desc}">{canonical}
<meta property="og:title" content="{name} ({by})"><meta property="og:description" content="{desc}"><link rel="stylesheet" href="../style.css"></head>
<body><header class="top"><div class="wrap top-in"><a class="brand" href="../#/">{title}</a></div></header><main class="wrap">
<section class="profile-head"><h1>{name}</h1><div class="facts"><span>{born} en <strong class="num">{by}</strong></span>{club}</div>
<p class="cta"><a class="more" href="../#/a/{pid}/{slug}">Progression, tous les résultats et confrontations</a></p></section>
<h2>Records personnels</h2><div class="cols">{lanes}</div>{medals}</main>
<footer class="foot"><div class="wrap"><p>Site indépendant et non officiel. Résultats publics de Swiss Athletics (Alabus), mis à jour le {updated}.{contact}</p></div></footer></body></html>
"""


def profile_page(pid, p, page, disc, title, base_url, contact, updated):
    e = html.escape
    names = {i: (d[2] + (" (salle)" if d[0] else "")) for i, d in enumerate(disc)}

    def lanes(rows, head):
        if not rows:
            return ""
        items = "".join(
            f'<li class="lane"><span class="ev">{e(disc[r[0]][2])}</span><span class="mk">{e(r[1])}'
            f'{f"<small>{r[2]:+.1f}</small>" if disc[r[0]][4] and r[2] is not None else ""}</span>'
            f'<span class="where">{fmt_date(r[3])}, {e(r[4])}</span></li>' for r in rows)
        return f'<div><h3>{head}</h3><ul class="lanes">{items}</ul></div>'

    out = [r for r in page["pb"] if disc[r[0]][0] == 0]
    ins = [r for r in page["pb"] if disc[r[0]][0] == 1]
    best = ", ".join(f"{names[r[0]]} {r[1]}" for r in (out + ins)[:5])
    desc = f"Records personnels de {p['n']}" + (f" ({p['c']})" if p["c"] else "") + (f" : {best}." if best else ".")
    medals = ""
    if p["m"]:
        li = "".join(f'<li><span class="medal p{m[2]}">{m[2]}</span><span><strong class="num">{m[0]}</strong> '
                     f'{e(names[m[1]])}, {e(m[3])}</span></li>' for m in p["m"])
        medals = f'<h2>Podiums aux championnats suisses</h2><ul class="medals">{li}</ul>'
    return PAGE.format(
        name=e(p["n"]), by=p["by"], title=e(title), desc=e(desc), pid=pid, slug=slug(p["n"]),
        canonical=f'<link rel="canonical" href="{e(base_url)}p/{pid}.html">' if base_url else "",
        born="Née" if p["s"] == "W" else "Né", club=f'<span>Club <strong>{e(p["c"])}</strong></span>' if p["c"] else "",
        lanes=lanes(out, "Plein air") + lanes(ins, "En salle") or '<p class="empty">Aucune performance régulière.</p>',
        medals=medals, updated=fmt_date(updated),
        contact=f' Corriger ou retirer ce profil : <a href="mailto:{e(contact)}">{e(contact)}</a>.' if contact else "")


def fmt_date(iso):
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s(iso))
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else s(iso)


# ---------- build ----------
def build(a):
    t0 = time.time()
    out = Path(a.out)
    if not Path(a.db).exists():
        sys.exit(f"{a.db} not found. Run alabus_scraper.py fetch first, or pass --db.")
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    have, minors = prepare(con, a.min_age)
    removed_ids, removed_names = read_removals(a.removals_ids, a.removals)
    n_ath, gone = public_ids(con, a.salt, removed_ids, removed_names)
    if not n_ath:
        sys.exit("nothing left to publish after the privacy filters")
    disc = disciplines(con)
    print(f"{n_ath:,} athletes to publish ({minors:,} under {a.min_age} left out, {gone} removed on request)")

    for sub in ("a", "idx", "lists", "p"):
        shutil.rmtree(out / sub, ignore_errors=True)
        (out / sub).mkdir(parents=True, exist_ok=True)
    for f in out.glob("sitemap*.xml"):
        f.unlink()
    updated = datetime.now().strftime("%Y-%m-%d")

    # Stream every result, sorted by public id, newest first: one athlete in memory at a time.
    q = """SELECT a.pid, d.d, r.date, r.mark_raw, r.mark_value, r.wind, r.place, r.round_code, r.meeting, r.location,
                  r.club, r.name, r.by, r.sex, r.nat, r.hand_timed, r.lower_better, r.indoor, r.discipline, r.season
           FROM r JOIN ath a ON a.athlete_key = r.athlete_key
                  JOIN disc d ON d.indoor = r.indoor AND d.discipline = r.discipline
           ORDER BY a.pid, r.date DESC, d.d"""
    shard, shard_id, cur, cur_pid = {}, None, [], None
    tables = [Dict(), Dict(), Dict()]

    def write_shard():
        dump(out / "a" / f"{shard_id}.json", {"_": [t.items for t in tables], **shard})
    index = defaultdict(list)
    n_res, pids = 0, []
    page_from = datetime.now().year - a.pages_years + 1

    def finish(pid, rows):
        nonlocal n_res
        prof, page = athlete(rows, *tables)
        n_res += len(rows)
        shard[pid] = prof
        entry = [pid, prof["n"], prof["by"], prof["c"], prof["s"]]
        for pref in {t[:2] for t in name_tokens(prof["n"]) if len(t) >= 2}:
            index[pref].append(entry)
        if max(r["season"] for r in rows) >= page_from:  # a plain page for Google only if active recently
            prof["pg"] = 1
            pids.append(pid)
            (out / "p" / f"{pid}.html").write_text(
                profile_page(pid, prof, page, disc, a.title, a.base_url, a.contact, updated), encoding="utf-8")

    for row in con.execute(q):
        (pid, d, date, mark, value, wind, place, rnd, meeting, loc, club, name, by, sex, nat, hand, lower, indoor,
         label, season) = row
        if pid != cur_pid:
            if cur:
                finish(cur_pid, cur)
            if shard_id is not None and pid[:3] != shard_id:
                write_shard()
                shard, tables[:] = {}, [Dict(), Dict(), Dict()]
            cur, cur_pid, shard_id = [], pid, pid[:3]
        cur.append({"d": d, "date": s(date), "mark": mark, "value": value, "wind": wind, "place": place,
                    "round": s(rnd), "meeting": s(meeting), "loc": s(loc), "club": s(club), "name": s(name),
                    "by": by, "sex": s(sex), "nat": s(nat), "hand": int(hand or 0), "season": int(season), "label": label,
                    "sortv": value if lower == 1 else -value,
                    "assisted": int(bool(is_wind_event(label, indoor) and wind is not None and wind > 2.0))})
    if cur:
        finish(cur_pid, cur)
    if shard:
        write_shard()
    for pref, entries in index.items():
        dump(out / "idx" / f"{pref}.json", sorted(entries, key=lambda e: e[1]))
    print(f"profiles written ({time.time() - t0:.0f}s)")

    # Season lists: best legal electronic mark per athlete, top N, equal marks share a rank.
    con.create_function("iswind", 2, lambda l, i: int(is_wind_event(l, i)), deterministic=True)
    lq = """
      WITH b AS (
        SELECT r.*, a.pid, d.d, CASE r.lower_better WHEN 1 THEN r.mark_value ELSE -r.mark_value END AS sortv
        FROM r JOIN ath a ON a.athlete_key = r.athlete_key
               JOIN disc d ON d.indoor = r.indoor AND d.discipline = r.discipline
        WHERE COALESCE(r.hand_timed, 0) = 0 AND NOT (iswind(r.discipline, r.indoor) AND COALESCE(r.wind, 0) > 2.0)),
      one AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY season, d, sex, athlete_key ORDER BY sortv, date) AS rn FROM b),
      ranked AS (SELECT *, ROW_NUMBER() OVER w AS pos, RANK() OVER w AS rk FROM one WHERE rn = 1
                 WINDOW w AS (PARTITION BY season, d, sex ORDER BY sortv))
      SELECT season, d, sex, rk, mark_raw, wind, pid, name, by, club, date, meeting FROM ranked
      WHERE pos <= ? ORDER BY season, sex, d, pos"""
    lists = defaultdict(dict)
    for season, d, sex, rk, mark, wind, pid, name, by, club, date, meeting in con.execute(lq, (a.list_n,)):
        lists[season].setdefault(s(sex), {}).setdefault(str(d), []).append(
            [rk, mark_text(mark), r1(wind), pid, s(name), by, s(club), s(date), s(meeting)])
    years = sorted(lists, reverse=True)
    for y in years:
        dump(out / "lists" / f"{y}.json", lists[y])

    first_year, last_result = con.execute("SELECT MIN(season), MAX(date) FROM r").fetchone()
    dump(out / "meta.json", {
        "title": a.title, "contact": a.contact, "min_age": a.min_age, "list_n": a.list_n, "generated": updated,
        "last_result": s(last_result), "first_year": first_year, "years": years, "athletes": n_ath,
        "results": n_res, "full_results": "allresults" in have, "disc": disc})

    # Static files, search engines, custom domain.
    for f in (HERE / "web").rglob("*"):
        if f.is_file():
            dest = out / f.relative_to(HERE / "web")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
    page = (out / "index.html").read_text(encoding="utf-8").replace("{{TITLE}}", html.escape(a.title))
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    if a.domain:
        (out / "CNAME").write_text(a.domain.strip() + "\n", encoding="utf-8")
    if a.base_url:
        base = a.base_url.rstrip("/") + "/"
        urls = [f"{base}p/{p}.html" for p in pids]
        maps = []
        for i in range(0, len(urls), 45000):
            name = f"sitemap-{i // 45000 + 1}.xml"
            body = "".join(f"<url><loc>{html.escape(u)}</loc><lastmod>{updated}</lastmod></url>" for u in urls[i:i + 45000])
            (out / name).write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                                    f'<url><loc>{base}</loc><lastmod>{updated}</lastmod></url>{body}</urlset>\n', encoding="utf-8")
            maps.append(name)
        (out / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<sitemap><loc>{base}{m}</loc><lastmod>{updated}</lastmod></sitemap>" for m in maps)
            + "</sitemapindex>\n", encoding="utf-8")
        (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}sitemap.xml\n", encoding="utf-8")

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    files = sum(1 for f in out.rglob("*") if f.is_file())
    print(f"built {out}/: {n_ath:,} athletes, {n_res:,} results, {len(years)} seasons, "
          f"{len(pids):,} plain pages, {files:,} files, {size / 1e6:.0f} MB, {time.time() - t0:.0f}s")
    if size > 900e6:
        print("WARNING: GitHub Pages sites are limited to 1 GB. Raise --min-age, lower --list-n or drop old seasons.")
        return 2
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="athletics.db")
    p.add_argument("--out", default="site")
    p.add_argument("--domain", default="", help="custom domain written to CNAME, e.g. athletes.mcintyre.ch")
    p.add_argument("--base-url", default="", help="full address, e.g. https://athletes.mcintyre.ch/ (for sitemaps and link previews)")
    p.add_argument("--title", default="Athlètes suisses")
    p.add_argument("--contact", default="paullucmcintyre@gmail.com", help="shown in the footer for corrections and removals")
    p.add_argument("--min-age", type=int, default=18, help="leave out athletes younger than this in the current year")
    p.add_argument("--list-n", type=int, default=20, help="athletes per season list")
    p.add_argument("--removals-ids", default="removals.txt", help="public ids to leave out, one per line")
    p.add_argument("--removals", default="removals.csv", help="optional name,birth_year file (keep it out of git)")
    p.add_argument("--pages-years", type=int, default=10,
                   help="write a plain HTML page (for Google and link previews) for athletes with a result in the last N seasons")
    p.add_argument("--salt", default=".site_salt")
    sys.exit(build(p.parse_args()))


if __name__ == "__main__":
    main()
