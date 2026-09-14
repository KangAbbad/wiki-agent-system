#!/bin/sh
set -eu
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
mkdir "$test_root/workspace"
printf '%s' "{\"cwd\":\"$test_root/workspace\",\"hook_event_name\":\"SessionStart\"}" | plugins/wiki-preflight/hooks/launcher.sh plugins/wiki-preflight/hooks/preflight.py >/dev/null
test -f "$test_root/workspace/.wiki/_index.md"
test ! -e "$test_root/workspace/AGENTS.md"
test ! -e "$test_root/workspace/.gitignore"
printf '%s' "{\"cwd\":\"$test_root/workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"clean\",\"turn_id\":\"one\"}" | plugins/wiki-preflight/hooks/launcher.sh plugins/wiki-preflight/hooks/preflight.py >/dev/null
printf 'changed\n' >"$test_root/workspace/changed.txt"
printf '%s' "{\"cwd\":\"$test_root/workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"clean\",\"turn_id\":\"one\",\"last_assistant_message\":\"Completed clean-device verification\"}" | plugins/wiki-preflight/hooks/launcher.sh plugins/wiki-preflight/hooks/preflight.py >"$test_root/stop.json"
! grep -q '"decision": "block"' "$test_root/stop.json"
test -n "$(find "$test_root/workspace/.wiki/inbox/autosave" -type f)"
mkdir "$test_root/git-workspace"
git init -q "$test_root/git-workspace"
printf '%s\n' '# existing project rule' >"$test_root/git-workspace/.gitignore"
printf '%s' "{\"cwd\":\"$test_root/git-workspace\",\"hook_event_name\":\"SessionStart\"}" | plugins/wiki-preflight/hooks/launcher.sh plugins/wiki-preflight/hooks/preflight.py >/dev/null
test -f "$test_root/git-workspace/.wiki/_index.md"
test ! -e "$test_root/git-workspace/AGENTS.md"
git -C "$test_root/git-workspace" check-ignore -q .wiki/.sessions/runtime.json
test "$(grep -Fx '.wiki/.sessions/' "$test_root/git-workspace/.gitignore" | wc -l | tr -d ' ')" = 1
grep -Fx '# existing project rule' "$test_root/git-workspace/.gitignore" >/dev/null
