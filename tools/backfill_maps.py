#!/usr/bin/env python3
"""Rebuild act maps from the seed for ranked windows whose runs predate mod 0.4.0, and upload them.

Needs: this machine's STS2 install (tools/MapGen builds against it) and the server's admin key.

    dotnet build -c Release tools/MapGen
    RANKED_ADMIN_KEY=... python3 tools/backfill_maps.py [--server https://spire.ursumajor.com] [--force]

Map generation changes between game versions, so a window is skipped unless its runs were played on the same
game version as the local install (override with --force). Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAPGEN = ROOT / "tools" / "MapGen" / "bin" / "Release" / "net9.0" / "mapgen.dll"


def game_version() -> str | None:
    candidates = [
        Path.home() / ".local/share/Steam/steamapps/common/Slay the Spire 2/release_info.json",
        Path("C:/Program Files (x86)/Steam/steamapps/common/Slay the Spire 2/release_info.json"),
    ]
    for p in candidates:
        if p.is_file():
            return json.loads(p.read_text()).get("version")
    return None


def request(server: str, key: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(server.rstrip("/") + path, data=data, method=method, headers={
        "X-Admin-Key": key, "Content-Type": "application/json", "User-Agent": "STS2RankedBackfill/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read() or b"null")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default=os.environ.get("RANKED_SERVER_URL", "https://spire.ursumajor.com"))
    ap.add_argument("--force", action="store_true", help="ignore game version mismatches")
    args = ap.parse_args()
    key = os.environ.get("RANKED_ADMIN_KEY")
    if not key:
        print("Set RANKED_ADMIN_KEY.", file=sys.stderr)
        return 2
    if not MAPGEN.is_file():
        print(f"Build MapGen first: dotnet build -c Release tools/MapGen  ({MAPGEN} missing)", file=sys.stderr)
        return 2
    dotnet = shutil.which("dotnet") or str(Path.home() / ".dotnet" / "dotnet")
    local = game_version()

    todo = request(args.server, key, "GET", "/api/v1/admin/backfill")
    if not todo:
        print("Nothing to backfill.")
        return 0
    for item in todo:
        wid, rid = item["window_id"], item["run_id"]
        runs = request(args.server, key, "GET", f"/api/v1/admin/runs?window_id={wid}")
        played = {r["game_version"] for r in runs if r.get("game_version")}
        if not args.force and local and played and played != {local}:
            print(f"skip {wid}: played on {', '.join(sorted(played))}, local game is {local}")
            continue
        run = request(args.server, key, "GET", f"/api/v1/admin/runs/{rid}/json")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(run, f)
            path = f.name
        try:
            out = subprocess.run([dotnet, str(MAPGEN), path], capture_output=True, text=True, check=True).stdout
        finally:
            os.unlink(path)
        maps = json.loads(next(line for line in out.splitlines() if line.startswith("{")))["acts"]
        acts = {str(i): {"act_id": run["acts"][i]["id"], "map": m} for i, m in enumerate(maps)}
        request(args.server, key, "PUT", f"/api/v1/admin/windows/{wid}/maps", {"acts": acts})
        print(f"{wid}: uploaded {len(acts)} act maps (from run {rid[:8]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
