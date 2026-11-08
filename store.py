#!/usr/bin/env python3
"""
Keep athletics.db between GitHub Actions runs, encrypted, as a file attached to the release tagged "data".

  python store.py download    newest stored copy -> athletics.db (does nothing on the very first run)
  python store.py upload      athletics.db -> new encrypted copy; keeps the 2 newest, deletes older ones

Why a release file: the repository must be public for free GitHub Pages, and the database holds full
birth dates and under-18 athletes, so it is only ever stored encrypted (AES-256 with the SITE_KEY secret).
Release files may be up to 2 GB and are not part of the git history, so the repository stays small.
A new copy is uploaded before an old one is deleted, so a failed upload never loses data.

Needs: the gh command (preinstalled on GitHub runners) with GH_TOKEN, gpg, and the SITE_KEY variable.
"""
import gzip
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TAG = "data"
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


def download():
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
    {"download": download, "upload": upload}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
