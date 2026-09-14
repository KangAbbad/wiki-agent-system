#!/bin/sh
set -eu

fail() { printf '%s\n' "FAIL: $*" >&2; exit 1; }
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
mkdir -p "$root/codex" "$root/home" "$root/workspace"

export HOME="$root/home" CODEX_HOME="$root/codex"
marketplace_source=${WIKI_MARKETPLACE_SOURCE:-KangAbbad/wiki-agent-system}
marketplace_ref=${WIKI_MARKETPLACE_REF-main}
if [ -n "$marketplace_ref" ]; then
  codex plugin marketplace add "$marketplace_source" --ref "$marketplace_ref" --json >"$root/marketplace.json"
else
  codex plugin marketplace add "$marketplace_source" --json >"$root/marketplace.json"
fi
codex plugin add wiki-preflight@wiki-agent-system --json >"$root/install.json"
codex plugin list --marketplace wiki-agent-system --json >"$root/list.json"

grep -q '"pluginId": "wiki-preflight@wiki-agent-system"' "$root/list.json" || fail "marketplace install did not register wiki-preflight"
plugin=$(find "$CODEX_HOME/plugins/cache/wiki-agent-system/wiki-preflight" -path '*/hooks/preflight.py' -type f | head -n 1)
test -n "$plugin" || fail "installed cache has no preflight hook"
case "$plugin" in "$PWD"/*) fail "test used source tree instead of installed cache";; esac
plugin_root=$(dirname "$plugin")
launcher="$plugin_root/launcher.sh"
test -x "$launcher" || fail "installed cache has no launcher"
python3 - "$plugin_root/../defaults/ambient.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
assert data["schema_version"] == 4
assert data["retention"]["trash_days"] == 7
PY

printf '{"cwd":"%s","hook_event_name":"SessionStart"}' "$root/workspace" | "$launcher" "$plugin" >"$root/start.json"
printf '{"cwd":"%s","hook_event_name":"UserPromptSubmit","session_id":"clean","turn_id":"one"}' "$root/workspace" | "$launcher" "$plugin" >/dev/null
printf 'changed\n' >"$root/workspace/changed.txt"
printf '{"cwd":"%s","hook_event_name":"Stop","session_id":"clean","turn_id":"one","last_assistant_message":"Completed clean-device verification"}' "$root/workspace" | "$launcher" "$plugin" >"$root/stop.json"
! grep -q '"decision": "block"' "$root/stop.json" || fail "installed Stop hook interrupted user response"

test -f "$root/workspace/.wiki/.wiki-agent-system.json" || fail "installed hook did not initialize workspace wiki"
find "$root/workspace/.wiki/inbox/autosave" -type f -name '*.md' -print -quit | grep -q . || fail "installed Stop hook did not capture"
grep -R -q 'Completed clean-device verification' "$root/workspace/.wiki/inbox/autosave" || fail "installed Stop fallback omitted final result"
grep -q 'Workspace knowledge index:' "$root/start.json" || fail "installed hook did not emit preflight context"

retention_workspace="$root/retention-workspace"
mkdir -p "$retention_workspace/.wiki/raw" "$retention_workspace/.wiki/wiki" \
  "$retention_workspace/.wiki/inbox/autosave" "$retention_workspace/.wiki/.sessions" \
  "$retention_workspace/.wiki/.trash/autosave" "$retention_workspace/.wiki/.trash/state"
printf '%s\n' '# Workspace Wiki' >"$retention_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$retention_workspace/.wiki/_index.md"
printf '%s\n' canonical >"$retention_workspace/.wiki/raw/keep.md"
printf '%s\n' canonical >"$retention_workspace/.wiki/wiki/keep.md"
printf '%s\n' active >"$retention_workspace/.wiki/inbox/autosave/active.md"
printf '%s\n' old >"$retention_workspace/.wiki/.trash/autosave/old.md"
printf '%s\n' old-state >"$retention_workspace/.wiki/.trash/state/old.json"
touch -t 202001010000 "$retention_workspace/.wiki/inbox/autosave/active.md" \
  "$retention_workspace/.wiki/.trash/autosave/old.md" "$retention_workspace/.wiki/.trash/state/old.json"
"$launcher" "$plugin_root/../scripts/retention.py" "$retention_workspace" \
  --apply --scheduled --autosave-days 1 --state-days 1 >"$root/retention.json"
python3 - "$root/retention.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
purge = data["purge"]
assert purge["enabled"]
assert purge["older_than_days"] == 7
assert purge["summary"]["files"] == 2
assert purge["summary"]["errors"] == 0
PY
test ! -e "$retention_workspace/.wiki/.trash/autosave/old.md"
test ! -e "$retention_workspace/.wiki/.trash/state/old.json"
test -f "$retention_workspace/.wiki/.trash/autosave/active.md"
test -f "$retention_workspace/.wiki/raw/keep.md"
test -f "$retention_workspace/.wiki/wiki/keep.md"
printf '%s\n' 'PASS: Git marketplace clean-device install'
