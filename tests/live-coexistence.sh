#!/bin/sh
set -eu

fail() { printf '%s\n' "FAIL: $*" >&2; exit 1; }
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
mkdir -p "$root/codex" "$root/home" "$root/workspace"

export HOME="$root/home" CODEX_HOME="$root/codex"
export XDG_CONFIG_HOME="$root/config"
export MNEMOSYNE_CLI="$root/mnemosyne-not-installed" TMPDIR="$root/tmp"
mkdir -p "$XDG_CONFIG_HOME" "$TMPDIR"
marketplace_source=${WIKI_MARKETPLACE_SOURCE:-KangAbbad/wiki-agent-system}
marketplace_ref=${WIKI_MARKETPLACE_REF-main}
if [ -d "$marketplace_source" ] || [ -z "$marketplace_ref" ]; then
  codex plugin marketplace add "$marketplace_source" --json >"$root/wiki-agent-system-marketplace.json"
else
  codex plugin marketplace add "$marketplace_source" --ref "$marketplace_ref" --json >"$root/wiki-agent-system-marketplace.json"
fi
codex plugin add wiki-preflight@wiki-agent-system --json >"$root/wiki-agent-system-install.json"
codex plugin marketplace add "${LLM_WIKI_MARKETPLACE_SOURCE:-nvk/llm-wiki}" --ref "${LLM_WIKI_MARKETPLACE_REF:-master}" --json >"$root/upstream-marketplace.json"
codex plugin add wiki@llm-wiki --json >"$root/upstream-install.json"

preflight=$(find "$CODEX_HOME/plugins/cache/wiki-agent-system/wiki-preflight" -path '*/hooks/preflight.py' -type f | head -n 1)
upstream=$(find "$CODEX_HOME/plugins/cache/llm-wiki/wiki" -path '*/hooks/llm_wiki_session.py' -type f | head -n 1)
test -n "$preflight" || fail "wiki-preflight hook missing after Git installation"
test -n "$upstream" || fail "wiki@llm-wiki hook missing after Git installation"
preflight_root=$(dirname "$preflight")
preflight_launcher="$preflight_root/launcher.sh"
test -x "$preflight_launcher" || fail "wiki-preflight launcher missing after Git installation"

payload=$(printf '{"cwd":"%s","hook_event_name":"SessionStart","session_id":"coexist"}' "$root/workspace")
printf '%s' "$payload" | "$preflight_launcher" "$preflight" >"$root/preflight-start.json"
printf '%s' "$payload" | python3 "$upstream" hook --harness codex --if-enabled >"$root/upstream-start.json"
stop=$(printf '{"cwd":"%s","hook_event_name":"Stop","session_id":"coexist","turn_id":"one","stop_hook_active":false,"last_assistant_message":"Completed coexistence verification"}' "$root/workspace")
printf '{"cwd":"%s","hook_event_name":"UserPromptSubmit","session_id":"coexist","turn_id":"one","prompt":"coexistence retrieval context"}' "$root/workspace" | "$preflight_launcher" "$preflight" >"$root/preflight-prompt.json"
printf 'changed\n' >"$root/workspace/changed.txt"
printf '%s' "$stop" | "$preflight_launcher" "$preflight" >"$root/preflight-stop-first.json"
! grep -q '"decision": "block"' "$root/preflight-stop-first.json" || fail "wiki-preflight interrupted user response"
printf '%s' "$stop" | python3 "$upstream" hook --harness codex --if-enabled >"$root/upstream-stop.json"

grep -q 'Wiki Preflight owns scoped knowledge retrieval' "$root/preflight-start.json" || fail "wiki-preflight omitted its bounded startup capsule"
! grep -q '_index.md' "$root/preflight-start.json" || fail "wiki-preflight dumped the full Wiki index"
! grep -q 'additionalContext' "$root/upstream-start.json" || fail "upstream hook added duplicate digest context after default ownership setup"
python3 - "$HOME/wiki/.sessions/config.json" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1]))
assert config["rehydrate"]["session_start"] is False
assert config["rehydrate"]["user_prompt"] is False
auto_capture = config.get("auto_capture")
assert auto_capture is None or isinstance(auto_capture, dict), config
PY
grep -q 'Wiki check: checked-no-match' "$root/preflight-prompt.json" || fail "wiki-preflight did not record its per-turn retrieval"
test "$(find "$root/workspace/.wiki/inbox/autosave" -type f -name '*.md' | wc -l | tr -d ' ')" -eq 1 || fail "coexisting Stop hooks produced duplicate workspace capture"
grep -R -q 'Completed coexistence verification' "$root/workspace/.wiki/inbox/autosave" || fail "fallback omitted final result"
test "$(find "$HOME/wiki/.sessions/digests" -type f -name '*.md' | wc -l | tr -d ' ')" -eq 1 || fail "upstream session digest missing or duplicated"
test -f "$root/workspace/.wiki/.sessions/wiki-agent-system/marker.json" || fail "workspace wiki was not initialized"
printf '%s\n' 'PASS: live wiki-preflight + wiki@llm-wiki coexistence'
