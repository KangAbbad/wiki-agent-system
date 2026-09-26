#!/bin/sh
# P1 release gate. It must be run from any directory after a clean checkout.
set -u

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_codex_home=${CODEX_HOME:-${HOME}/.codex}
llm_wiki_bin=${LLM_WIKI_BIN:-}
if [ -z "$llm_wiki_bin" ]; then
  llm_wiki_bin=$(command -v llm-wiki 2>/dev/null || true)
fi
if [ -z "$llm_wiki_bin" ] && [ -d "$source_codex_home/plugins/cache/llm-wiki" ]; then
  llm_wiki_bin=$(find "$source_codex_home/plugins/cache/llm-wiki" -type f -path '*/bin/llm-wiki' -perm -111 -print -quit 2>/dev/null || true)
fi
if [ -z "$llm_wiki_bin" ] || [ ! -x "$llm_wiki_bin" ]; then
  printf '%s\n' 'P1 acceptance requires an executable llm-wiki binary. Set LLM_WIKI_BIN, put llm-wiki on PATH, or install it under CODEX_HOME.' >&2
  exit 2
fi
LLM_WIKI_BIN=$llm_wiki_bin
export LLM_WIKI_BIN
test_environment=$(mktemp -d)
trap 'rm -rf "$test_environment"' EXIT
export HOME="$test_environment/home" XDG_CONFIG_HOME="$test_environment/config" CODEX_HOME="$test_environment/codex"
export MNEMOSYNE_CLI="$test_environment/mnemosyne-not-installed" TMPDIR="$test_environment/tmp"
export WIKI_TEST_CODEX_AUTH_FILE="$source_codex_home/auth.json"
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$CODEX_HOME" "$TMPDIR"
failed=0
check_index=0

run_check() {
  if [ "${WIKI_P1_TRACE:-0}" != 1 ]; then
    "$@"
    return
  fi
  case "$1" in
    sh)
      shift
      sh -x "$@"
      ;;
    from_root)
      shift
      from_root "$@"
      ;;
    *)
      (set -x; "$@")
      ;;
  esac
}

check() {
  name=$1
  shift
  check_index=$((check_index + 1))
  output="$test_environment/check-$check_index.log"
  if run_check "$@" >"$output" 2>&1; then
    printf 'PASS %s\n' "$name"
  else
    status=$?
    printf 'FAIL %s (exit %s)\n' "$name" "$status" >&2
    tail -n 80 "$output" >&2
    failed=1
  fi
}

semantic_and_evidence() (
  test_root=$(mktemp -d)
  trap 'rm -rf "$test_root"' EXIT
  cp -R "$root/plugins/wiki-preflight" "$test_root/plugin"
  workspace="$test_root/workspace"
  mkdir -p "$workspace/.wiki/raw" "$workspace/.wiki/wiki" "$workspace/.wiki/inbox"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/config.md"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/_index.md"

  result=$("$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" capture \
    --cwd "$workspace" \
    --outcome 'Delivered token=super-secret safely' \
    --kind result \
    --artifact src/main.py \
    --decision 'Use atomic writes' \
    --verification 'tests passed' \
    --source 'https://example.test/spec' \
    --confidence high \
    --open-question 'None')
  printf '%s' "$result" | grep -q 'pending-curation'
  capture=$(find "$workspace/.wiki/inbox/autosave" -type f -name 'session-*.md')
  test -n "$capture"
  grep -q 'token=\[REDACTED\]' "$capture"
  ! grep -q 'super-secret' "$capture"

  source="$test_root/source.md"
  printf '%s\n' '# Attributable evidence' >"$source"
  result=$("$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" canonicalize \
    --cwd "$workspace" --source "$source" \
    --source-url 'https://example.test/spec' --title 'Official spec')
  printf '%s' "$result" | grep -q 'canonical-evidence'
  raw=$(find "$workspace/.wiki/raw/articles" -maxdepth 1 -type f -name 'official-spec-*.md')
  test -n "$raw"
  grep -q '^type: articles$' "$raw"
  grep -q '^source: "https://example.test/spec"$' "$raw"
  grep -q '^ingested: ' "$raw"
  grep -q '^summary: "Wiki Preflight web-extraction evidence; lifecycle=unverified\."$' "$raw"
  grep -q '^knowledge_readiness: ready$' "$raw"
  grep -q '^tags: \[wiki-preflight, evidence, web-extraction\]$' "$raw"
  grep -q 'content_sha256:' "$raw"
  grep -q '^status: canonical$' "$raw"
  grep -q '^valid_until: null$' "$raw"
  grep -q '^canonical_uri: "wiki://workspace/' "$raw"
  test -z "$(find "$workspace/.wiki/raw" -maxdepth 1 -type f -name '*.md' ! -name '_index.md' -print)"

  caption="$workspace/.wiki/inbox/youtube/p1.vtt"
  mkdir -p "$(dirname "$caption")"
  printf '%s\n' 'WEBVTT' '' '00:00.000 --> 00:01.000' 'P1 caption' >"$caption"
  ! HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" canonicalize \
    --cwd "$workspace" --source "$caption" \
    --source-url 'https://youtu.be/dQw4w9WgXcQ?si=p1' --title 'P1 Missing Receipt' >/dev/null 2>&1

  unsafe="$test_root/unsafe.md"
  printf '%s\n' 'api_key=not-for-wiki' >"$unsafe"
  ! "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" canonicalize \
    --cwd "$workspace" --source "$unsafe" \
    --source-url 'https://example.test/unsafe' --title 'Unsafe evidence' >/dev/null 2>&1
)

lifecycle_and_retention() (
  test_root=$(mktemp -d)
  trap 'rm -rf "$test_root"' EXIT
  cp -R "$root/plugins/wiki-preflight" "$test_root/plugin"
  config_home="$test_root/config"
  workspace="$test_root/workspace"
  export XDG_CONFIG_HOME="$config_home"
  mkdir -p "$workspace/.wiki/raw" "$workspace/.wiki/wiki" "$workspace/.wiki/output" "$workspace/.wiki/inbox/autosave" "$workspace/.wiki/.sessions"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/config.md"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/_index.md"

  capture() {
    session=$1
    outcome=$2
    CODEX_SESSION_ID="$session" "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" capture \
      --cwd "$workspace" --outcome "$outcome" --kind decision
  }
  transition() {
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" transition --cwd "$workspace" "$@"
  }

  old_key=$(python3 -c 'import hashlib; print(hashlib.sha256(b"old-decision").hexdigest()[:16])')
  capture old-decision 'Old decision' >/dev/null
  old="$workspace/.wiki/inbox/autosave/session-$old_key.md"
  old_uri=$(sed -n 's/^canonical_uri: "\(.*\)"$/\1/p' "$old")
  test -f "$old"
  grep -q '^status: pending-curation$' "$old"
  grep -q '^confidence: unverified$' "$old"
  ! transition --record "$old" --status superseded >/dev/null 2>&1
  transition --record "$old" --status canonical >/dev/null
  old_canonical="$workspace/.wiki/wiki/captures/session-$old_key.md"
  test ! -e "$old"
  test -f "$old_canonical"
  grep -q '^status: canonical$' "$old_canonical"
  grep -q "^canonical_uri: \"$old_uri\"$" "$old_canonical"
  grep -q 'Old decision' "$old_canonical"
  ! transition --record "$old_canonical" --status canonical >/dev/null 2>&1

  new_key=$(python3 -c 'import hashlib; print(hashlib.sha256(b"new-decision").hexdigest()[:16])')
  capture new-decision 'New decision' >/dev/null
  new="$workspace/.wiki/inbox/autosave/session-$new_key.md"
  new_uri=$(sed -n 's/^canonical_uri: "\(.*\)"$/\1/p' "$new")
  fake_uri='wiki://workspace/capture/not-a-real-origin/not-a-real-record'
  ! transition --record "$old_canonical" --status superseded --supersedes "$fake_uri" >/dev/null 2>&1
  ! transition --record "$old_canonical" --status superseded --supersedes "$new_uri" >/dev/null 2>&1
  grep -q '^status: canonical$' "$old_canonical"
  transition --record "$new" --status canonical >/dev/null
  new_canonical="$workspace/.wiki/wiki/captures/session-$new_key.md"
  transition --record "$old_canonical" --status superseded --supersedes "$new_uri" >/dev/null
  grep -q '^status: superseded$' "$old_canonical"
  grep -q "^supersedes: \"$new_uri\"$" "$old_canonical"
  grep -q '^valid_until: [0-9]' "$old_canonical"
  test -f "$old_canonical"
  test -f "$new_canonical"
  grep -q '^status: canonical$' "$new_canonical"
  ! transition --record "$old_canonical" --status retracted --supersedes "$new_uri" >/dev/null 2>&1
  ! transition --record "$new_canonical" --status superseded --supersedes "$new_uri" >/dev/null 2>&1

  printf '%s\n' 'canonical raw' >"$workspace/.wiki/raw/keep.md"
  printf '%s\n' 'canonical article' >"$workspace/.wiki/wiki/keep.md"
  printf '%s\n' 'canonical output' >"$workspace/.wiki/output/keep.md"
  printf '%s\n' 'expired operational' >"$workspace/.wiki/inbox/autosave/old.md"
  printf '%s\n' 'expired state' >"$workspace/.wiki/.sessions/old.json"
  touch -t 202001010000 "$workspace/.wiki/raw/keep.md" "$workspace/.wiki/wiki/keep.md" "$workspace/.wiki/output/keep.md" \
    "$workspace/.wiki/inbox/autosave/old.md" "$workspace/.wiki/.sessions/old.json"
  "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/retention.py" "$workspace" --apply --autosave-days 1 --state-days 1 --max-bytes 1 >/dev/null
  test -f "$workspace/.wiki/.trash/autosave/old.md"
  test -f "$workspace/.wiki/.trash/state/old.json"
  test -f "$workspace/.wiki/raw/keep.md"
  test -f "$workspace/.wiki/wiki/keep.md"
  test -f "$workspace/.wiki/output/keep.md"
)

retention_default_days() (
  set -eu
  test_root=$(mktemp -d)
  trap 'rm -rf "$test_root"' EXIT
  cp -R "$root/plugins/wiki-preflight" "$test_root/plugin"
  workspace="$test_root/workspace"
  mkdir -p "$workspace/.wiki/raw" "$workspace/.wiki/wiki" "$workspace/.wiki/inbox/autosave" \
    "$workspace/.wiki/.trash/autosave"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/config.md"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/_index.md"
  printf '%s\n' 'expired autosave' >"$workspace/.wiki/inbox/autosave/old.md"
  printf '%s\n' 'quarantined data' >"$workspace/.wiki/.trash/autosave/old.md"
  touch -t 202001010000 "$workspace/.wiki/inbox/autosave/old.md"
  touch -t 202001010000 "$workspace/.wiki/.trash/autosave/old.md"
  report=$(XDG_CONFIG_HOME="$test_root/config" "$test_root/plugin/hooks/launcher.sh" \
    "$test_root/plugin/scripts/retention.py" "$workspace" --apply)
  printf '%s' "$report" | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["purge"]["older_than_days"] == 7; assert not data["purge"]["enabled"]'
  test -f "$workspace/.wiki/.trash/autosave/old.md"
)

scheduled_retention() (
  set -eu
  test_root=$(mktemp -d)
  trap 'rm -rf "$test_root"' EXIT
  cp -R "$root/plugins/wiki-preflight" "$test_root/plugin"
  launcher="$test_root/plugin/hooks/launcher.sh"
  hook="$test_root/plugin/hooks/preflight.py"
  export HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config"

  make_wiki() {
    workspace=$1
    mkdir -p "$workspace/.wiki/raw" "$workspace/.wiki/wiki"
    printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/config.md"
    printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/_index.md"
  }
  expire() {
    file=$1
    printf '%s\n' expired >"$file"
    touch -t 202001010000 "$file"
  }
  run_start() {
    workspace=$1
    printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"SessionStart\"}" | "$launcher" "$hook"
  }

  due="$test_root/due"
  make_wiki "$due"
  mkdir -p "$due/.wiki/inbox/autosave" "$due/.wiki/.sessions"
  expire "$due/.wiki/inbox/autosave/old.md"
  expire "$due/.wiki/.sessions/old.json"
  run_start "$due" >/dev/null
  test -f "$due/.wiki/.trash/autosave/old.md"
  test -f "$due/.wiki/.trash/state/old.json"
  test -f "$due/.wiki/.sessions/retention.json"
  test ! -e "$due/.gitignore"

  non_due="$test_root/non-due"
  make_wiki "$non_due"
  mkdir -p "$non_due/.wiki/inbox/autosave"
  run_start "$non_due" >/dev/null
  expire "$non_due/.wiki/inbox/autosave/old.md"
  run_start "$non_due" >/dev/null
  test -f "$non_due/.wiki/inbox/autosave/old.md"
  test ! -e "$non_due/.wiki/.trash/autosave/old.md"

  event="$test_root/event"
  make_wiki "$event"
  mkdir -p "$event/.wiki/inbox/autosave"
  expire "$event/.wiki/inbox/autosave/old.md"
  printf '%s' "{\"cwd\":\"$event\",\"hook_event_name\":\"UserPromptSubmit\"}" | "$launcher" "$hook" >/dev/null
  test -f "$event/.wiki/inbox/autosave/old.md"
  test ! -e "$event/.wiki/.trash/autosave/old.md"

  locked="$test_root/locked"
  make_wiki "$locked"
  mkdir -p "$locked/.wiki/inbox/autosave" "$locked/.wiki/.sessions"
  expire "$locked/.wiki/inbox/autosave/old.md"
  lock="$locked/.wiki/.sessions/retention.lock"
  ready="$test_root/lock-ready"
  python3 - "$lock" "$ready" <<'PY' &
import fcntl
import os
import sys
import time

fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
open(sys.argv[2], "w").close()
time.sleep(2)
PY
  holder=$!
  while [ ! -f "$ready" ]; do sleep 0.01; done
  run_start "$locked" >/dev/null
  test -f "$locked/.wiki/inbox/autosave/old.md"
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true

  malformed="$test_root/malformed"
  make_wiki "$malformed"
  mkdir -p "$malformed/.wiki/inbox/autosave" "$malformed/.wiki/.sessions"
  printf '%s\n' '{"schema_version":99}' >"$malformed/.wiki/.sessions/retention.json"
  expire "$malformed/.wiki/inbox/autosave/old.md"
  before=$(cat "$malformed/.wiki/.sessions/retention.json")
  run_start "$malformed" >/dev/null
  test -f "$malformed/.wiki/inbox/autosave/old.md"
  test "$(cat "$malformed/.wiki/.sessions/retention.json")" = "$before"

  foreign="$test_root/foreign"
  mkdir -p "$foreign/.wiki"
  printf '%s\n' foreign >"$foreign/.wiki/marker"
  run_start "$foreign" >/dev/null
  test -f "$foreign/.wiki/marker"
  test ! -e "$foreign/.wiki/.sessions"
  test ! -e "$foreign/.wiki/.trash"

  timeout_workspace="$test_root/timeout"
  make_wiki "$timeout_workspace"
  cat >"$test_root/plugin/scripts/retention.py" <<'PY'
import time
time.sleep(10)
PY
  run_start "$timeout_workspace" >/dev/null
  CODEX_SESSION_ID=timeout "$launcher" "$test_root/plugin/scripts/wiki_ambient.py" capture \
    --cwd "$timeout_workspace" --outcome 'capture remains available' --kind result >/dev/null
  test -n "$(find "$timeout_workspace/.wiki/inbox/autosave" -type f -name 'session-*.md')"
)

storage_accounting() (
  set -eu
  test_root=$(mktemp -d)
  trap 'rm -rf "$test_root"' EXIT
  cp -R "$root/plugins/wiki-preflight" "$test_root/plugin"
  launcher="$test_root/plugin/hooks/launcher.sh"
  export HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config"
  workspace="$test_root/workspace"
  mkdir -p "$workspace/.wiki/raw" "$workspace/.wiki/wiki" "$workspace/.wiki/output" \
    "$workspace/.wiki/inbox/autosave" "$workspace/.wiki/.sessions" "$workspace/.wiki/.trash/autosave"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/config.md"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/_index.md"
  printf '%s\n' canonical >"$workspace/.wiki/raw/keep.md"
  printf '%s\n' canonical >"$workspace/.wiki/wiki/keep.md"
  printf '%s\n' canonical >"$workspace/.wiki/output/keep.md"
  printf '%s\n' active >"$workspace/.wiki/inbox/autosave/current.md"
  printf '%s\n' quarantined >"$workspace/.wiki/.trash/autosave/old.md"
  printf '%s\n' expired >"$workspace/.wiki/inbox/autosave/expired.md"
  touch -t 202001010000 "$workspace/.wiki/raw/keep.md" "$workspace/.wiki/wiki/keep.md" \
    "$workspace/.wiki/output/keep.md" "$workspace/.wiki/inbox/autosave/expired.md"

  before=$("$launcher" "$test_root/plugin/scripts/retention.py" "$workspace" --max-bytes 1)
  printf '%s' "$before" | python3 -c 'import json,sys; data=json.load(sys.stdin); storage=data["storage"]; paths=sum(data["expired_operational_files"].values(), []); assert storage["active_autosave_bytes"] > 0; assert storage["quarantine_bytes"] > 0; assert data["quota"]["used_bytes"] == storage["total_bytes"]; assert data["quota"]["basis"] == "total_bytes"; assert all(part not in path for path in paths for part in ("/raw/", "/wiki/", "/output/")); assert len(data["diagnostics"]) == 1'
  before_total=$(printf '%s' "$before" | python3 -c 'import json,sys; print(json.load(sys.stdin)["storage"]["total_bytes"])')
  apply=$("$launcher" "$test_root/plugin/scripts/retention.py" "$workspace" --apply --autosave-days 1 --state-days 1 --max-bytes 1)
  after_total=$(printf '%s' "$apply" | python3 -c 'import json,sys; print(json.load(sys.stdin)["storage"]["total_bytes"])')
  test "$before_total" = "$after_total"
  test -f "$workspace/.wiki/.trash/autosave/expired.md"
  test -f "$workspace/.wiki/raw/keep.md"
  test -f "$workspace/.wiki/wiki/keep.md"
  test -f "$workspace/.wiki/output/keep.md"

  capture=$("$launcher" "$test_root/plugin/scripts/wiki_ambient.py" capture --cwd "$workspace" \
    --outcome 'bounded storage capture' --kind result)
  printf '%s' "$capture" | python3 -c 'import json,sys; data=json.load(sys.stdin); quota=data["quota"]; storage=quota["storage"]; assert quota["used_bytes"] == storage["total_bytes"]; assert quota["basis"] == "total_bytes"; assert storage["quarantine_bytes"] > 0; assert len(quota["diagnostics"]) == 1; assert len(json.dumps(data).encode()) < 4000'
)

purge_policy() (
  set -eu
  test_root=$(mktemp -d)
  trap 'rm -rf "$test_root"' EXIT
  cp -R "$root/plugins/wiki-preflight" "$test_root/plugin"
  launcher="$test_root/plugin/hooks/launcher.sh"
  retention="$test_root/plugin/scripts/retention.py"
  export HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config"

  make_wiki() {
    workspace=$1
    mkdir -p "$workspace/.wiki/raw" "$workspace/.wiki/wiki" "$workspace/.wiki/output" \
      "$workspace/.wiki/inbox/autosave" "$workspace/.wiki/.sessions" \
      "$workspace/.wiki/.trash/autosave" "$workspace/.wiki/.trash/state"
    printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/config.md"
    printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/_index.md"
  }

  workspace="$test_root/workspace"
  make_wiki "$workspace"
  printf '%s\n' canonical >"$workspace/.wiki/raw/keep.md"
  printf '%s\n' canonical >"$workspace/.wiki/wiki/keep.md"
  printf '%s\n' canonical >"$workspace/.wiki/output/keep.md"
  printf '%s\n' active >"$workspace/.wiki/inbox/autosave/active.md"
  printf '%s\n' recent >"$workspace/.wiki/.trash/autosave/recent.md"
  printf '%s\n' old >"$workspace/.wiki/.trash/autosave/old.md"
  printf '%s\n' old-state >"$workspace/.wiki/.trash/state/old.json"
  touch -t 202001010000 "$workspace/.wiki/inbox/autosave/active.md" \
    "$workspace/.wiki/.trash/autosave/old.md" "$workspace/.wiki/.trash/state/old.json" \
    "$workspace/.wiki/raw/keep.md" "$workspace/.wiki/wiki/keep.md" "$workspace/.wiki/output/keep.md"

  report=$("$launcher" "$retention" "$workspace" --apply --scheduled \
    --autosave-days 1 --state-days 1)
  printf '%s' "$report" | python3 -c 'import json,sys; data=json.load(sys.stdin); purge=data["purge"]; summary=purge["summary"]; assert purge["enabled"]; assert purge["older_than_days"] == 7; assert summary["files"] == 2; assert summary["bytes"] > 0; assert summary["errors"] == 0; assert summary["by_category"]["autosave"]["files"] == 1; assert summary["by_category"]["state"]["files"] == 1; assert "path" not in json.dumps(purge); assert len(json.dumps(purge).encode()) < 1000'
  test ! -e "$workspace/.wiki/.trash/autosave/old.md"
  test ! -e "$workspace/.wiki/.trash/state/old.json"
  test -f "$workspace/.wiki/.trash/autosave/active.md"
  test -f "$workspace/.wiki/.trash/autosave/recent.md"
  test ! -e "$workspace/.wiki/inbox/autosave/active.md"
  test -f "$workspace/.wiki/raw/keep.md"
  test -f "$workspace/.wiki/wiki/keep.md"
  test -f "$workspace/.wiki/output/keep.md"

  locked="$test_root/locked"
  make_wiki "$locked"
  printf '%s\n' locked >"$locked/.wiki/.trash/autosave/old.md"
  touch -t 202001010000 "$locked/.wiki/.trash/autosave/old.md"
  printf '%s\n' '{"schema_version":1,"last_run_at":0}' >"$locked/.wiki/.sessions/retention.json"
  lock="$locked/.wiki/.sessions/retention.lock"
  ready="$test_root/purge-lock-ready"
  python3 - "$lock" "$ready" <<'PY' &
import fcntl
import os
import sys
import time

fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
open(sys.argv[2], "w").close()
time.sleep(2)
PY
  holder=$!
  while [ ! -f "$ready" ]; do sleep 0.01; done
  locked_report=$("$launcher" "$retention" "$locked" --apply --scheduled --trash-days 7)
  printf '%s' "$locked_report" | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["skip_reason"] == "lock-timeout"; assert data["purge"]["summary"]["files"] == 0'
  test -f "$locked/.wiki/.trash/autosave/old.md"
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true

  outside="$test_root/outside"
  mkdir -p "$outside"
  printf '%s\n' outside >"$outside/keep.md"
  symlinked="$test_root/symlinked"
  make_wiki "$symlinked"
  rmdir "$symlinked/.wiki/.trash/state"
  ln -s "$outside" "$symlinked/.wiki/.trash/state"
  symlink_report=$("$launcher" "$retention" "$symlinked" --apply --scheduled --trash-days 7)
  printf '%s' "$symlink_report" | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["purge"]["summary"]["files"] == 0; assert data["purge"]["summary"]["errors"] == 1'
  test -f "$outside/keep.md"

  python3 - "$retention" "$test_root/utime-failure" <<'PY'
import importlib.util
import os
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("retention", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
workspace = Path(sys.argv[2])
source = workspace / ".wiki/inbox/autosave/old.md"
source.parent.mkdir(parents=True)
source.write_text("preserve restore window\n")
original = module.os.utime
module.os.utime = lambda *_: (_ for _ in ()).throw(OSError("injected"))
try:
    try:
        module.quarantine(workspace / ".wiki", {"autosave": [source], "state": []})
    except OSError:
        pass
finally:
    module.os.utime = original
assert source.is_file()
assert not (workspace / ".wiki/.trash/autosave/old.md").exists()
PY
)

adapter_and_retrieval() (
  set -e
  test_root=$(mktemp -d)
  trap 'rm -rf "$test_root"' EXIT
  cp -R "$root/plugins/wiki-preflight" "$test_root/plugin"
  config_home="$test_root/config"
  test_home="$test_root/home-alias"
  test_home_target="$test_root/home-target"
  workspace="$test_root/workspace"
  fake="$test_root/fake-mnemosyne"
  log="$fake.log"
  mkdir -p "$test_home_target"
  ln -s "$test_home_target" "$test_home"
  export HOME="$test_home" XDG_CONFIG_HOME="$config_home"
  mkdir -p "$workspace/.wiki/raw" "$workspace/.wiki/wiki" "$workspace/.wiki/inbox/autosave" \
    "$test_home/wiki/raw" "$test_home/wiki/wiki" "$test_home/wiki/topics/shared/wiki"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/config.md"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/_index.md"
  # A pre-existing User Wiki must be valid; incomplete roots stay read-only.
  printf '%s\n' '# User Wiki' >"$test_home/wiki/_index.md"
  cat >"$fake" <<'EOF'
#!/bin/sh
mode=ok
case "$0" in
  *-invalid) mode=invalid ;;
  *-recall-timeout) mode=recall-timeout ;;
  *-timeout) mode=timeout ;;
esac
if [ "$mode" = timeout ] || { [ "$mode" = recall-timeout ] && [ "$1" = recall ]; }; then
  sleep 3
  exit 0
fi
case "$1" in
  store)
    if [ "$mode" = invalid ]; then
      printf '%s\n' 'not-json-or-store'
      exit 0
    fi
    printf 'scope=%s content=%s source=%s\n' "$MNEMOSYNE_DEFAULT_SCOPE" "$2" "$3" >>"$0.log"
    printf '%s\n' 'Stored: fixture-memory'
    ;;
  recall)
    printf '%s\n' '{"query":"fixture","top_k":2,"results":[{"content":"Mnemosyne private continuity hint","score":0.9}]}'
    ;;
  *)
    exit 1
    ;;
esac
EOF
  chmod 700 "$fake"
  cp "$fake" "$test_root/fake-mnemosyne-invalid"
  cp "$fake" "$test_root/fake-mnemosyne-timeout"
  cp "$fake" "$test_root/fake-mnemosyne-recall-timeout"
  chmod 700 "$test_root"/fake-mnemosyne-*

  personal_result=$(MNEMOSYNE_CLI="$fake" CODEX_SESSION_ID=personal \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" capture --cwd "$workspace" --scope personal \
      --outcome 'Keep response concise' --decision 'Prefer concise responses' \
      --artifact team-secret.md --source 'api_key=hidden')
  printf '%s' "$personal_result" | grep -q '"status": "handoff-required"'
  printf '%s' "$personal_result" | grep -q '"status": "stored"'
  grep -q 'scope=session content=Prefer concise responses' "$log"
  ! grep -q 'team-secret\|hidden' "$log"
  test "$(find "$workspace/.wiki/inbox/autosave" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 0

  MNEMOSYNE_CLI="$fake" MNEMOSYNE_DEFAULT_SCOPE=global CODEX_SESSION_ID=global \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" capture --cwd "$workspace" --scope personal \
      --outcome 'Keep global preference' --decision 'Prefer global concise replies' >/dev/null
  grep -q 'scope=global content=Prefer global concise replies' "$log"

  : >"$log"
  transcript=$(printf 'User: tell me what we decided.\nAssistant: the full conversation follows.')
  transcript_result=$(MNEMOSYNE_CLI="$fake" CODEX_SESSION_ID=transcript \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" capture --cwd "$workspace" --scope personal \
      --outcome "$transcript")
  printf '%s' "$transcript_result" | python3 -c 'import json,sys; assert json.load(sys.stdin)["adapter"]["status"] == "abstained"'
  test ! -s "$log"

  invalid_result=$(MNEMOSYNE_CLI="$test_root/fake-mnemosyne-invalid" CODEX_SESSION_ID=invalid \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" capture --cwd "$workspace" --scope personal \
      --outcome 'Invalid adapter response' --decision 'Keep invalid adapter test concise')
  printf '%s' "$invalid_result" | grep -q '"status": "invalid"'
  timeout_result=$(MNEMOSYNE_CLI="$test_root/fake-mnemosyne-timeout" CODEX_SESSION_ID=timeout \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" capture --cwd "$workspace" --scope personal \
      --outcome 'Slow adapter response' --decision 'Keep timeout adapter test concise')
  printf '%s' "$timeout_result" | grep -q '"status": "timeout"'
  absent_result=$(MNEMOSYNE_CLI="$test_root/missing" CODEX_SESSION_ID=absent \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" capture --cwd "$workspace" --scope personal \
      --outcome 'Absent adapter' --decision 'Keep absent adapter test concise')
  printf '%s' "$absent_result" | grep -q '"status": "unavailable"'

  cat >"$workspace/.wiki/wiki/workspace.md" <<'EOF'
---
type: knowledge
status: canonical
canonical_uri: "wiki://workspace/capture/workspace/workspace-architecture"
---

# Workspace architecture

The bounded retrieval architecture decision keeps workspace evidence authoritative.
EOF
  cat >"$test_home/wiki/topics/shared/wiki/user.md" <<'EOF'
---
type: knowledge
status: canonical
canonical_uri: "wiki://user/capture/user/user-architecture"
---

# User architecture

The user architecture decision records a reusable bounded retrieval pattern.
EOF
  mkdir -p "$workspace/.wiki/output"
  cat >"$workspace/.wiki/output/retrieval-plan.md" <<'EOF'
# Bounded retrieval rollout plan

Task artifact for validating bounded retrieval in the workspace.
EOF
  mkdir -p "$workspace/.wiki/inbox/autosave"
  cat >"$workspace/.wiki/inbox/autosave/session-retrieval.md" <<'EOF'
---
status: pending-curation
---

## Outcome

Bounded retrieval decision from a previous task remains pending curation.
EOF
  cat >"$workspace/.wiki/inbox/autosave/session-unrelated.md" <<'EOF'
---
status: pending-curation
---

## Outcome

Unrelated horticulture discussion remains pending curation.
EOF

  simple=$(MNEMOSYNE_CLI="$fake" "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" retrieve \
    --cwd "$workspace" --prompt 'What is two plus two?')
  printf '%s' "$simple" | grep -q '"status": "checked-no-match"'
  ! printf '%s' "$simple" | grep -q 'Workspace architecture'

  # A topical prompt retrieves Wiki content without an intent signal.
  direct=$(MNEMOSYNE_CLI="$fake" "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" retrieve \
    --cwd "$workspace" --prompt 'bounded retrieval')
  printf '%s' "$direct" | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["status"] == "checked-with-results", data; assert data["intent"]["signals"] == [], data["intent"]; assert data["results"][0]["source"] == "workspace", data["results"]; assert data["task_artifacts"][0]["kind"] == "task-artifact", data["task_artifacts"]; assert data["task_artifacts"][0]["path"] == "output/retrieval-plan.md", data["task_artifacts"]; assert [item["path"] for item in data["pending_captures"]] == ["inbox/autosave/session-retrieval.md"], data["pending_captures"]' || exit 1

  historical=$(MNEMOSYNE_CLI="$fake" "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" retrieve \
    --cwd "$workspace" --prompt 'Continue the architecture decision for bounded retrieval' --limit 3 --max-bytes 3000)
  printf '%s' "$historical" | python3 -c 'import json,sys; data=json.load(sys.stdin); sources=[item["source"] for item in data["results"]]; assert sources[:3] == ["workspace", "user", "mnemosyne"], data["results"]; assert len(json.dumps(data["results"]).encode()) <= 3000, len(json.dumps(data["results"]).encode())' || exit 1

  cat >"$workspace/.wiki/raw/uncurated.md" <<'EOF'
---
type: raw-source
canonical_uri: "wiki://workspace/capture/uncurated/raw-architecture"
---

# Uncurated architecture

The uncurated architecture decision repeats bounded retrieval architecture terms and must never outrank canonical evidence.
EOF
  uncurated_check=$(MNEMOSYNE_CLI="$fake" "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" retrieve \
    --cwd "$workspace" --prompt 'Continue the architecture decision for bounded retrieval' --limit 5 --max-bytes 6000)
  printf '%s' "$uncurated_check" | python3 -c 'import json,sys; data=json.load(sys.stdin); assert all(item["title"] != "Uncurated architecture" for item in data["results"]); assert data["results"][0]["source"] == "workspace"' || exit 1

  simple_context=$(printf '%s' '{"cwd":"'"$workspace"'","hook_event_name":"UserPromptSubmit","prompt":"What is two plus two?"}' | \
    MNEMOSYNE_CLI="$fake" "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/hooks/preflight.py")
  printf '%s' "$simple_context" | grep -q 'Wiki check: checked-no-match'
  ! printf '%s' "$simple_context" | grep -q 'Workspace architecture'
  historical_context=$(printf '%s' '{"cwd":"'"$workspace"'","hook_event_name":"UserPromptSubmit","prompt":"Continue the architecture decision for bounded retrieval"}' | \
    MNEMOSYNE_CLI="$fake" "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/hooks/preflight.py")
  printf '%s' "$historical_context" | grep -q 'Workspace architecture'
  printf '%s' "$historical_context" | grep -q 'Private continuity hint'

  first_turn=$(printf '%s' '{"cwd":"'"$workspace"'","hook_event_name":"UserPromptSubmit","session_id":"continuity","turn_id":"one","prompt":"bounded retrieval"}' | \
    MNEMOSYNE_CLI="$fake" "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/hooks/preflight.py")
  printf '%s' "$first_turn" | grep -q 'Workspace architecture' || return 1
  printf '%s' "$first_turn" | grep -q 'task artifact; not canonical' || return 1
  printf '%s' "$first_turn" | grep -q 'pending capture; not canonical; scope=workspace' || return 1
  ! printf '%s' "$first_turn" | grep -q 'Unrelated horticulture' || return 1
  followup_turn=$(printf '%s' '{"cwd":"'"$workspace"'","hook_event_name":"UserPromptSubmit","session_id":"continuity","turn_id":"two","prompt":"Ya, setuju"}' | \
    MNEMOSYNE_CLI="$fake" "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/hooks/preflight.py")
  printf '%s' "$followup_turn" | grep -q 'Workspace architecture' || return 1
  printf '%s' "$followup_turn" | grep -q 'Wiki check: checked-with-results' || return 1
  deictic_topic=$(printf '%s' '{"cwd":"'"$workspace"'","hook_event_name":"UserPromptSubmit","session_id":"continuity","turn_id":"deictic","prompt":"Apa itu Kubernetes?"}' | \
    MNEMOSYNE_CLI="$fake" "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/hooks/preflight.py")
  ! printf '%s' "$deictic_topic" | grep -q 'Workspace architecture' || return 1
  pivot_turn=$(printf '%s' '{"cwd":"'"$workspace"'","hook_event_name":"UserPromptSubmit","session_id":"continuity","turn_id":"three","prompt":"quantum thermodynamics"}' | \
    MNEMOSYNE_CLI="$fake" "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/hooks/preflight.py")
  ! printf '%s' "$pivot_turn" | grep -q 'Workspace architecture' || return 1
  python3 - "$workspace/.wiki/.sessions/wiki-agent-system/retrieval" <<'PY' || return 1
import json
import sys
from pathlib import Path

records = list(Path(sys.argv[1]).glob("*.json"))
assert len(records) == 1
ledger = json.loads(records[0].read_text())
assert ledger["schema_version"] == 4
assert len(ledger["sessions"]) == 1
session = next(iter(ledger["sessions"].values()))
assert "query" not in session
assert session["refs"] == []
assert [event["status"] for event in session["events"]] == ["checked-with-results", "checked-with-results", "checked-no-match", "checked-no-match"]
assert all(len(event["refs"]) <= 5 and "latency_ms" in event for event in session["events"])
assert "Ya, setuju" not in records[0].read_text()
assert "quantum thermodynamics" not in records[0].read_text()
PY

  foreign="$test_root/foreign-wiki"
  mkdir -p "$foreign/.wiki"
  printf '%s\n' '# Owned by another tool' >"$foreign/.wiki/_index.md"
  foreign_context=$(printf '%s' '{"cwd":"'"$foreign"'","hook_event_name":"UserPromptSubmit","prompt":"bounded retrieval"}' | \
    MNEMOSYNE_CLI="$fake" "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/hooks/preflight.py")
  printf '%s' "$foreign_context" | grep -q 'Wiki check: partial' || return 1
  printf '%s' "$foreign_context" | grep -q 'User architecture' || return 1
  test ! -e "$foreign/.wiki/.sessions" || return 1
  symlink_workspace="$test_root/symlink-wiki"
  mkdir -p "$symlink_workspace" "$test_root/foreign-target/raw" "$test_root/foreign-target/wiki"
  printf '%s\n' '# Foreign target' >"$test_root/foreign-target/config.md"
  printf '%s\n' '# Foreign target' >"$test_root/foreign-target/_index.md"
  ln -s "$test_root/foreign-target" "$symlink_workspace/.wiki"
  symlink_context=$(printf '%s' '{"cwd":"'"$symlink_workspace"'","hook_event_name":"UserPromptSubmit","prompt":"bounded retrieval"}' | \
    MNEMOSYNE_CLI="$fake" "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/hooks/preflight.py")
  printf '%s' "$symlink_context" | grep -q 'Wiki check: partial' || return 1
  printf '%s' "$symlink_context" | grep -q 'User architecture' || return 1
  test ! -e "$test_root/foreign-target/.sessions" || return 1

  timed_out=$(MNEMOSYNE_CLI="$test_root/fake-mnemosyne-recall-timeout" \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" retrieve --cwd "$workspace" \
      --prompt 'Investigate historical banana topic' --timeout 0.1)
  printf '%s' "$timed_out" | grep -q '"status": "timeout"'
  printf '%s' "$timed_out" | grep -q '"status": "checked-no-match"'
)

capture_contract() (
  test_root=$(mktemp -d)
  trap 'rm -rf "$test_root"' EXIT
  cp -R "$root/plugins/wiki-preflight" "$test_root/plugin"
  workspace="$test_root/workspace"
  config_home="$test_root/config"
  mkdir -p "$workspace/.wiki/raw" "$workspace/.wiki/wiki" "$workspace/.wiki/inbox/autosave" "$config_home"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/config.md"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/_index.md"

  legacy_key=$(python3 -c 'import hashlib; print(hashlib.sha256(b"legacy").hexdigest()[:16])')
  legacy="$workspace/.wiki/inbox/autosave/session-$legacy_key.md"
  cat >"$legacy" <<EOF
---
type: autosave-capture
schema: 1
status: pending-curation
workspace: $workspace
---

# Auto-saved work capture

## Outcome

Legacy result

## Decisions

- Preserve old decisions
EOF

  XDG_CONFIG_HOME="$config_home" CODEX_SESSION_ID=legacy \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" capture \
      --cwd "$workspace" --outcome 'Migrated result' --kind result >/dev/null
  grep -q '^schema: 2$' "$legacy"
  grep -q '^scope: workspace$' "$legacy"
  grep -q '^canonical_uri: "wiki://workspace/' "$legacy"
  grep -q '^origin_workspace: "' "$legacy"
  grep -q '^status: pending-curation$' "$legacy"
  grep -q '^supersedes: null$' "$legacy"
  grep -q '^valid_from: ' "$legacy"
  grep -q '^valid_until: null$' "$legacy"
  grep -q 'Preserve old decisions' "$legacy"

  future_key=$(python3 -c 'import hashlib; print(hashlib.sha256(b"future").hexdigest()[:16])')
  future="$workspace/.wiki/inbox/autosave/session-$future_key.md"
  printf '%s\n' '---' 'schema: 99' 'status: pending-curation' '---' 'future record' >"$future"
  cp "$future" "$future.before"
  ! XDG_CONFIG_HOME="$config_home" CODEX_SESSION_ID=future \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" capture \
      --cwd "$workspace" --outcome 'Must not overwrite' >/dev/null 2>&1
  cmp "$future.before" "$future"
)

scope_routing() (
  test_root=$(mktemp -d)
  trap 'rm -rf "$test_root"' EXIT
  cp -R "$root/plugins/wiki-preflight" "$test_root/plugin"
  test_home="$test_root/home"
  config_home="$test_root/config"
  workspace="$test_root/workspace"
  mkdir -p "$workspace/.wiki/raw" "$workspace/.wiki/wiki" "$workspace/.wiki/inbox" "$test_home" "$config_home"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/config.md"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/_index.md"

  run_capture() {
    capture_scope=$1
    session=$2
    capture_cwd=$3
    HOME="$test_home" XDG_CONFIG_HOME="$config_home" CODEX_SESSION_ID="$session" \
      "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" capture \
        --cwd "$capture_cwd" --scope "$capture_scope" --outcome "$session result"
  }

  auto_result=$(run_capture auto auto "$workspace")
  printf '%s' "$auto_result" | grep -q '"scope": "workspace"'
  workspace_capture=$(find "$workspace/.wiki/inbox/autosave" -type f -name 'session-*.md')
  test -n "$workspace_capture"
  test ! -e "$test_home/wiki"
  workspace_uri=$(sed -n 's/^canonical_uri: "\(.*\)"$/\1/p' "$workspace_capture")

  subdirectory="$workspace/subdirectory"
  mkdir "$subdirectory"
  stable_key=$(python3 -c 'import hashlib; print(hashlib.sha256(b"stable").hexdigest()[:16])')
  run_capture workspace stable "$workspace" >/dev/null
  stable_capture="$workspace/.wiki/inbox/autosave/session-$stable_key.md"
  root_uri=$(sed -n 's/^canonical_uri: "\(.*\)"$/\1/p' "$stable_capture")
  run_capture workspace stable "$subdirectory" >/dev/null
  subdirectory_uri=$(sed -n 's/^canonical_uri: "\(.*\)"$/\1/p' "$stable_capture")
  test "$root_uri" = "$subdirectory_uri"
  grep -q "^origin_workspace: \"$workspace\"$" "$stable_capture"

  run_capture user user "$subdirectory" >/dev/null
  test "$(find "$workspace/.wiki/inbox/autosave" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 2
  user_capture=$(find "$test_home/wiki" -type f -path '*/inbox/autosave/session-*.md')
  test -n "$user_capture"
  grep -q '^scope: user$' "$user_capture"
  grep -q "^origin_workspace: \"$workspace\"$" "$user_capture"
  user_uri=$(sed -n 's/^canonical_uri: "\(.*\)"$/\1/p' "$user_capture")
  test -n "$workspace_uri"
  test -n "$user_uri"
  test "$workspace_uri" != "$user_uri"

  run_capture uncertain uncertain "$workspace" >/dev/null
  uncertain_capture=$(find "$test_home/wiki/inbox/pending-scope" -type f -name 'session-*.md')
  test -n "$uncertain_capture"
  grep -q '^scope: uncertain$' "$uncertain_capture"

  wiki_files_before=$(find "$test_home/wiki" -type f | wc -l | tr -d ' ')
  personal_result=$(run_capture personal personal "$workspace")
  printf '%s' "$personal_result" | grep -q '"status": "handoff-required"'
  printf '%s' "$personal_result" | grep -q '"provider": "mnemosyne"'
  printf '%s' "$personal_result" | grep -q '"scope": "personal"'
  wiki_files_after=$(find "$test_home/wiki" -type f | wc -l | tr -d ' ')
  test "$wiki_files_before" -eq "$wiki_files_after"

  fallback_workspace="$test_root/fallback"
  mkdir "$fallback_workspace"
  printf '%s' "{\"cwd\":\"$fallback_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"fallback\",\"turn_id\":\"one\"}" | \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/hooks/preflight.py" >/dev/null
  printf '%s\n' changed >"$fallback_workspace/changed.txt"
  stop_result=$(printf '%s' "{\"cwd\":\"$fallback_workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"fallback\",\"turn_id\":\"one\",\"last_assistant_message\":\"Completed fallback\"}" | \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/hooks/preflight.py")
  printf '%s' "$stop_result" | grep -q '"hookEventName": "Stop"'
  fallback_capture=$(find "$fallback_workspace/.wiki/inbox/autosave" -name 'session-*.md')
  test -n "$fallback_capture"
  grep -q '^scope: workspace$' "$fallback_capture"

  foreign="$test_root/foreign"
  mkdir -p "$foreign/.wiki"
  printf '%s\n' foreign >"$foreign/.wiki/marker"
  ! HOME="$test_home" XDG_CONFIG_HOME="$config_home" CODEX_SESSION_ID=foreign \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" capture \
      --cwd "$foreign" --scope workspace --outcome 'Must not bypass foreign Wiki' >/dev/null 2>&1
  test -f "$foreign/.wiki/marker"
  test ! -e "$foreign/.wiki/inbox"
)

privacy_and_boundaries() (
  set -e
  test_root=$(mktemp -d)
  trap 'rm -rf "$test_root"' EXIT
  cp -R "$root/plugins/wiki-preflight" "$test_root/plugin"
  repo="$test_root/repo"
  nongit="$test_root/nongit"
  private_home="$test_root/private-home"
  config_home="$test_root/config"
  mkdir -p "$repo" "$nongit" "$private_home" "$config_home"
  git init -q "$repo"
  repo_plugin="$repo/plugin"
  cp -R "$root/plugins/wiki-preflight" "$repo_plugin"

  printf '%s' "{\"cwd\":\"$repo\",\"hook_event_name\":\"SessionStart\"}" | \
    HOME="$repo" XDG_CONFIG_HOME="$repo/.config" "$repo_plugin/hooks/launcher.sh" "$repo_plugin/hooks/preflight.py" >/dev/null
  test ! -e "$repo/.config"
  test ! -e "$repo/Library"
  test ! -e "$repo_plugin/scripts/__pycache__"
  if HOME="$repo" XDG_CONFIG_HOME="$repo/.config" CODEX_SESSION_ID=private \
    "$repo_plugin/hooks/launcher.sh" "$repo_plugin/scripts/wiki_ambient.py" capture --cwd "$repo" \
      --scope user --outcome 'Must stay private' >/dev/null 2>&1; then
    exit 1
  fi
  test ! -e "$repo/wiki"
  test ! -e "$repo/.config"
  test ! -e "$repo/Library"
  test ! -e "$repo_plugin/scripts/__pycache__"

  hook_input() {
    event=$1
    printf '%s' "{\"cwd\":\"$repo\",\"hook_event_name\":\"$event\"}" | \
      "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/hooks/preflight.py" >/dev/null
  }
  hook_input SessionStart
  test -f "$repo/.wiki/_index.md"
  test -f "$repo/.gitignore"
  git -C "$repo" check-ignore -q .wiki/.sessions/runtime.json
  test "$(git -C "$repo" diff --cached --name-only)" = ""
  if git -C "$repo" rev-parse --verify HEAD >/dev/null 2>&1; then
    exit 1
  fi

  HOME="$private_home" XDG_CONFIG_HOME="$config_home" CODEX_SESSION_ID=workspace \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" capture --cwd "$repo" \
      --scope workspace --outcome 'Shared repository result' >/dev/null
  HOME="$private_home" XDG_CONFIG_HOME="$config_home" CODEX_SESSION_ID=user \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" capture --cwd "$repo" \
      --scope user --outcome 'Private reusable result' >/dev/null
  personal_before=$(find "$private_home/wiki" -type f 2>/dev/null | wc -l | tr -d ' ')
  HOME="$private_home" XDG_CONFIG_HOME="$config_home" CODEX_SESSION_ID=personal \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" capture --cwd "$repo" \
      --scope personal --outcome 'Private continuity result' >/dev/null
  personal_after=$(find "$private_home/wiki" -type f 2>/dev/null | wc -l | tr -d ' ')
  test "$personal_before" -eq "$personal_after"
  test -f "$private_home/wiki/inbox/autosave/session-$(python3 -c 'import hashlib; print(hashlib.sha256(b"user").hexdigest()[:16])').md"
  test -f "$config_home/llm-wiki/wiki-agent-system.json"
  test ! -e "$repo/.config"
  test ! -e "$repo/wiki"
  test "$(git -C "$repo" diff --cached --name-only)" = ""
  if git -C "$repo" rev-parse --verify HEAD >/dev/null 2>&1; then
    exit 1
  fi

  printf '%s' "{\"cwd\":\"$nongit\",\"hook_event_name\":\"SessionStart\"}" | \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/hooks/preflight.py" >/dev/null
  test -f "$nongit/.wiki/_index.md"
  test ! -e "$nongit/.gitignore"
  HOME="$private_home" XDG_CONFIG_HOME="$config_home" CODEX_SESSION_ID=nongit \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/scripts/wiki_ambient.py" capture --cwd "$nongit" \
      --scope workspace --outcome 'Non-Git result' >/dev/null
  test -n "$(find "$nongit/.wiki/inbox/autosave" -type f -name 'session-*.md')"

  foreign="$test_root/foreign"
  mkdir -p "$foreign/.wiki"
  printf '%s\n' foreign >"$foreign/.wiki/marker"
  printf '%s' "{\"cwd\":\"$foreign\",\"hook_event_name\":\"SessionStart\"}" | \
    "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/hooks/preflight.py" >/dev/null
  test -f "$foreign/.wiki/marker"
  test ! -e "$foreign/.wiki/_index.md"
  test ! -e "$foreign/.gitignore"
)

migration_marker() (
  test_root=$(mktemp -d)
  trap 'rm -rf "$test_root"' EXIT
  workspace="$test_root/workspace"
  mkdir "$workspace"
  run_hook() {
    event=$1
    printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"$event\"}" | \
      "$root/plugins/wiki-preflight/hooks/launcher.sh" "$root/plugins/wiki-preflight/hooks/preflight.py"
  }
  printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"SessionStart\"}" | \
    "$root/plugins/wiki-preflight/hooks/launcher.sh" "$root/plugins/wiki-preflight/hooks/preflight.py" >/dev/null
  marker="$workspace/.wiki/.sessions/wiki-agent-system/marker.json"
  legacy="$workspace/.wiki/.wiki-agent-system.json"
  python3 -c 'import json,sys; assert json.load(open(sys.argv[1])) == {"schema_version": 2}' "$marker"
  test ! -e "$legacy"
  printf '%s\n' '{"schema_version":2,"preserved":"legacy"}' >"$legacy"
  rm "$marker"
  run_hook SessionStart >/dev/null
  python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); assert data["schema_version"] == 2 and data["preserved"] == "legacy"' "$marker"
  test ! -e "$legacy"
  printf '%s\n' '{"schema_version":1,"preserved":"yes"}' >"$marker"
  run_hook SessionStart >/dev/null
  python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); assert data["schema_version"] == 2 and data["preserved"] == "yes"' \
    "$marker"
  printf '%s\n' '{"schema_version": 99}' >"$marker"
  output=$(printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"SessionStart\"}" | "$root/plugins/wiki-preflight/hooks/launcher.sh" "$root/plugins/wiki-preflight/hooks/preflight.py")
  printf '%s' "$output" | grep -q 'schema is newer'
  python3 -c 'import json,sys; assert json.load(open(sys.argv[1])) == {"schema_version": 99}' \
    "$marker"
)

config_forward_migration() (
  test_root=$(mktemp -d)
  trap 'rm -rf "$test_root"' EXIT
  config_home="$test_root/config"
  config="$config_home/llm-wiki/wiki-agent-system.json"
  mkdir -p "$(dirname "$config")"
  cp "$root/plugins/wiki-preflight/defaults/ambient.json" "$config"
  python3 - "$config" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path))
data["schema_version"] = 99
json.dump(data, open(path, "w"))
PY
  cp "$config" "$config.before"
  ! XDG_CONFIG_HOME="$config_home" "$root/plugins/wiki-preflight/hooks/launcher.sh" "$root/plugins/wiki-preflight/scripts/wiki_ambient.py" validate >/dev/null 2>&1
  cmp "$config.before" "$config"
)

from_root() (
  cd "$root" || exit 1
  if [ "${WIKI_P1_TRACE:-0}" = 1 ] && [ "${1:-}" = sh ]; then
    shift
    sh -x "$@"
    exit $?
  fi
  "$@"
)

launcher_only_runtime() {
  pattern='python3.*'
  pattern="$pattern(/hooks/|/scripts/).*"
  pattern="$pattern\\.py"
  ! rg -n --hidden --glob '!**/.git/**' "$pattern" \
    "$root/README.md" "$root/plugins"
}

release_integrity() (
  cd "$root" || exit 1
  pycache=$(mktemp -d)
  PYTHONPYCACHEPREFIX="$pycache" python3 -m py_compile $(find plugins/wiki-preflight -type f -name '*.py' -print)
  find . -type f -name '*.sh' -exec sh -n {} +
  python3 - "$root" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
for path in root.rglob("*.json"):
    if ".git" in path.parts:
        continue
    json.loads(path.read_text(encoding="utf-8"))
PY
  git diff --check
  ! rg -n --hidden --glob '!**/.git/**' '(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|https?://[^[:space:]]+:[^[:space:]@]+@)' plugins tests
)

check 'plugin manifest' test -f "$root/plugins/wiki-preflight/.codex-plugin/plugin.json"
check 'ambient configuration schema' "$root/plugins/wiki-preflight/hooks/launcher.sh" "$root/plugins/wiki-preflight/scripts/wiki_ambient.py" validate
check 'user-scope configuration survives plugin replacement' sh "$root/tests/config-persistence.sh" "$root/plugins/wiki-preflight"
check 'user-scope configuration migration' sh "$root/tests/config-migration.sh" "$root/plugins/wiki-preflight"
check 'agent-side semantic finalizer contract' sh "$root/tests/semantic-finalizer.sh" "$root/plugins/wiki-preflight"
check 'Codex real UserPromptSubmit retrieval and Stop continuation' sh "$root/tests/codex-stop-continuation.sh"
check 'per-task semantic capture deduplication' sh "$root/tests/capture-dedup.sh" "$root/plugins/wiki-preflight"
check 'quota report and operational-data quarantine' sh "$root/tests/quota-retention.sh" "$root/plugins/wiki-preflight"
check 'scheduled, locked, fail-open retention' scheduled_retention
check 'truthful storage accounting and diagnostics' storage_accounting
check 'authorized quarantine purge and restore boundaries' purge_policy
check 'stable hook runtime survives removed plugin cache' sh "$root/tests/stable-hook-runtime.sh" "$root/plugins/wiki-preflight"
check 'semantic capture and evidence gate' semantic_and_evidence
check 'evidence lifecycle and canonical retention' lifecycle_and_retention
check 'default autosave retention is 10 days' retention_default_days
check 'optional Mnemosyne adapter and bounded retrieval' adapter_and_retrieval
check 'capture contract and forward compatibility' capture_contract
check 'deterministic scope routing' scope_routing
check 'privacy, collaboration, and Git boundaries' privacy_and_boundaries
check 'migration/version marker' migration_marker
check 'user configuration future-schema protection' config_forward_migration
check 'behavior matrix' sh "$root/tests/behavior-matrix.sh" "$root/plugins/wiki-preflight"
check 'universal retrieval, scope, continuity, privacy, context, and bilingual golden set' sh "$root/tests/universal-preflight.sh" "$root/plugins/wiki-preflight"
check 'autonomous evidence verification' from_root sh tests/evidence-verification.sh
check 'canonical evidence compatibility and migration' from_root sh tests/canonical-evidence.sh
check 'source integrity and release gate' release_integrity
check 'source clean-device smoke test' from_root sh tests/clean-device.sh
check 'vendored runtime integrity tamper regression' from_root sh tests/vendor-runtime-integrity.sh
check 'installed-package smoke test' from_root sh tests/installed-package.sh
check 'YouTube caption fallback' from_root sh tests/youtube-fallback.sh
check 'YouTube transcript API adapter' from_root sh tests/youtube-transcript-api.sh
check 'live coexistence test' env WIKI_MARKETPLACE_SOURCE="$root" WIKI_MARKETPLACE_REF= sh "$root/tests/live-coexistence.sh"
check 'Git marketplace clean-device test' env WIKI_MARKETPLACE_SOURCE="$root" WIKI_MARKETPLACE_REF= sh "$root/tests/git-marketplace-clean-device.sh"
check 'launcher-only plugin runtime' launcher_only_runtime

if [ "$failed" -eq 0 ]; then
  echo 'P1 ACCEPTANCE: PASS'
  exit 0
fi
echo 'P1 ACCEPTANCE: FAIL' >&2
exit 1
