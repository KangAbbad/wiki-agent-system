#!/bin/sh
# Contract: the injected policy requires semantic capture before final reply.
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
printf '%s\n' "$context" | grep -Fq "The Stop hook is crash/session fallback only. It cannot read the model's final response"
grep -Fq '### Required semantic finalizer' "$plugin_root/skills/wiki-team/SKILL.md"
grep -Fq 'Before sending the final response for meaningful workspace work, execute the' "$plugin_root/skills/wiki-team/SKILL.md"

echo 'semantic finalizer contract passed'
