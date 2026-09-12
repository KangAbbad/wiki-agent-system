#!/bin/sh
set -eu

fail() { printf '%s\n' "FAIL: $*" >&2; exit 1; }
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
mkdir -p "$root/codex" "$root/home" "$root/workspace"

export HOME="$root/home" CODEX_HOME="$root/codex"
codex plugin marketplace add "${WIKI_MARKETPLACE_SOURCE:-KangAbbad/wiki-agent-system}" --ref "${WIKI_MARKETPLACE_REF:-main}" --json >"$root/team-marketplace.json"
codex plugin add wiki-preflight@team-wiki --json >"$root/team-install.json"
codex plugin marketplace add "${LLM_WIKI_MARKETPLACE_SOURCE:-nvk/llm-wiki}" --ref "${LLM_WIKI_MARKETPLACE_REF:-master}" --json >"$root/upstream-marketplace.json"
codex plugin add wiki@llm-wiki --json >"$root/upstream-install.json"

preflight=$(find "$CODEX_HOME/plugins/cache/team-wiki/wiki-preflight" -path '*/hooks/preflight.py' -type f | head -n 1)
upstream=$(find "$CODEX_HOME/plugins/cache/llm-wiki/wiki" -path '*/hooks/llm_wiki_session.py' -type f | head -n 1)
test -n "$preflight" || fail "wiki-preflight hook missing after Git installation"
test -n "$upstream" || fail "wiki@llm-wiki hook missing after Git installation"

payload=$(printf '{"cwd":"%s","hook_event_name":"SessionStart","session_id":"coexist"}' "$root/workspace")
printf '%s' "$payload" | python3 "$preflight" >"$root/preflight-start.json"
printf '%s' "$payload" | python3 "$upstream" hook --harness codex --if-enabled >"$root/upstream-start.json"
stop=$(printf '{"cwd":"%s","hook_event_name":"Stop","session_id":"coexist"}' "$root/workspace")
printf '%s' "$stop" | python3 "$preflight" >"$root/preflight-stop.json"
printf '%s' "$stop" | python3 "$upstream" hook --harness codex --if-enabled >"$root/upstream-stop.json"

test "$(grep -c 'Workspace knowledge index:' "$root/preflight-start.json")" -eq 1 || fail "wiki-preflight emitted duplicate repository preflight"
test "$(grep -c 'Workspace knowledge index:' "$root/upstream-start.json" || true)" -eq 0 || fail "upstream hook emitted duplicate repository preflight"
test "$(find "$root/workspace/.wiki/inbox/autosave" -type f -name '*.md' | wc -l | tr -d ' ')" -eq 1 || fail "coexisting Stop hooks produced duplicate workspace capture"
test "$(find "$HOME/wiki/.sessions/digests" -type f -name '*.md' | wc -l | tr -d ' ')" -eq 1 || fail "upstream session digest missing or duplicated"
test -f "$root/workspace/.wiki/.wiki-agent-system.json" || fail "workspace wiki was not initialized"
printf '%s\n' 'PASS: live wiki-preflight + wiki@llm-wiki coexistence'
