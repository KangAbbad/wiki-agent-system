#!/bin/sh
set -eu

fail() { printf '%s\n' "FAIL: $*" >&2; exit 1; }
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
mkdir -p "$root/codex" "$root/home" "$root/workspace"

export HOME="$root/home" CODEX_HOME="$root/codex"
marketplace_source=${WIKI_MARKETPLACE_SOURCE:-KangAbbad/wiki-agent-system}
marketplace_ref=${WIKI_MARKETPLACE_REF-main}
if [ -d "$marketplace_source" ]; then
  codex plugin marketplace add "$marketplace_source" --json >"$root/marketplace.json"
elif [ -n "$marketplace_ref" ]; then
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
hooks_root=$(dirname "$plugin")
installed_root=$(dirname "$hooks_root")
launcher="$hooks_root/launcher.sh"
test -x "$launcher" || fail "installed cache has no launcher"
sh "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/vendor-runtime-import.sh" "$installed_root"
python3 - "$installed_root/defaults/ambient.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
assert data["schema_version"] == 4
assert data["retention"]["trash_days"] == 7
PY

data_root="$root/plugin-data"
PLUGIN_ROOT="$installed_root" PLUGIN_DATA="$data_root" "$launcher" "$installed_root/hooks/provision.py"
test -f "$data_root/current/hooks/preflight.py" || fail "marketplace runtime was not provisioned"
cmp "$installed_root/hooks/preflight.py" "$data_root/current/hooks/preflight.py" || fail "stable runtime differs from installed candidate"
cmp "$installed_root/scripts/wiki_ambient.py" "$data_root/current/scripts/wiki_ambient.py" || fail "stable runtime differs from installed capture writer"
cmp "$installed_root/scripts/evidence_verification.py" "$data_root/current/scripts/evidence_verification.py" || fail "stable runtime differs from installed verifier"
rm -rf "$installed_root"
test ! -e "$installed_root" || fail "versioned marketplace cache was not removed"
stable_root="$data_root/current"
stable_launcher="$stable_root/hooks/launcher.sh"
stable_hook="$stable_root/hooks/preflight.py"
sh "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/vendor-runtime-import.sh" "$stable_root"
fallback_vendor="$root/fallback-vendor"
mv "$stable_root/vendor" "$fallback_vendor"
printf '{"cwd":"%s","hook_event_name":"SessionStart"}' "$root/workspace" | "$stable_launcher" "$stable_hook" >"$root/start.json"
"$stable_launcher" "$stable_root/scripts/evidence_verification.py" self-test >/dev/null
printf '{"cwd":"%s","hook_event_name":"UserPromptSubmit","session_id":"clean","turn_id":"one","prompt":"Research and synthesize the installed marketplace result"}' "$root/workspace" | "$stable_launcher" "$stable_hook" >/dev/null
printf '{"cwd":"%s","hook_event_name":"Stop","session_id":"clean","turn_id":"one","last_assistant_message":"Completed clean-device verification"}' "$root/workspace" | "$stable_launcher" "$stable_hook" >"$root/stop.json"
! grep -q '"decision": "block"' "$root/stop.json" || fail "installed Stop hook interrupted user response"

test -f "$root/workspace/.wiki/.sessions/wiki-agent-system/marker.json" || fail "installed hook did not initialize workspace wiki"
find "$root/workspace/.wiki/inbox/autosave" -type f -name '*.md' -print -quit | grep -q . || fail "installed Stop hook did not capture"
grep -R -q 'Completed clean-device verification' "$root/workspace/.wiki/inbox/autosave" || fail "installed Stop hook omitted final result"
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
"$stable_launcher" "$stable_root/scripts/retention.py" "$retention_workspace" \
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
mv "$fallback_vendor" "$stable_root/vendor"
printf '%s\n' 'PASS: Git marketplace clean-device install'
