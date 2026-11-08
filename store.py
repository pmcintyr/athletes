#!/usr/bin/env python3
"""
Keep athletics.db between GitHub Actions runs, encrypted, as a file attached to the release tagged "data".

  python store.py download    newest stored copy -> athletics.db (does nothing on the very first run)
  python store.py upload      athletics.db -> new encrypted copy; keeps the 2 newest, deletes older ones
  python store.py drop-seed   delete the seed release once its database is stored encrypted

Seed: to start from a database made elsewhere (a laptop scrape), attach it xz-compressed and cut into parts
(seed.db.xz.part000, part001, ...) plus seed.sha256 to a DRAFT release tagged "seed". Drafts are only visible
to people with write access. `download` then takes the seed instead of the stored copy, the workflow saves it
encrypted, and `drop-seed` deletes the draft.

Why a release file: the repository must be public for free GitHub Pages, and the database holds full
birth dates and under-18 athletes, so it is only ever stored encrypted (AES-256 with the SITE_KEY secret).
Release files may be up to 2 GB and are not part of the git history, so the repository stays small.
A new copy is uploaded before an old one is deleted, so a failed upload never loses data.

Needs: the gh command (preinstalled on GitHub runners) with GH_TOKEN, gpg, and the SITE_KEY variable.
"""
import gzip
import hashlib
import json
import lzma
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TAG = "data"
SEED = "seed"
DB = Path(os.environ.get("DB_FILE", "athletics.db"))
SUFFIX = ".db.gz.gpg"
KEEP = 2
NOTES = ("Encrypted database used by the update workflow (AES-256, key in the SITE_KEY secret). "
         "It is not readable without the key. Do not delete this release.")


def gh(*args, check=True):
    return subprocess.run(["gh", *args], check=check, capture_output=True, text=True)


def key():
    k = os.environ.get("SITE_KEY", "")
    if len(k) < 16:
        sys.exit("SITE_KEY is missing or too short. Add it under Settings > Secrets and variables > Actions.")
    return k


def gpg(*args):
    r = subprocess.run(["gpg", "--batch", "--yes", "--quiet", "--pinentry-mode", "loopback", "--passphrase-fd", "0", *args],
                       input=key(), capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"gpg failed: {r.stderr.strip()}")


def stored():
    r = gh("release", "view", TAG, "--json", "assets", check=False)
    if r.returncode:
        return None  # no release yet
    assets = [x for x in json.loads(r.stdout)["assets"] if x["name"].endswith(SUFFIX)]
    return sorted(assets, key=lambda x: x["createdAt"])


def output(k, v):
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"{k}={v}\n")


def seed_release():
    """The draft release tagged "seed" and its parts, or None."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        return None
    r = gh("api", f"repos/{repo}/releases?per_page=100", check=False)
    if r.returncode:
        return None
    for rel in json.loads(r.stdout):
        if SEED in (rel.get("tag_name"), rel.get("name")):
            parts = sorted((a for a in rel["assets"] if a["name"].startswith("seed.db.xz.part")), key=lambda a: a["name"])
            sums = [a for a in rel["assets"] if a["name"] == "seed.sha256"]
            if parts:
                return rel, parts, (sums[0] if sums else None)
    return None


def asset_bytes(asset):
    repo = os.environ["GITHUB_REPOSITORY"]
    r = subprocess.run(["gh", "api", "-H", "Accept: application/octet-stream", f"repos/{repo}/releases/assets/{asset['id']}"],
                       check=True, capture_output=True)
    if len(r.stdout) != asset["size"]:
        sys.exit(f"{asset['name']}: got {len(r.stdout)} bytes, expected {asset['size']}")
    return r.stdout


def import_seed(found):
    rel, parts, sums = found
    digest = hashlib.sha256()
    with open("seed.db.xz", "wb") as out:
        for a in parts:
            b = asset_bytes(a)
            digest.update(b)
            out.write(b)
    if sums:
        want = asset_bytes(sums).decode().split()[0]
        if digest.hexdigest() != want:
            sys.exit(f"seed checksum mismatch: {digest.hexdigest()} != {want}")
    with lzma.open("seed.db.xz", "rb") as src, open(DB, "wb") as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    os.remove("seed.db.xz")
    con = sqlite3.connect(DB)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")} & {"bestlist", "allresults"}
    n = sum(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables)
    con.close()
    if not n:
        sys.exit("The seed database has no bestlist or allresults rows")
    print(f"Started from the seed database ({len(parts)} parts, {DB.stat().st_size / 1e6:.0f} MB, {n:,} results)")


def drop_seed():
    found = seed_release()
    if found:
        gh("api", "-X", "DELETE", f"repos/{os.environ['GITHUB_REPOSITORY']}/releases/{found[0]['id']}")
        print("Deleted the seed release: the database is now only stored encrypted.")


def download():
    found = seed_release()
    if found:
        import_seed(found)
        output("seeded", "true")
        return
    assets = stored()
    if not assets:
        print("No stored database yet: this run starts from an empty one.")
        return
    name = assets[-1]["name"]
    gh("release", "download", TAG, "--pattern", name, "--clobber")
    gpg("--output", "db.gz", "--decrypt", name)
    with gzip.open("db.gz", "rb") as src, open(DB, "wb") as dst:
        shutil.copyfileobj(src, dst)
    os.remove("db.gz")
    os.remove(name)
    print(f"Restored {DB} ({DB.stat().st_size / 1e6:.0f} MB) from {name}")


def upload():
    if not DB.exists():
        sys.exit(f"{DB} not found")
    name = f"athletics-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}{SUFFIX}"
    with open(DB, "rb") as src, gzip.open("db.gz", "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)
    gpg("--symmetric", "--cipher-algo", "AES256", "--compress-algo", "none", "--output", name, "db.gz")
    os.remove("db.gz")
    if stored() is None:
        gh("release", "create", TAG, "--title", "Encrypted data store", "--notes", NOTES, "--latest=false")
    gh("release", "upload", TAG, name)
    print(f"Uploaded {name} ({Path(name).stat().st_size / 1e6:.0f} MB)")
    for old in (stored() or [])[:-KEEP]:
        gh("release", "delete-asset", TAG, old["name"], "--yes")
        print(f"Deleted older copy {old['name']}")
    os.remove(name)


if __name__ == "__main__":
    {"download": download, "upload": upload, "drop-seed": drop_seed}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
