#!/bin/sh
# P1 release gate. It must be run from any directory after a clean checkout.
set -u

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
failed=0

check() {
  name=$1
  shift
  if "$@"; then
    printf 'PASS %s\n' "$name"
  else
    printf 'FAIL %s\n' "$name" >&2
    failed=1
  fi
}

semantic_and_evidence() (
  test_root=$(mktemp -d)
  trap 'rm -rf "$test_root"' EXIT
  cp -R "$root/plugins/wiki-preflight" "$test_root/plugin"
  workspace="$test_root/workspace"
  mkdir -p "$workspace/.wiki/raw" "$workspace/.wiki/wiki" "$workspace/.wiki/inbox"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/config.md"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/_index.md"

  result=$(python3 "$test_root/plugin/scripts/wiki_ambient.py" capture \
    --cwd "$workspace" \
    --outcome 'Delivered token=super-secret safely' \
    --kind result \
    --artifact src/main.py \
    --decision 'Use atomic writes' \
    --verification 'tests passed' \
    --source 'https://example.test/spec' \
    --confidence high \
    --open-question 'None')
  printf '%s' "$result" | grep -q 'pending-curation'
  capture=$(find "$workspace/.wiki/inbox/autosave" -type f -name 'session-*.md')
  test -n "$capture"
  grep -q 'token=\[REDACTED\]' "$capture"
  ! grep -q 'super-secret' "$capture"

  source="$test_root/source.md"
  printf '%s\n' '# Attributable evidence' >"$source"
  result=$(python3 "$test_root/plugin/scripts/wiki_ambient.py" canonicalize \
    --cwd "$workspace" --source "$source" \
    --source-url 'https://example.test/spec' --title 'Official spec')
  printf '%s' "$result" | grep -q 'canonical-evidence'
  raw=$(find "$workspace/.wiki/raw" -type f -name 'official-spec-*.md')
  test -n "$raw"
  grep -q 'content_sha256:' "$raw"

  unsafe="$test_root/unsafe.md"
  printf '%s\n' 'api_key=not-for-wiki' >"$unsafe"
  ! python3 "$test_root/plugin/scripts/wiki_ambient.py" canonicalize \
    --cwd "$workspace" --source "$unsafe" \
    --source-url 'https://example.test/unsafe' --title 'Unsafe evidence' >/dev/null 2>&1
)

migration_marker() (
  test_root=$(mktemp -d)
  trap 'rm -rf "$test_root"' EXIT
  workspace="$test_root/workspace"
  mkdir "$workspace"
  printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"SessionStart\"}" | \
    python3 "$root/plugins/wiki-preflight/hooks/preflight.py" >/dev/null
  python3 -c 'import json,sys; assert json.load(open(sys.argv[1])) == {"schema_version": 2}' \
    "$workspace/.wiki/.wiki-agent-system.json"
  printf '%s\n' '{"schema_version": 99}' >"$workspace/.wiki/.wiki-agent-system.json"
  output=$(printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"SessionStart\"}" | python3 "$root/plugins/wiki-preflight/hooks/preflight.py")
  printf '%s' "$output" | grep -q 'schema is newer'
  python3 -c 'import json,sys; assert json.load(open(sys.argv[1])) == {"schema_version": 99}' \
    "$workspace/.wiki/.wiki-agent-system.json"
)

from_root() (
  cd "$root" || exit 1
  "$@"
)

check 'plugin manifest' test -f "$root/plugins/wiki-preflight/.codex-plugin/plugin.json"
check 'ambient configuration schema' python3 "$root/plugins/wiki-preflight/scripts/wiki_ambient.py" validate
check 'user-scope configuration survives plugin replacement' sh "$root/tests/config-persistence.sh" "$root/plugins/wiki-preflight"
check 'user-scope configuration migration' sh "$root/tests/config-migration.sh" "$root/plugins/wiki-preflight"
check 'agent-side semantic finalizer contract' sh "$root/tests/semantic-finalizer.sh" "$root/plugins/wiki-preflight"
check 'per-task semantic capture deduplication' sh "$root/tests/capture-dedup.sh" "$root/plugins/wiki-preflight"
check 'quota report and operational-data quarantine' sh "$root/tests/quota-retention.sh" "$root/plugins/wiki-preflight"
check 'stable hook runtime survives removed plugin cache' sh "$root/tests/stable-hook-runtime.sh" "$root/plugins/wiki-preflight"
check 'semantic capture and evidence gate' semantic_and_evidence
check 'migration/version marker' migration_marker
check 'behavior matrix' sh "$root/tests/behavior-matrix.sh" "$root/plugins/wiki-preflight"
check 'source clean-device smoke test' from_root sh tests/clean-device.sh
check 'installed-package smoke test' from_root sh tests/installed-package.sh
check 'live coexistence test' from_root sh tests/live-coexistence.sh
check 'Git marketplace clean-device test' from_root sh tests/git-marketplace-clean-device.sh

if [ "$failed" -eq 0 ]; then
  echo 'P1 ACCEPTANCE: PASS'
  exit 0
fi
echo 'P1 ACCEPTANCE: FAIL' >&2
exit 1
