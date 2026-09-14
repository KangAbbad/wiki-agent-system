#!/bin/sh
set -eu

plugin_root=${1:-plugins/wiki-preflight}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
workspace="$test_root/workspace"
mkdir "$workspace"
printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"SessionStart\"}" | "$plugin_root/hooks/launcher.sh" "$plugin_root/hooks/preflight.py" >/dev/null
mkdir -p "$workspace/.wiki/inbox/autosave" "$workspace/.wiki/.sessions/state" "$workspace/.wiki/raw"
printf 'expired autosave\n' >"$workspace/.wiki/inbox/autosave/old.md"
printf 'expired state\n' >"$workspace/.wiki/.sessions/state/old.json"
printf 'canonical evidence\n' >"$workspace/.wiki/raw/keep.md"
touch -t 202001010000 "$workspace/.wiki/inbox/autosave/old.md" "$workspace/.wiki/.sessions/state/old.json" "$workspace/.wiki/raw/keep.md"

"$plugin_root/hooks/launcher.sh" "$plugin_root/scripts/retention.py" "$workspace" --max-bytes 1 >"$test_root/report.json"
grep -q '"level": "block"' "$test_root/report.json"
grep -q 'old.md' "$test_root/report.json"
grep -q '"raw"' "$test_root/report.json"
test -f "$workspace/.wiki/raw/keep.md"
"$plugin_root/hooks/launcher.sh" "$plugin_root/scripts/retention.py" "$workspace" --apply --max-bytes 1 >"$test_root/apply.json"
test -f "$workspace/.wiki/.trash/autosave/old.md"
test -f "$workspace/.wiki/.trash/state/state/old.json"
test -f "$workspace/.wiki/raw/keep.md"

printf '%s\n' 'quota retention passed'
