#!/bin/sh
set -eu

fail() { printf '%s\n' "FAIL: $*" >&2; exit 1; }
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
mkdir -p "$root/codex" "$root/home" "$root/workspace"

export HOME="$root/home" CODEX_HOME="$root/codex"
codex plugin marketplace add "${WIKI_MARKETPLACE_SOURCE:-KangAbbad/wiki-agent-system}" --ref "${WIKI_MARKETPLACE_REF:-main}" --json >"$root/marketplace.json"
codex plugin add wiki-preflight@team-wiki --json >"$root/install.json"
codex plugin list --marketplace team-wiki --json >"$root/list.json"

grep -q '"pluginId": "wiki-preflight@team-wiki"' "$root/list.json" || fail "marketplace install did not register wiki-preflight"
plugin=$(find "$CODEX_HOME/plugins/cache/team-wiki/wiki-preflight" -path '*/hooks/preflight.py' -type f | head -n 1)
test -n "$plugin" || fail "installed cache has no preflight hook"
case "$plugin" in "$PWD"/*) fail "test used source tree instead of installed cache";; esac

printf '{"cwd":"%s","hook_event_name":"SessionStart"}' "$root/workspace" | python3 "$plugin" >"$root/start.json"
printf '{"cwd":"%s","hook_event_name":"Stop"}' "$root/workspace" | python3 "$plugin" >"$root/stop.json"

test -f "$root/workspace/.wiki/.wiki-agent-system.json" || fail "installed hook did not initialize workspace wiki"
find "$root/workspace/.wiki/inbox/autosave" -type f -name '*.md' -print -quit | grep -q . || fail "installed Stop hook did not capture"
grep -q 'Workspace knowledge index:' "$root/start.json" || fail "installed hook did not emit preflight context"
printf '%s\n' 'PASS: Git marketplace clean-device install'
