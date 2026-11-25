#!/usr/bin/env bash
# Builds the mod and produces dist/RankedMode-<version>.zip, ready to unzip into the game's mods/ folder.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(dirname "$here")"
version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$here/RankedMode/RankedMode.json")"
dotnet build "$here/RankedMode/RankedMode.csproj" -c Release "$@"
stage="$(mktemp -d)"
mkdir -p "$stage/RankedMode" "$root/dist"
cp "$here/RankedMode/bin/Release/net9.0/RankedMode.dll" "$here/RankedMode/RankedMode.json" "$stage/RankedMode/"
(cd "$stage" && python3 -m zipfile -c "$root/dist/RankedMode-$version.zip" RankedMode)
rm -rf "$stage"
echo "Wrote dist/RankedMode-$version.zip"
