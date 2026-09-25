#!/bin/sh
set -eu

plugin_root=${1:-plugins/wiki-preflight}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
export HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" CODEX_HOME="$test_root/codex"
export MNEMOSYNE_CLI="$test_root/mnemosyne-not-installed" TMPDIR="$test_root/tmp"
mkdir -p "$HOME" "$TMPDIR"
workspace="$test_root/workspace"
mkdir "$workspace"
printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"SessionStart\"}" | "$plugin_root/hooks/launcher.sh" "$plugin_root/hooks/preflight.py" >/dev/null

CODEX_SESSION_ID=one "$plugin_root/hooks/launcher.sh" "$plugin_root/scripts/wiki_ambient.py" capture \
  --cwd "$workspace" --kind research --outcome 'Initial research' \
  --decision 'Keep provenance' --artifact notes/research.md --confidence medium >/dev/null
CODEX_SESSION_ID=one "$plugin_root/hooks/launcher.sh" "$plugin_root/scripts/wiki_ambient.py" capture \
  --cwd "$workspace" --kind result --outcome 'Final result' \
  --verification 'lint passed' --artifact docs/result.md \
  --source 'https://example.test/source' --confidence high >/dev/null

captures="$workspace/.wiki/inbox/autosave"
test "$(find "$captures" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 1
capture=$(find "$captures" -type f -name 'session-*.md')
grep -Fq 'Final result' "$capture"
grep -Fq 'Keep provenance' "$capture"
grep -Fq '`notes/research.md`' "$capture"
grep -Fq '`docs/result.md`' "$capture"
grep -Fq 'lint passed' "$capture"
grep -Fq 'https://example.test/source' "$capture"
grep -Fq 'confidence: high' "$capture"

CODEX_SESSION_ID=two "$plugin_root/hooks/launcher.sh" "$plugin_root/scripts/wiki_ambient.py" capture \
  --cwd "$workspace" --outcome 'Separate task' --kind result >/dev/null
test "$(find "$captures" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 2

printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"hook-merge\",\"turn_id\":\"one\",\"prompt\":\"Implement capture merging\"}" | "$plugin_root/hooks/launcher.sh" "$plugin_root/hooks/preflight.py" >/dev/null
CODEX_SESSION_ID=hook-merge "$plugin_root/hooks/launcher.sh" "$plugin_root/scripts/wiki_ambient.py" capture \
  --cwd "$workspace" --outcome 'Structured hook result' \
  --decision 'Keep one record' --artifact src/hook.py --verification 'merge passed' >/dev/null
printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"hook-merge\",\"turn_id\":\"one\",\"last_assistant_message\":\"Final hook result\"}" | "$plugin_root/hooks/launcher.sh" "$plugin_root/hooks/preflight.py" >/dev/null
test "$(find "$captures" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 3
hook_key=$(python3 -c 'import hashlib; print(hashlib.sha256(b"hook-merge").hexdigest()[:16])')
hook_capture="$captures/session-$hook_key.md"
grep -Fq 'Final hook result' "$hook_capture"
grep -Fq 'Keep one record' "$hook_capture"
grep -Fq '`src/hook.py`' "$hook_capture"
grep -Fq 'merge passed' "$hook_capture"

parallel="$test_root/parallel"
mkdir "$parallel"
printf '%s' "{\"cwd\":\"$parallel\",\"hook_event_name\":\"SessionStart\"}" | "$plugin_root/hooks/launcher.sh" "$plugin_root/hooks/preflight.py" >/dev/null
python3 - "$plugin_root/scripts" "$parallel" <<'PY'
import multiprocessing
import sys
from threading import BrokenBarrierError


def capture_worker(scripts, workspace, index, barrier, errors):
    sys.path.insert(0, scripts)
    import wiki_ambient

    original = wiki_ambient.atomic_write

    def synchronized_write(path, content):
        try:
            barrier.wait(timeout=0.4)
        except (BrokenBarrierError, TimeoutError):
            pass
        original(path, content)

    wiki_ambient.atomic_write = synchronized_write
    try:
        wiki_ambient.capture(
            workspace,
            f"Parallel outcome {index}",
            "result",
            [f"src/parallel-{index}.py"],
            [f"Decision {index}"],
            [f"Verification {index}"],
            [],
            "medium",
            [],
            "workspace",
            task_key="b" * 16,
        )
    except BaseException as error:
        errors.put(repr(error))


if __name__ == "__main__":
    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(2)
    errors = ctx.Queue()
    processes = [ctx.Process(target=capture_worker, args=(sys.argv[1], sys.argv[2], index, barrier, errors)) for index in (1, 2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(5)
        assert process.exitcode == 0, process.exitcode
    assert errors.empty(), errors.get() if not errors.empty() else "capture failed"
    capture = open(f"{sys.argv[2]}/.wiki/inbox/autosave/session-{'b' * 16}.md").read()
    assert "Parallel outcome 1" in capture or "Parallel outcome 2" in capture
    for value in ("Decision 1", "Decision 2", "Verification 1", "Verification 2", "`src/parallel-1.py`", "`src/parallel-2.py`"):
        assert value in capture, value
PY

printf '%s\n' 'capture dedup passed'
