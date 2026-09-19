#!/bin/sh
# Real Codex CLI Stop continuation integration. The fixture is local and deterministic.
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
fixture="$root/tests/fixtures/codex-stop-continuation"
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

command -v codex >/dev/null
codex --version | grep -Eq '^codex-cli '
workspace="$test_root/workspace"
codex_home=${CODEX_HOME:-${HOME}/.codex}
auth_file="$codex_home/auth.json"
test -f "$auth_file"
mkdir -p "$workspace/.codex" "$test_root/codex-home"
cp "$fixture/stop_fixture.py" "$workspace/.codex/stop_fixture.py"
chmod 700 "$workspace/.codex/stop_fixture.py"
ln -s "$auth_file" "$test_root/codex-home/auth.json"
cp "$fixture/hooks.json" "$test_root/codex-home/hooks.json"

state="$workspace/.codex/stop-state.json"
output="$test_root/codex-output.jsonl"
if ! CODEX_HOME="$test_root/codex-home" \
  WIKI_FIXTURE_HOOK="$workspace/.codex/stop_fixture.py" \
  CODEX_FIXTURE_STATE="$state" \
  codex exec --ephemeral --json --dangerously-bypass-hook-trust \
    --skip-git-repo-check -C "$workspace" \
    'Reply with exactly DONE. Do not use tools or ask questions.' >"$output" 2>"$test_root/codex-stderr"; then
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
assert messages == ["DONE", "DONE"], messages
PY

python3 -m json.tool "$fixture/hooks.json" >/dev/null
echo 'Codex Stop hook real-cli continuation fixture passed'
