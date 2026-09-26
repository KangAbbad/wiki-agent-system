#!/bin/sh
set -eu

fail() { printf '%s\n' "FAIL: $*" >&2; exit 1; }
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
mkdir -p "$root/codex" "$root/home" "$root/config" "$root/workspace" "$root/tmp"

export HOME="$root/home" XDG_CONFIG_HOME="$root/config" CODEX_HOME="$root/codex"
export MNEMOSYNE_CLI="$root/mnemosyne-not-installed" TMPDIR="$root/tmp"
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
if [ "${WIKI_P1_TRACE:-0}" = 1 ]; then
  sh -x "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/universal-preflight.sh" "$installed_root"
else
  sh "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/universal-preflight.sh" "$installed_root"
fi
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
cmp "$installed_root/scripts/canonical_evidence.py" "$data_root/current/scripts/canonical_evidence.py" || fail "stable runtime differs from installed canonical serializer"
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
printf '{"cwd":"%s","hook_event_name":"UserPromptSubmit","session_id":"clean","turn_id":"one","prompt":"Synthesize the installed marketplace result"}' "$root/workspace" | "$stable_launcher" "$stable_hook" >/dev/null
printf '{"cwd":"%s","hook_event_name":"Stop","session_id":"clean","turn_id":"one","last_assistant_message":"Completed clean-device verification"}' "$root/workspace" | "$stable_launcher" "$stable_hook" >"$root/stop.json"
! grep -q '"decision": "block"' "$root/stop.json" || fail "installed Stop hook interrupted user response"

test -f "$root/workspace/.wiki/.sessions/wiki-agent-system/marker.json" || fail "installed hook did not initialize workspace wiki"
find "$root/workspace/.wiki/inbox/autosave" -type f -name '*.md' -print -quit | grep -q . || fail "installed Stop hook did not capture"
grep -R -q 'Completed clean-device verification' "$root/workspace/.wiki/inbox/autosave" || fail "installed Stop hook omitted final result"
grep -q 'Wiki Preflight owns scoped knowledge retrieval' "$root/start.json" || fail "installed hook did not emit the bounded startup capsule"
printf '\nWIKI_INDEX_CONTENT_SENTINEL_7d26f3c9\n' >>"$root/workspace/.wiki/_index.md"
printf '{"cwd":"%s","hook_event_name":"SessionStart"}' "$root/workspace" | "$stable_launcher" "$stable_hook" >"$root/start-after-index.json"
! grep -q 'WIKI_INDEX_CONTENT_SENTINEL_7d26f3c9' "$root/start-after-index.json" || fail "installed startup hook dumped Wiki index content"

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
if [ "${WIKI_P1_TRACE:-0}" = 1 ]; then
  sh -x "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/universal-preflight.sh" "$stable_root"
else
  sh "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/universal-preflight.sh" "$stable_root"
fi

canonical_workspace="$root/canonical-workspace"
mkdir -p "$canonical_workspace/.wiki/raw" "$canonical_workspace/.wiki/wiki"
printf '%s\n' '# Workspace Wiki' >"$canonical_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$canonical_workspace/.wiki/_index.md"
printf '%s\n' 'Marketplace canonical evidence.' >"$root/marketplace-source.md"
canonical_result=$(HOME="$root/home" XDG_CONFIG_HOME="$root/config" \
  "$stable_launcher" "$stable_root/scripts/wiki_ambient.py" canonicalize \
  --cwd "$canonical_workspace" --source "$root/marketplace-source.md" \
  --source-url 'https://example.test/marketplace' --title 'Marketplace source')
canonical_raw=$(printf '%s' "$canonical_result" | python3 -c 'import json,sys; print(json.load(sys.stdin)["path"])')
case "$canonical_raw" in
  */raw/articles/*) ;;
  *) fail "marketplace canonical writer did not emit raw/articles" ;;
esac
grep -q '^type: articles$' "$canonical_raw" || fail "marketplace canonical writer emitted the wrong type"
grep -q '^knowledge_readiness: ready$' "$canonical_raw" || fail "marketplace canonical writer omitted readiness"
grep -q '^source: "https://example.test/marketplace"$' "$canonical_raw" || fail "marketplace canonical writer omitted source"
test -z "$(find "$canonical_workspace/.wiki/raw" -maxdepth 1 -type f -name '*.md' ! -name '_index.md' -print)" || fail "marketplace canonical writer left a direct raw record"

legacy_body='Marketplace legacy evidence.'
legacy_digest=$(printf '%s\n' "$legacy_body" | shasum -a 256 | awk '{print $1}')
legacy_uri="wiki://workspace/evidence/$legacy_digest"
legacy_record="$canonical_workspace/.wiki/raw/marketplace-legacy.md"
printf '%s\n' \
  '---' \
  'schema: 1' \
  'title: "Marketplace legacy"' \
  'source_url: "https://example.test/legacy"' \
  'type: raw-source' \
  'retrieved: 2026-09-20' \
  'retrieved_at: 2026-09-20T12:00:00+00:00' \
  "content_sha256: $legacy_digest" \
  'provenance_class: web-extraction' \
  'evidence_status: unverified' \
  'evidence_eligible: true' \
  'transcript_eligible: false' \
  "canonical_uri: \"$legacy_uri\"" \
  'status: canonical' \
  'supersedes: null' \
  'valid_until: null' \
  '---' \
  '# Marketplace legacy' \
  '' \
  "$legacy_body" >"$legacy_record"
migration_result=$(HOME="$root/home" XDG_CONFIG_HOME="$root/config" \
  "$stable_launcher" "$stable_root/scripts/wiki_ambient.py" migrate-evidence \
  --cwd "$canonical_workspace")
printf '%s' "$migration_result" | python3 -c 'import json,sys; assert json.load(sys.stdin)["migrated"] == 1'
test ! -e "$legacy_record" || fail "marketplace migration left the legacy record"
migrated_raw=$(find "$canonical_workspace/.wiki/raw/articles" -maxdepth 1 -type f -name 'marketplace-legacy-*.md' -print)
test -n "$migrated_raw" || fail "marketplace migration emitted no raw/articles record"
grep -q '^type: articles$' "$migrated_raw" || fail "marketplace migration emitted the wrong type"
grep -q '^canonical_uri: "wiki://workspace/evidence/' "$migrated_raw" || fail "marketplace migration changed canonical identity"
grep -q 'Marketplace legacy evidence\.' "$migrated_raw" || fail "marketplace migration changed source body"
second_migration=$(HOME="$root/home" XDG_CONFIG_HOME="$root/config" \
  "$stable_launcher" "$stable_root/scripts/wiki_ambient.py" migrate-evidence \
  --cwd "$canonical_workspace")
printf '%s' "$second_migration" | python3 -c 'import json,sys; assert json.load(sys.stdin)["migrated"] == 0'
printf '%s\n' 'PASS: Git marketplace clean-device install'
