#!/bin/sh
# Contract: Stop owns bounded semantic capture and never blocks the response.
set -eu

plugin_root=${1:-plugins/wiki-preflight}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
export HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config"
mkdir -p "$HOME"

run_hook() {
  printf '%s' "$2" | "$plugin_root/hooks/launcher.sh" "$plugin_root/hooks/preflight.py"
}

context_workspace="$test_root/context"
mkdir "$context_workspace"
context=$(run_hook UserPromptSubmit "{\"cwd\":\"$context_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"context\",\"turn_id\":\"one\"}")
printf '%s\n' "$context" | grep -Fq 'Stop hook owns the default semantic capture'
printf '%s\n' "$context" | grep -Fq 'No command is required'
! printf '%s\n' "$context" | grep -Fq 'Before sending the final response for meaningful workspace work, run'
! printf '%s\n' "$context" | grep -Eq 'python3.*wiki_ambient.py'
! grep -Fq '### Required semantic finalizer' "$plugin_root/skills/wiki-workspace/SKILL.md"
grep -Fq '## Stop-owned capture' "$plugin_root/skills/wiki-workspace/SKILL.md"

durable="$test_root/durable"
mkdir "$durable"
run_hook UserPromptSubmit "{\"cwd\":\"$durable\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"durable\",\"turn_id\":\"one\",\"prompt\":\"Research and synthesize the migration decision\"}" >/dev/null
state=$(find "$durable/.wiki/.sessions" -type f -name '*.json')
python3 - "$state" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
assert set(data) == {"schema_version", "started_at", "capture_key", "prompt_intent"}
assert data["prompt_intent"]["matched"] is True
assert "prompt" not in data
PY
stop=$(run_hook Stop "{\"cwd\":\"$durable\",\"hook_event_name\":\"Stop\",\"session_id\":\"durable\",\"turn_id\":\"one\",\"last_assistant_message\":\"Completed migration with token=hidden\"}")
printf '%s\n' "$stop" | grep -Fq '"hookEventName": "Stop"'
! printf '%s\n' "$stop" | grep -Fq '"decision": "block"'
capture=$(find "$durable/.wiki/inbox/autosave" -type f -name 'session-*.md')
test "$(find "$durable/.wiki/inbox/autosave" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 1
grep -Fq 'Completed migration with token= [REDACTED]' "$capture"
! grep -Fq 'token=hidden' "$capture"
test ! -e "$state"

casual="$test_root/casual"
mkdir "$casual"
run_hook UserPromptSubmit "{\"cwd\":\"$casual\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"casual\",\"turn_id\":\"one\",\"prompt\":\"What is two plus two?\"}" >/dev/null
run_hook Stop "{\"cwd\":\"$casual\",\"hook_event_name\":\"Stop\",\"session_id\":\"casual\",\"turn_id\":\"one\",\"last_assistant_message\":\"4\"}" >/dev/null
test "$(find "$casual/.wiki/inbox" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 0

no_id="$test_root/no-id"
mkdir "$no_id"
run_hook UserPromptSubmit "{\"cwd\":\"$no_id\",\"hook_event_name\":\"UserPromptSubmit\",\"prompt\":\"Research lifecycle alpha\"}" >/dev/null
run_hook Stop "{\"cwd\":\"$no_id\",\"hook_event_name\":\"Stop\",\"last_assistant_message\":\"Outcome alpha\"}" >/dev/null
run_hook UserPromptSubmit "{\"cwd\":\"$no_id\",\"hook_event_name\":\"UserPromptSubmit\",\"prompt\":\"Research lifecycle beta\"}" >/dev/null
run_hook Stop "{\"cwd\":\"$no_id\",\"hook_event_name\":\"Stop\",\"last_assistant_message\":\"Outcome beta\"}" >/dev/null
test "$(find "$no_id/.wiki/inbox" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 0
test ! -d "$no_id/.wiki/.sessions/wiki-agent-system/finalizers"

changed="$test_root/changed"
mkdir "$changed"
run_hook UserPromptSubmit "{\"cwd\":\"$changed\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"changed\",\"turn_id\":\"one\",\"prompt\":\"Hello\"}" >/dev/null
printf '%s\n' changed >"$changed/changed.txt"
run_hook Stop "{\"cwd\":\"$changed\",\"hook_event_name\":\"Stop\",\"session_id\":\"changed\",\"turn_id\":\"one\",\"last_assistant_message\":\"Workspace changed\"}" >/dev/null
test "$(find "$changed/.wiki/inbox/autosave" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 1

structured="$test_root/structured"
mkdir "$structured"
run_hook UserPromptSubmit "{\"cwd\":\"$structured\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"structured\",\"turn_id\":\"one\",\"prompt\":\"Implement and verify the structured result\"}" >/dev/null
structured_payload=$(python3 - "$structured" <<'PY'
import json
import sys

print(json.dumps({
    "cwd": sys.argv[1],
    "hook_event_name": "Stop",
    "session_id": "structured",
    "turn_id": "one",
    "last_assistant_message": """## Outcome

Hook parsed the structured result with token=hidden.

## Decisions

- Keep bounded parsing.

## Artifacts

- `src/structured.py`

## Verification

- parser passed

## Sources

- https://example.test/structured

## Confidence

high

## Open questions

- None
""",
}))
PY
)
run_hook Stop "$structured_payload" >/dev/null
structured_capture=$(find "$structured/.wiki/inbox/autosave" -type f -name 'session-*.md')
grep -Fq 'Hook parsed the structured result with token= [REDACTED]' "$structured_capture"
grep -Fq 'Keep bounded parsing.' "$structured_capture"
grep -Fq '`src/structured.py`' "$structured_capture"
grep -Fq 'parser passed' "$structured_capture"
grep -Fq 'https://example.test/structured' "$structured_capture"
grep -Fq 'confidence: high' "$structured_capture"
grep -Fq -- '- None' "$structured_capture"
! grep -Fq 'token=hidden' "$structured_capture"

missing="$test_root/missing"
mkdir "$missing"
run_hook UserPromptSubmit "{\"cwd\":\"$missing\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"missing\",\"turn_id\":\"one\",\"prompt\":\"Implement the fix\"}" >/dev/null
run_hook Stop "{\"cwd\":\"$missing\",\"hook_event_name\":\"Stop\",\"session_id\":\"missing\",\"turn_id\":\"one\"}" >/dev/null
test "$(find "$missing/.wiki/inbox" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 0

foreign="$test_root/foreign"
mkdir -p "$foreign/.wiki"
printf '%s\n' foreign >"$foreign/.wiki/marker"
run_hook UserPromptSubmit "{\"cwd\":\"$foreign\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"foreign\",\"turn_id\":\"one\",\"prompt\":\"Research the foreign Wiki boundary\"}" >/dev/null
run_hook Stop "{\"cwd\":\"$foreign\",\"hook_event_name\":\"Stop\",\"session_id\":\"foreign\",\"turn_id\":\"one\",\"last_assistant_message\":\"Must not write\"}" >/dev/null
test -f "$foreign/.wiki/marker"
test ! -e "$foreign/.wiki/inbox"

future="$test_root/future"
mkdir -p "$future/.wiki/raw" "$future/.wiki/wiki"
printf '%s\n' '# Workspace Wiki' >"$future/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$future/.wiki/_index.md"
printf '%s\n' '{"schema_version":99}' >"$future/.wiki/.wiki-agent-system.json"
run_hook UserPromptSubmit "{\"cwd\":\"$future\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"future\",\"turn_id\":\"one\",\"prompt\":\"Implement the future-schema task\"}" >/dev/null
run_hook Stop "{\"cwd\":\"$future\",\"hook_event_name\":\"Stop\",\"session_id\":\"future\",\"turn_id\":\"one\",\"last_assistant_message\":\"Must stay read-only\"}" >/dev/null
grep -Fq '{"schema_version":99}' "$future/.wiki/.wiki-agent-system.json"
test ! -e "$future/.wiki/inbox"

future_state="$test_root/future-state"
mkdir -p "$future_state/.wiki/.sessions/wiki-agent-system/finalizers"
future_state_key=$(python3 -c 'import hashlib; print(hashlib.sha256(b"future:one").hexdigest()[:16])')
printf '%s\n' '{"schema_version":99}' >"$future_state/.wiki/.sessions/wiki-agent-system/finalizers/$future_state_key.json"
run_hook Stop "{\"cwd\":\"$future_state\",\"hook_event_name\":\"Stop\",\"session_id\":\"future\",\"turn_id\":\"one\",\"last_assistant_message\":\"Must preserve future state\"}" >/dev/null
grep -Fq '{"schema_version":99}' "$future_state/.wiki/.sessions/wiki-agent-system/finalizers/$future_state_key.json"

merge="$test_root/merge"
mkdir "$merge"
run_hook UserPromptSubmit "{\"cwd\":\"$merge\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"merge\",\"turn_id\":\"one\",\"prompt\":\"Implement and verify the capture merge\"}" >/dev/null
CODEX_SESSION_ID=merge "$plugin_root/hooks/launcher.sh" "$plugin_root/scripts/wiki_ambient.py" capture \
  --cwd "$merge" --outcome 'Structured result' --decision 'Keep merge' \
  --artifact src/main.py --verification 'tests passed' --confidence high >/dev/null
run_hook Stop "{\"cwd\":\"$merge\",\"hook_event_name\":\"Stop\",\"session_id\":\"merge\",\"turn_id\":\"one\",\"last_assistant_message\":\"Final merged result token=hidden\"}" >/dev/null
run_hook Stop "{\"cwd\":\"$merge\",\"hook_event_name\":\"Stop\",\"session_id\":\"merge\",\"turn_id\":\"one\",\"last_assistant_message\":\"Repeated stop\"}" >/dev/null
test "$(find "$merge/.wiki/inbox/autosave" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 1
capture=$(find "$merge/.wiki/inbox/autosave" -type f -name 'session-*.md')
grep -Fq 'Final merged result token= [REDACTED]' "$capture"
grep -Fq 'Keep merge' "$capture"
grep -Fq '`src/main.py`' "$capture"
grep -Fq 'tests passed' "$capture"
! grep -Fq 'token=hidden' "$capture"

echo 'semantic finalizer contract passed'
