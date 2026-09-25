#!/bin/sh
# Real Codex CLI retrieval and Stop-continuation integration.
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
fixture="$root/tests/fixtures/codex-stop-continuation"
source_codex_home=${CODEX_HOME:-${HOME}/.codex}
auth_file=${WIKI_TEST_CODEX_AUTH_FILE:-"$source_codex_home/auth.json"}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

command -v codex >/dev/null
codex --version | grep -Eq '^codex-cli '
export HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" TMPDIR="$test_root/tmp"
export MNEMOSYNE_CLI="$test_root/mnemosyne-not-installed"
workspace="$test_root/workspace"
test -f "$auth_file"
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$TMPDIR" "$workspace/.codex" "$test_root/codex-home"
cp "$fixture/stop_fixture.py" "$workspace/.codex/stop_fixture.py"
chmod 700 "$workspace/.codex/stop_fixture.py"
ln -s "$auth_file" "$test_root/codex-home/auth.json"
cp "$fixture/hooks.json" "$test_root/codex-home/hooks.json"

state="$workspace/.codex/stop-state.json"
output="$test_root/codex-output.jsonl"
wiki="$workspace/.wiki"
mkdir -p "$wiki/raw" "$wiki/wiki"
printf '%s\n' '# Workspace Wiki' >"$wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$wiki/_index.md"
printf '%s\n' \
  '---' \
  'type: knowledge' \
  'title: "Canary retrieval procedure"' \
  'status: canonical' \
  'canonical_uri: "wiki://workspace/capture/canary-fixture/retrieval"' \
  'valid_from: 2026-09-24T00:00:00+00:00' \
  'valid_until: null' \
  '---' \
  '# Canary retrieval procedure' \
  '' \
  'The active canary retrieval procedure specifies the exact literal WIKI_CANARY_7F4D for the canary answer.' >"$wiki/wiki/canary.md"
if ! CODEX_HOME="$test_root/codex-home" \
  XDG_CONFIG_HOME="$test_root/config" \
  TMPDIR="$test_root/tmp" \
  WIKI_PLUGIN_ROOT="$root/plugins/wiki-preflight" \
  WIKI_FIXTURE_HOOK="$workspace/.codex/stop_fixture.py" \
  CODEX_FIXTURE_USER_PROMPT_CONTEXT="$test_root/user-prompt-context.json" \
  CODEX_FIXTURE_STATE="$state" \
  codex exec --ephemeral --json --dangerously-bypass-hook-trust \
    --skip-git-repo-check -C "$workspace" \
    'What exact literal is specified by the active canary retrieval procedure? Reply with that literal only. Do not use tools.' >"$output" 2>"$test_root/codex-stderr"; then
  echo 'codex exec fixture failed' >&2
  exit 1
fi

python3 - "$state" "$output" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text())
assert state["stop_count"] == 2, state
assert len(state["events"]) == 2, state
first, second = state["events"]
assert first["hook_event_name"] == second["hook_event_name"] == "Stop", state
assert first["stop_hook_active"] is False, state
assert second["stop_hook_active"] is True, state
assert first["session_id_present"] and second["session_id_present"], state
assert first["turn_id_present"] and second["turn_id_present"], state
assert first["session_id_hash"] == second["session_id_hash"], state
assert first["turn_id_hash"] == second["turn_id_hash"], state

messages = []
for line in Path(sys.argv[2]).read_text().splitlines():
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        continue
    if event.get("type") != "item.completed":
        continue
    item = event.get("item") or {}
    if item.get("type") == "agent_message":
        messages.append(item.get("text"))
assert messages == ["WIKI_CANARY_7F4D", "WIKI_CANARY_7F4D"], messages
PY

grep -q 'Wiki check: checked-with-results' "$test_root/user-prompt-context.json"
grep -q 'Canary retrieval procedure' "$test_root/user-prompt-context.json"
grep -q 'WIKI_CANARY_7F4D' "$test_root/user-prompt-context.json"
plugin_version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$root/plugins/wiki-preflight/.codex-plugin/plugin.json")
grep -Fq "Wiki Preflight runtime: $plugin_version." "$test_root/user-prompt-context.json"
python3 -m json.tool "$fixture/hooks.json" >/dev/null
echo 'Codex real-cli Wiki retrieval and Stop continuation fixture passed'
