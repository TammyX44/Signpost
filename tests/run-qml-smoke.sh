#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
shell_dir=${OMARCHY_SHELL_DIR:-/usr/share/omarchy/shell}
stage=$(mktemp -d /tmp/signpost-qml-smoke.XXXXXX)
trap 'rm -rf "$stage"' EXIT HUP INT TERM
ln -s "$shell_dir/Commons" "$stage/Commons"
ln -s "$shell_dir/Ui" "$stage/Ui"
for entry in "$repo"/*.qml; do ln -s "$entry" "$stage/${entry##*/}"; done
for dir in components assets i18n; do ln -s "$repo/$dir" "$stage/$dir"; done
cp "$repo/tests/qml-smoke.qml" "$stage/shell.qml"
# Quickshell keeps running after the check, so wait for the summary line and then stop it.
QT_QUICK_BACKEND=software qs --no-color -p "$stage/shell.qml" > "$stage/result.log" 2>&1 &
pid=$!
for _ in $(seq 100); do
  grep -q 'QML_SMOKE_TOTAL' "$stage/result.log" && break
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.2
done
kill "$pid" 2>/dev/null || true
wait "$pid" 2>/dev/null || true
grep -E 'QML_SMOKE' "$stage/result.log" || tail -n 30 "$stage/result.log"
grep -q 'QML_SMOKE_TOTAL 8 files, 0 failed' "$stage/result.log"
