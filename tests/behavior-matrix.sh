#!/bin/sh
# Portable behavior matrix. Runs against a copied plugin, never user-home state.
set -eu

plugin_root=${1:-plugins/wiki-preflight}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
cp -R "$plugin_root" "$test_root/plugin"
hook="$test_root/plugin/hooks/preflight.py"
workspace="$test_root/non-git-workspace"
mkdir "$workspace"

run_hook() {
  event=$1
  printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"$event\"}" | python3 "$hook"
}

# Empty, non-Git workspace bootstraps a valid schema-owned wiki.
run_hook SessionStart >"$test_root/start.json"
test -f "$workspace/.wiki/.wiki-agent-system.json"
test -f "$workspace/.wiki/_index.md"
test ! -d "$workspace/.git"
grep -q 'schema_version' "$workspace/.wiki/.wiki-agent-system.json"

# A research capture from one session appears in the next preflight context.
mkdir -p "$workspace/.wiki/inbox/autosave"
printf '%s\n' '# Research result' 'Use bounded retries for remote calls.' >"$workspace/.wiki/inbox/autosave/research.md"
run_hook UserPromptSubmit >"$test_root/research.json"
grep -q 'bounded retries' "$test_root/research.json"

# Stop auto-capture preserves uniqueness even inside one timestamp second.
run_hook Stop >/dev/null
run_hook Stop >/dev/null
test "$(find "$workspace/.wiki/inbox/autosave" -name '*-session.md' | wc -l | tr -d ' ')" -eq 2

# Docs routing is content policy, not a folder-name heuristic.
policy="$test_root/plugin/defaults/policy.md"
grep -q 'knowledge artifacts in `.wiki/`' "$policy"
grep -q 'explicit product/developer documentation' "$policy"

# Retention dry-run finds expired autosaves; apply quarantines without deleting.
old="$workspace/.wiki/inbox/autosave/old.md"
printf 'old\n' >"$old"
touch -t 202001010000 "$old"
python3 "$test_root/plugin/scripts/retention.py" "$workspace" >"$test_root/retention-dry.json"
grep -q 'old.md' "$test_root/retention-dry.json"
test -f "$old"
python3 "$test_root/plugin/scripts/retention.py" "$workspace" --apply >"$test_root/retention-apply.json"
test -f "$workspace/.wiki/.trash/autosave/old.md"
test ! -f "$old"

# Existing valid wiki without marker upgrades additively by adding only marker.
rm "$workspace/.wiki/.wiki-agent-system.json"
run_hook SessionStart >/dev/null
test -f "$workspace/.wiki/.wiki-agent-system.json"
test -f "$workspace/.wiki/inbox/autosave/research.md"

# A foreign .wiki is not initialized, written, or captured.
foreign="$test_root/foreign-workspace"
mkdir -p "$foreign/.wiki"
printf 'foreign\n' >"$foreign/.wiki/marker"
printf '%s' "{\"cwd\":\"$foreign\",\"hook_event_name\":\"SessionStart\"}" | python3 "$hook" >"$test_root/foreign.json"
grep -q 'Foreign/incomplete wiki' "$test_root/foreign.json"
test -f "$foreign/.wiki/marker"
test ! -e "$foreign/.wiki/_index.md"
printf '%s' "{\"cwd\":\"$foreign\",\"hook_event_name\":\"Stop\"}" | python3 "$hook" >/dev/null
test ! -d "$foreign/.wiki/inbox"

# Coexistence shape: this plugin owns each lifecycle event once and does not
# invoke, patch, or name the upstream wiki plugin in its hook configuration.
hooks="$test_root/plugin/hooks/hooks.json"
test "$(grep -o 'preflight.py' "$hooks" | wc -l | tr -d ' ')" -eq 3
! grep -q 'wiki@llm-wiki\|llm-wiki' "$hooks"

echo 'behavior matrix passed'
