#!/bin/sh
set -eu
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
mkdir "$test_root/workspace"
printf '%s' "{\"cwd\":\"$test_root/workspace\",\"hook_event_name\":\"SessionStart\"}" | python3 plugins/wiki-preflight/hooks/preflight.py >/dev/null
test -f "$test_root/workspace/.wiki/_index.md"
printf '%s' "{\"cwd\":\"$test_root/workspace\",\"hook_event_name\":\"Stop\"}" | python3 plugins/wiki-preflight/hooks/preflight.py >/dev/null
test -n "$(find "$test_root/workspace/.wiki/inbox/autosave" -type f)"
