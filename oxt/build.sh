#!/bin/bash
# Assemble the BookSmart .book import filter into an installable .oxt.
#
# Bundles the static extension files (this dir) together with the parser and
# UNO backend from the repo root (bookxml.py, unobuild.py), so the installed
# extension is self-contained.  Install with:  unopkg add booksmart-import.oxt
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OUT="$HERE/booksmart-import.oxt"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# static extension files
mkdir -p "$STAGE/META-INF"
cp "$HERE/META-INF/manifest.xml" "$STAGE/META-INF/"
cp "$HERE/description.xml" "$HERE/description-en-US.txt" \
   "$HERE/Types.xcu" "$HERE/Filters.xcu" \
   "$HERE/booksmart_filter.py" "$STAGE/"

# the parser + UNO backend from the repo root
cp "$ROOT/bookxml.py" "$ROOT/unobuild.py" "$STAGE/"

rm -f "$OUT"
( cd "$STAGE" && zip -q -r -X "$OUT" . )
echo "Built $OUT"
