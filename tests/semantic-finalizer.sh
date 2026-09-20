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
printf '%s\n' "$context" | grep -Fq 'evidence lifecycle'
printf '%s\n' "$context" | grep -Fq '`unverified` or `exhausted`'
printf '%s\n' "$context" | grep -Fq 'required private credential or source'
printf '%s\n' "$context" | grep -Fq 'destructive production verification'
printf '%s\n' "$context" | grep -Fq 'Public vendor, database'
! printf '%s\n' "$context" | grep -Fq 'Before sending the final response for meaningful workspace work, run'
! printf '%s\n' "$context" | grep -Eq 'python3.*wiki_ambient.py'
! grep -Fq '### Required semantic finalizer' "$plugin_root/skills/wiki-workspace/SKILL.md"
grep -Fq '## Stop-owned capture' "$plugin_root/skills/wiki-workspace/SKILL.md"
grep -Fq 'routine user delegation' "$plugin_root/skills/wiki-workspace/SKILL.md"
grep -Fq 'routine user delegation' "$plugin_root/skills/wiki-ambient/SKILL.md"

durable="$test_root/durable"
mkdir "$durable"
run_hook UserPromptSubmit "{\"cwd\":\"$durable\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"durable\",\"turn_id\":\"one\",\"prompt\":\"Research and synthesize the migration decision\"}" >/dev/null
state=$(find "$durable/.wiki/.sessions/wiki-agent-system/finalizers" -type f -name '*.json')
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
mkdir -p "$future/.wiki/.sessions/wiki-agent-system"
printf '%s\n' '{"schema_version":99}' >"$future/.wiki/.sessions/wiki-agent-system/marker.json"
run_hook UserPromptSubmit "{\"cwd\":\"$future\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"future\",\"turn_id\":\"one\",\"prompt\":\"Implement the future-schema task\"}" >/dev/null
run_hook Stop "{\"cwd\":\"$future\",\"hook_event_name\":\"Stop\",\"session_id\":\"future\",\"turn_id\":\"one\",\"last_assistant_message\":\"Must stay read-only\"}" >/dev/null
grep -Fq '{"schema_version":99}' "$future/.wiki/.sessions/wiki-agent-system/marker.json"
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

public_gap="$test_root/public-gap"
mkdir "$public_gap"
public_context=$(run_hook UserPromptSubmit "{\"cwd\":\"$public_gap\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"public-gap\",\"turn_id\":\"one\",\"prompt\":\"Verify production drop database docs https://example.test/spec\"}")
printf '%s' "$public_context" | grep -Fq 'status=blocked'
printf '%s' "$public_context" | grep -Fq 'authority_request=destructive production verification'
! printf '%s' "$public_context" | grep -Fq 'Skipped'
run_hook Stop "{\"cwd\":\"$public_gap\",\"hook_event_name\":\"Stop\",\"session_id\":\"public-gap\",\"turn_id\":\"one\",\"last_assistant_message\":\"Verification status: unverified; durable retry remains agent-owned.\"}" >/dev/null
gap_capture=$(find "$public_gap/.wiki/inbox/autosave" -type f -name 'session-*.md')
grep -Fq 'unverified' "$gap_capture"
grep -Fq 'agent-owned' "$gap_capture"

foreground="$test_root/foreground"
mkdir "$foreground"
foreground_hook="$plugin_root/hooks/preflight.py"
python3 - "$foreground_hook" "$foreground/.wiki" <<'PY'
import hashlib
import contextlib
import importlib.util
import io
import json
import sys
import time
from pathlib import Path

hook, wiki = map(Path, sys.argv[1:])
wiki.mkdir(parents=True)
(wiki / "raw").mkdir()
(wiki / "wiki").mkdir()
(wiki / "config.md").write_text("# Workspace Wiki\n")
(wiki / "_index.md").write_text("# Workspace Wiki\n")
spec = importlib.util.spec_from_file_location("preflight", hook)
module = importlib.util.module_from_spec(spec)
sys.stdin = io.StringIO(json.dumps({"cwd": str(wiki.parent), "hook_event_name": "SessionStart"}))
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(module)

queue_id = module.queue_id_for("controller-session", "controller-turn")
module.foreground_controller_directory(wiki)
path, _ = module.foreground_controller_paths(wiki, queue_id)
now = time.time()
module.atomic_queue_write(path, {
    "schema_version": 0,
    "queue_id": queue_id,
    "state": "pending",
    "action": "caption-attempt",
    "loop_count": 0,
    "deadline_at": now + 40,
    "revision": 0,
    "reason": "caption evidence pending",
    "created_at": now,
    "updated_at": now,
})
status, state, error = module.ensure_foreground_controller(wiki, queue_id)
assert status == "migrated", (status, error)
assert state["schema_version"] == 1
assert "prompt" not in json.dumps(state)

gate = module.stop_foreground_gate(wiki, {
    "session_id": "controller-session",
    "turn_id": "controller-turn",
    "last_assistant_message": "still working",
})
assert gate["block"] is False and gate["capture"] is True, gate
assert gate["reason"] == ""

status, state, error = module.update_foreground_controller(
    wiki, queue_id, state["revision"], state="exhausted",
    action="terminal-caption-result", reason="caption queue exhausted",
)
assert status == "updated", (status, error)
report = {
    "session_id": "controller-session",
    "turn_id": "controller-turn",
    "last_assistant_message": "still finalizing",
}
assert module.stop_foreground_gate(wiki, report)["block"] is True
report["last_assistant_message"] = "status: exhausted; provenance: metadata; transcript: not-available"
assert module.stop_foreground_gate(wiki, report)["capture"] is True

future_id = module.queue_id_for("future-controller", "turn")
future_path, _ = module.foreground_controller_paths(wiki, future_id)
future = module.new_foreground_controller(future_id)
future["schema_version"] = 99
module.atomic_queue_write(future_path, future)
before = hashlib.sha256(future_path.read_bytes()).hexdigest()
assert module.ensure_foreground_controller(wiki, future_id)[0] == "future-schema"
assert hashlib.sha256(future_path.read_bytes()).hexdigest() == before
future_gate = module.stop_foreground_gate(wiki, {"session_id": "future-controller", "turn_id": "turn"})
assert future_gate["capture"] is False

expired_id = module.queue_id_for("expired-controller", "turn")
expired_path, _ = module.foreground_controller_paths(wiki, expired_id)
expired = module.new_foreground_controller(expired_id)
expired["deadline_at"] = 0
module.atomic_queue_write(expired_path, expired)
module.advance_foreground_controller(
    wiki, wiki.parent, "", {"session_id": "expired-controller", "turn_id": "turn"},
)
assert module.read_foreground_controller(wiki, expired_id)[1]["state"] == "exhausted"
PY

echo 'semantic finalizer contract passed'
