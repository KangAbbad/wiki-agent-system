#!/bin/sh
set -eu
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
cp -R plugins/wiki-preflight "$root/plugin"
test -f "$root/plugin/skills/wiki-ambient/SKILL.md"
grep -q 'projectless work' "$root/plugin/skills/wiki-ambient/SKILL.md"
test -x "$root/plugin/scripts/youtube_fallback.py"
test -x "$root/plugin/scripts/evidence_verification.py"
grep -Fxq 'youtube-transcript-api==1.2.4' "$root/plugin/requirements.txt"
test -f "$root/plugin/vendor/NOTICE.md"
test -f "$root/plugin/vendor/MANIFEST.sha256"
test -f "$root/plugin/vendor/LICENSES/youtube-transcript-api.LICENSE"
sh tests/vendor-runtime-import.sh "$root/plugin"
"$root/plugin/hooks/launcher.sh" "$root/plugin/scripts/youtube_fallback.py" self-test >/dev/null
"$root/plugin/hooks/launcher.sh" "$root/plugin/scripts/evidence_verification.py" self-test >/dev/null
grep -q 'install-approval-required' "$root/plugin/defaults/policy.md"
grep -q 'web-extraction' "$root/plugin/defaults/policy.md"
grep -q 'evidence lifecycle' "$root/plugin/defaults/policy.md"
grep -Fq '`UserPromptSubmit` owns ordinary YouTube ingestion' "$root/plugin/defaults/policy.md"
grep -Fq 'durable foreground controller' "$root/plugin/defaults/policy.md"
for document in \
  "$root/plugin/defaults/policy.md" \
  "$root/plugin/skills/wiki-ambient/SKILL.md" \
  "$root/plugin/skills/wiki-workspace/SKILL.md"; do
  ! grep -Fq '$PLUGIN_ROOT' "$document"
  ! grep -Fq '$PLUGIN_DATA' "$document"
  ! grep -Eq 'run.*semantic finalizer|execute.*semantic finalizer|stable launcher contract|capture --scope|python3.*wiki_ambient.py' "$document"
done
grep -q '"timeout": 45' "$root/plugin/hooks/hooks.json"
test "$(grep -c 'provision.py' "$root/plugin/hooks/hooks.json")" -eq 4
test "$(grep -c 'PLUGIN_DATA/current/hooks/preflight.py' "$root/plugin/hooks/hooks.json")" -eq 4
mkdir "$root/workspace"
printf '%s' "{\"cwd\":\"$root/workspace\",\"hook_event_name\":\"SessionStart\"}" | "$root/plugin/hooks/launcher.sh" "$root/plugin/hooks/preflight.py" >/dev/null
test -f "$root/workspace/.wiki/.sessions/wiki-agent-system/marker.json"
printf '%s' "{\"cwd\":\"$root/workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"installed\",\"turn_id\":\"one\"}" | "$root/plugin/hooks/launcher.sh" "$root/plugin/hooks/preflight.py" >/dev/null
printf 'changed\n' >"$root/workspace/changed.txt"
printf '%s' "{\"cwd\":\"$root/workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"installed\",\"turn_id\":\"one\",\"last_assistant_message\":\"Completed installed verification\"}" | "$root/plugin/hooks/launcher.sh" "$root/plugin/hooks/preflight.py" >"$root/stop.json"
! grep -q '"decision": "block"' "$root/stop.json"
test -n "$(find "$root/workspace/.wiki/inbox/autosave" -type f)"

printf '%s\n' 'Installed canonical evidence' >"$root/installed-source.md"
installed_result=$(HOME="$root/home" XDG_CONFIG_HOME="$root/config" \
  "$root/plugin/hooks/launcher.sh" "$root/plugin/scripts/wiki_ambient.py" canonicalize \
  --cwd "$root/workspace" --source "$root/installed-source.md" \
  --source-url 'https://example.test/installed' --title 'Installed source')
installed_raw=$(printf '%s' "$installed_result" | python3 -c 'import json,sys; print(json.load(sys.stdin)["path"])')
case "$installed_raw" in
  */raw/articles/*) ;;
  *) exit 1 ;;
esac
grep -q '^type: articles$' "$installed_raw"

printf '%s' "{\"cwd\":\"$root/workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"installed-durable\",\"turn_id\":\"one\",\"prompt\":\"Research and synthesize the installed runtime result\"}" | "$root/plugin/hooks/launcher.sh" "$root/plugin/hooks/preflight.py" >/dev/null
printf '%s' "{\"cwd\":\"$root/workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"installed-durable\",\"turn_id\":\"one\",\"last_assistant_message\":\"Completed durable installed verification\"}" | "$root/plugin/hooks/launcher.sh" "$root/plugin/hooks/preflight.py" >/dev/null
grep -R -q 'Completed durable installed verification' "$root/workspace/.wiki/inbox/autosave"
