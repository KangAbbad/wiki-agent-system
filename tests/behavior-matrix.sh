#!/bin/sh
# Portable behavior matrix. Runs against a copied plugin, never user-home state.
set -eu

plugin_root=${1:-plugins/wiki-preflight}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
cp -R "$plugin_root" "$test_root/plugin"
export HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" CODEX_HOME="$test_root/codex"
export MNEMOSYNE_CLI="$test_root/mnemosyne-not-installed" TMPDIR="$test_root/tmp"
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$CODEX_HOME" "$TMPDIR"
hook="$test_root/plugin/hooks/preflight.py"
launcher="$test_root/plugin/hooks/launcher.sh"
workspace="$test_root/non-git-workspace"
mkdir "$workspace"

run_hook() {
  event=$1
  printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"$event\"}" | "$launcher" "$hook"
}

# Projectless/home work initializes only the User Wiki, never HOME/.wiki.
printf '%s' "{\"cwd\":\"$HOME\",\"hook_event_name\":\"SessionStart\"}" | "$launcher" "$hook" >"$test_root/home-start.json"
test -f "$HOME/wiki/_index.md"
test ! -e "$HOME/.wiki"
home_prompt=$(printf '%s' "{\"cwd\":\"$HOME\",\"hook_event_name\":\"UserPromptSubmit\",\"prompt\":\"hello\"}" | "$launcher" "$hook")
printf '%s' "$home_prompt" | grep -q 'Wiki check: checked-no-match'

# Empty, non-Git workspace bootstraps a valid schema-owned wiki.
run_hook SessionStart >"$test_root/start.json"
test -f "$workspace/.wiki/.sessions/wiki-agent-system/marker.json"
test -f "$workspace/.wiki/_index.md"
test ! -d "$workspace/.git"
grep -q 'schema_version' "$workspace/.wiki/.sessions/wiki-agent-system/marker.json"

# A relevant pending research capture from one session appears in the next preflight context.
mkdir -p "$workspace/.wiki/inbox/autosave"
printf '%s\n' '---' 'status: pending-curation' '---' '# Research result' 'Use bounded retries for remote calls.' >"$workspace/.wiki/inbox/autosave/research.md"
printf '%s' '{"cwd":"'"$workspace"'","hook_event_name":"UserPromptSubmit","prompt":"bounded retries for remote calls"}' | "$launcher" "$hook" >"$test_root/research.json"
grep -q 'bounded retries' "$test_root/research.json"
printf '%s' '{"cwd":"'"$workspace"'","hook_event_name":"UserPromptSubmit","prompt":"unrelated horticulture"}' | "$launcher" "$hook" >"$test_root/unrelated.json"
! grep -q 'bounded retries' "$test_root/unrelated.json"

# Stop owns durable prompt capture even without workspace mutation and stays unique.
printf '%s' '{"cwd":"'"$workspace"'","hook_event_name":"UserPromptSubmit","session_id":"matrix","turn_id":"one","prompt":"Synthesize the migration decision"}' | "$launcher" "$hook" >/dev/null
printf '%s' '{"cwd":"'"$workspace"'","hook_event_name":"Stop","session_id":"matrix","turn_id":"one","last_assistant_message":"Completed one"}' | "$launcher" "$hook" >"$test_root/stop-one.json"
! grep -q '"decision": "block"' "$test_root/stop-one.json"
test "$(find "$workspace/.wiki/inbox/autosave" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 1
printf '%s' '{"cwd":"'"$workspace"'","hook_event_name":"Stop","session_id":"matrix","turn_id":"one","last_assistant_message":"Repeated stop"}' | "$launcher" "$hook" >/dev/null
test "$(find "$workspace/.wiki/inbox/autosave" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 1

printf '%s' '{"cwd":"'"$workspace"'","hook_event_name":"UserPromptSubmit","session_id":"matrix-two","turn_id":"one"}' | "$launcher" "$hook" >/dev/null
printf 'two\n' >"$workspace/two.txt"
printf '%s' '{"cwd":"'"$workspace"'","hook_event_name":"Stop","session_id":"matrix-two","turn_id":"one","last_assistant_message":"Completed two"}' | "$launcher" "$hook" >/dev/null
test "$(find "$workspace/.wiki/inbox/autosave" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 2

casual="$test_root/casual"
mkdir "$casual"
printf '%s' "{\"cwd\":\"$casual\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"casual\",\"turn_id\":\"one\",\"prompt\":\"What is two plus two?\"}" | "$launcher" "$hook" >/dev/null
printf '%s' "{\"cwd\":\"$casual\",\"hook_event_name\":\"Stop\",\"session_id\":\"casual\",\"turn_id\":\"one\",\"last_assistant_message\":\"4\"}" | "$launcher" "$hook" >/dev/null
test "$(find "$casual/.wiki/inbox" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 0

# Docs routing is content policy, not a folder-name heuristic.
policy="$test_root/plugin/defaults/policy.md"
grep -q 'knowledge artifacts default to Wiki `output/`' "$policy"
grep -q 'explicit product/developer documentation' "$policy"
grep -q 'Public vendor, database' "$policy"
grep -q 'Evidence lifecycle' "$policy"
grep -q 'knowledge_readiness=ready' "$policy"
grep -q 'never run `llm-wiki lint --fix`' "$policy"
grep -q 'routine user task' "$policy"
! grep -q 'run.*semantic finalizer\|python3.*wiki_ambient.py' "$policy"

# The marketplace package includes the global policy required for projectless
# user-scope work; a device-local skill is not a dependency.
ambient="$test_root/plugin/skills/wiki-ambient/SKILL.md"
test -f "$ambient"
grep -q 'projectless work' "$ambient"
grep -q 'routine user verification' "$ambient"

# A public gap remains agent-owned; a real boundary gets one concrete note.
gap_prompt=$(printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"boundary\",\"turn_id\":\"one\",\"prompt\":\"Verify production drop database docs https://example.test/spec\"}" | "$launcher" "$hook")
printf '%s' "$gap_prompt" | grep -q 'Usage note: treat this as a production mutation'
printf '%s' "$gap_prompt" | grep -q 'Source material is unavailable'
! printf '%s' "$gap_prompt" | grep -q 'Skipped'

# Retention dry-run finds expired autosaves; apply quarantines without deleting.
old="$workspace/.wiki/inbox/autosave/old.md"
printf 'old\n' >"$old"
touch -t 202001010000 "$old"
"$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/retention.py" "$workspace" >"$test_root/retention-dry.json"
grep -q 'old.md' "$test_root/retention-dry.json"
test -f "$old"
"$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/retention.py" "$workspace" --apply >"$test_root/retention-apply.json"
test -f "$workspace/.wiki/.trash/autosave/old.md"
test ! -f "$old"

# Existing valid wiki without marker upgrades additively by adding only runtime state.
rm "$workspace/.wiki/.sessions/wiki-agent-system/marker.json"
run_hook SessionStart >/dev/null
test -f "$workspace/.wiki/.sessions/wiki-agent-system/marker.json"
test -f "$workspace/.wiki/inbox/autosave/research.md"

# A foreign .wiki is not initialized, written, or captured; independent User Wiki retrieval still runs.
foreign="$test_root/foreign-workspace"
mkdir -p "$foreign/.wiki"
printf 'foreign\n' >"$foreign/.wiki/marker"
printf '%s' "{\"cwd\":\"$foreign\",\"hook_event_name\":\"SessionStart\"}" | "$launcher" "$hook" >"$test_root/foreign.json"
grep -q 'Workspace Wiki was not initialized because its location is foreign' "$test_root/foreign.json"
test -f "$foreign/.wiki/marker"
test ! -e "$foreign/.wiki/_index.md"
printf '%s' "{\"cwd\":\"$foreign\",\"hook_event_name\":\"Stop\"}" | "$launcher" "$hook" >/dev/null
test ! -d "$foreign/.wiki/inbox"

# Coexistence shape: this plugin owns each lifecycle event once and does not
# invoke, patch, or name the upstream wiki plugin in its hook configuration.
hooks="$test_root/plugin/hooks/hooks.json"
test "$(grep -o 'current/hooks/preflight.py' "$hooks" | wc -l | tr -d ' ')" -eq 6
test "$(grep -o 'provision.py' "$hooks" | wc -l | tr -d ' ')" -eq 6
! grep -q 'wiki@llm-wiki\|llm-wiki' "$hooks"

echo 'behavior matrix passed'
