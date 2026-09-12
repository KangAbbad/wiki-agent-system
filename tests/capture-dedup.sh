#!/bin/sh
set -eu

plugin_root=${1:-plugins/wiki-preflight}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
workspace="$test_root/workspace"
mkdir "$workspace"
printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"SessionStart\"}" | python3 "$plugin_root/hooks/preflight.py" >/dev/null

CODEX_SESSION_ID=one python3 "$plugin_root/scripts/wiki_ambient.py" capture \
  --cwd "$workspace" --kind research --outcome 'Initial research' \
  --decision 'Keep provenance' --artifact notes/research.md --confidence medium >/dev/null
CODEX_SESSION_ID=one python3 "$plugin_root/scripts/wiki_ambient.py" capture \
  --cwd "$workspace" --kind result --outcome 'Final result' \
  --verification 'lint passed' --artifact docs/result.md \
  --source 'https://example.test/source' --confidence high >/dev/null

captures="$workspace/.wiki/inbox/autosave"
test "$(find "$captures" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 1
capture=$(find "$captures" -type f -name 'session-*.md')
grep -Fq 'Final result' "$capture"
grep -Fq 'Keep provenance' "$capture"
grep -Fq '`notes/research.md`' "$capture"
grep -Fq '`docs/result.md`' "$capture"
grep -Fq 'lint passed' "$capture"
grep -Fq 'https://example.test/source' "$capture"
grep -Fq 'confidence: high' "$capture"

CODEX_SESSION_ID=two python3 "$plugin_root/scripts/wiki_ambient.py" capture \
  --cwd "$workspace" --outcome 'Separate task' --kind result >/dev/null
test "$(find "$captures" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 2

printf '%s\n' 'capture dedup passed'
