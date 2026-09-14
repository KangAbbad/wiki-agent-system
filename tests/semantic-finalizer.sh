#!/bin/sh
# Contract: the injected policy requires semantic capture; Stop falls back quietly.
set -eu

plugin_root=${1:-plugins/wiki-preflight}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
workspace="$test_root/workspace"
mkdir "$workspace"

context=$(printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"UserPromptSubmit\"}" \
  | "$plugin_root/hooks/launcher.sh" "$plugin_root/hooks/preflight.py")

printf '%s\n' "$context" | grep -Fq 'Before sending the final response for meaningful workspace work, run'
printf '%s\n' "$context" | grep -Fq 'Do not ask the user to save it.'
printf '%s\n' "$context" | grep -Fq 'The Stop hook never interrupts the user-facing response.'
grep -Fq '### Required semantic finalizer' "$plugin_root/skills/wiki-workspace/SKILL.md"
grep -Fq 'Before sending the final response for meaningful workspace work, execute the' "$plugin_root/skills/wiki-workspace/SKILL.md"

printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"test\",\"turn_id\":\"one\"}" | "$plugin_root/hooks/launcher.sh" "$plugin_root/hooks/preflight.py" >/dev/null
printf '%s\n' 'changed=true' >"$workspace/changed.txt"
first=$(printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"test\",\"turn_id\":\"one\",\"last_assistant_message\":\"Completed migration with token=hidden\"}" | "$plugin_root/hooks/launcher.sh" "$plugin_root/hooks/preflight.py")
! printf '%s\n' "$first" | grep -Fq '"decision": "block"'
printf '%s\n' "$first" | grep -Fq '"hookEventName": "Stop"'

second=$(printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"test\",\"turn_id\":\"one\",\"last_assistant_message\":\"Capture omitted\"}" | "$plugin_root/hooks/launcher.sh" "$plugin_root/hooks/preflight.py")
printf '%s\n' "$second" | grep -Fq '"hookEventName": "Stop"'
capture=$(find "$workspace/.wiki/inbox/autosave" -type f -name '*-semantic-fallback.md')
grep -Fq 'Completed migration with token= [REDACTED]' "$capture"
! grep -Fq 'token=hidden' "$capture"

printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"test\",\"turn_id\":\"two\"}" | "$plugin_root/hooks/launcher.sh" "$plugin_root/hooks/preflight.py" >/dev/null
"$plugin_root/hooks/launcher.sh" "$plugin_root/scripts/wiki_ambient.py" capture --cwd "$workspace" --outcome 'Completed direct capture' --kind result >/dev/null
printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"test\",\"turn_id\":\"two\",\"last_assistant_message\":\"Capture completed\"}" | "$plugin_root/hooks/launcher.sh" "$plugin_root/hooks/preflight.py" | grep -Fq '"hookEventName": "Stop"'
test "$(find "$workspace/.wiki/inbox/autosave" -type f -name '*-semantic-fallback.md' | wc -l | tr -d ' ')" -eq 1

echo 'semantic finalizer contract passed'
