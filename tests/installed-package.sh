#!/bin/sh
set -eu
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
cp -R plugins/wiki-preflight "$root/plugin"
test -f "$root/plugin/skills/wiki-ambient/SKILL.md"
grep -q 'projectless work' "$root/plugin/skills/wiki-ambient/SKILL.md"
mkdir "$root/workspace"
printf '%s' "{\"cwd\":\"$root/workspace\",\"hook_event_name\":\"SessionStart\"}" | "$root/plugin/hooks/launcher.sh" "$root/plugin/hooks/preflight.py" >/dev/null
test -f "$root/workspace/.wiki/.wiki-agent-system.json"
printf '%s' "{\"cwd\":\"$root/workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"installed\",\"turn_id\":\"one\"}" | "$root/plugin/hooks/launcher.sh" "$root/plugin/hooks/preflight.py" >/dev/null
printf 'changed\n' >"$root/workspace/changed.txt"
printf '%s' "{\"cwd\":\"$root/workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"installed\",\"turn_id\":\"one\",\"last_assistant_message\":\"Completed installed verification\"}" | "$root/plugin/hooks/launcher.sh" "$root/plugin/hooks/preflight.py" >"$root/stop.json"
! grep -q '"decision": "block"' "$root/stop.json"
test -n "$(find "$root/workspace/.wiki/inbox/autosave" -type f)"
