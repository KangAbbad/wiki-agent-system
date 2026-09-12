#!/bin/sh
set -eu
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
mkdir "$test_root/workspace"
printf '%s' "{\"cwd\":\"$test_root/workspace\",\"hook_event_name\":\"SessionStart\"}" | python3 plugins/wiki-preflight/hooks/preflight.py >/dev/null
test -f "$test_root/workspace/.wiki/_index.md"
printf '%s' "{\"cwd\":\"$test_root/workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"clean\",\"turn_id\":\"one\"}" | python3 plugins/wiki-preflight/hooks/preflight.py >/dev/null
printf 'changed\n' >"$test_root/workspace/changed.txt"
printf '%s' "{\"cwd\":\"$test_root/workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"clean\",\"turn_id\":\"one\",\"last_assistant_message\":\"Completed clean-device verification\"}" | python3 plugins/wiki-preflight/hooks/preflight.py >"$test_root/stop.json"
! grep -q '"decision": "block"' "$test_root/stop.json"
test -n "$(find "$test_root/workspace/.wiki/inbox/autosave" -type f)"
