#!/bin/sh
set -eu
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
cp -R plugins/wiki-preflight "$root/plugin"
mkdir "$root/workspace"
printf '%s' "{\"cwd\":\"$root/workspace\",\"hook_event_name\":\"SessionStart\"}" | python3 "$root/plugin/hooks/preflight.py" >/dev/null
test -f "$root/workspace/.wiki/.wiki-agent-system.json"
printf '%s' "{\"cwd\":\"$root/workspace\",\"hook_event_name\":\"Stop\"}" | python3 "$root/plugin/hooks/preflight.py" >/dev/null
test -n "$(find "$root/workspace/.wiki/inbox/autosave" -type f)"
