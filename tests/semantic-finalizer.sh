#!/bin/sh
# Contract: the injected policy requires semantic capture and Stop enforces it.
set -eu

plugin_root=${1:-plugins/wiki-preflight}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
workspace="$test_root/workspace"
mkdir "$workspace"

context=$(printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"UserPromptSubmit\"}" \
  | python3 "$plugin_root/hooks/preflight.py")

printf '%s\n' "$context" | grep -Fq 'Before sending the final response for meaningful workspace work, run'
printf '%s\n' "$context" | grep -Fq 'Do not ask the user to save it.'
printf '%s\n' "$context" | grep -Fq 'The Stop hook receives the final assistant message. It blocks once to require this'
grep -Fq '### Required semantic finalizer' "$plugin_root/skills/wiki-team/SKILL.md"
grep -Fq 'Before sending the final response for meaningful workspace work, execute the' "$plugin_root/skills/wiki-team/SKILL.md"

first=$(printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"test\",\"turn_id\":\"one\",\"stop_hook_active\":false,\"last_assistant_message\":\"Completed migration with token=hidden\"}" | python3 "$plugin_root/hooks/preflight.py")
printf '%s\n' "$first" | grep -Fq '"decision": "block"'

second=$(printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"test\",\"turn_id\":\"one\",\"stop_hook_active\":true,\"last_assistant_message\":\"Capture omitted\"}" | python3 "$plugin_root/hooks/preflight.py")
printf '%s\n' "$second" | grep -Fq '"hookEventName": "Stop"'
capture=$(find "$workspace/.wiki/inbox/autosave" -type f -name '*-semantic-fallback.md')
grep -Fq 'Completed migration with token= [REDACTED]' "$capture"
! grep -Fq 'token=hidden' "$capture"

printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"test\",\"turn_id\":\"two\",\"stop_hook_active\":false,\"last_assistant_message\":\"Completed direct capture\"}" | python3 "$plugin_root/hooks/preflight.py" | grep -Fq '"decision": "block"'
python3 "$plugin_root/scripts/wiki_ambient.py" capture --cwd "$workspace" --outcome 'Completed direct capture' --kind result >/dev/null
printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"test\",\"turn_id\":\"two\",\"stop_hook_active\":true,\"last_assistant_message\":\"Capture completed\"}" | python3 "$plugin_root/hooks/preflight.py" | grep -Fq '"hookEventName": "Stop"'
test "$(find "$workspace/.wiki/inbox/autosave" -type f -name '*-semantic-fallback.md' | wc -l | tr -d ' ')" -eq 1

echo 'semantic finalizer contract passed'
