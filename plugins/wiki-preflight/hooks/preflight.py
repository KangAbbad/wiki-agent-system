#!/usr/bin/env python3
import hashlib, json, sys, os, re, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path

SENSITIVE = re.compile(r"((?:api[_ -]?key|password|secret|token|private[_ -]?key|authorization)\s*[:=])[^\r\n]*", re.I)

payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
cwd = Path(payload.get("cwd") or payload.get("workspace_root") or ".").resolve()
existing = next((p / ".wiki" for p in (cwd, *cwd.parents) if (p / ".wiki").is_dir()), None)
root = existing
if root and not all((root / item).exists() for item in ("config.md", "_index.md", "raw", "wiki")):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "SessionStart"), "additionalContext": f"Foreign/incomplete wiki at {root}; do not modify it."}}))
    raise SystemExit(0)
if root is None and cwd.is_dir():
    root = cwd / ".wiki"
    for path in (root / "raw", root / "wiki", root / "output", root / "inbox"):
        path.mkdir(parents=True, exist_ok=True)
    (root / "config.md").write_text("# Workspace Wiki\n")
    (root / "_index.md").write_text("# Workspace Wiki\n\n## Knowledge\n\n- [Raw](raw/)\n- [Articles](wiki/)\n- [Output](output/)\n")
if root:
    marker = root / ".wiki-agent-system.json"
    if not marker.exists():
        marker.write_text('{"schema_version":1}\n')
    event = payload.get("hook_event_name", "SessionStart")
    if event == "Stop":
        captures = root / "inbox" / "autosave"
        captures.mkdir(parents=True, exist_ok=True)
        session_id = str(payload.get("session_id") or "unknown")
        turn_id = str(payload.get("turn_id") or "unknown")
        key = hashlib.sha256(f"{session_id}:{turn_id}".encode()).hexdigest()[:16]
        state = root / ".sessions" / "wiki-agent-system" / "finalizers" / f"{key}.md"
        message = payload.get("last_assistant_message")
        message = SENSITIVE.sub(lambda match: f"{match.group(1)} [REDACTED]", message or "").strip()
        if message and not payload.get("stop_hook_active"):
            state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=state.parent, prefix=".finalizer-", text=True)
            with os.fdopen(fd, "w") as file:
                file.write(message)
            os.chmod(temporary, 0o600)
            os.replace(temporary, state)
            capture = Path(__file__).resolve().parents[1] / "scripts" / "wiki_ambient.py"
            print(json.dumps({"decision": "block", "reason": f"Before completion, run `python3 {capture} capture --cwd {cwd} --outcome \"...\"` now. Summarize the completed outcome and add every applicable decision, artifact, verification, source, confidence, and open question. Do not ask the user to save it."}))
            raise SystemExit(0)
        previous = state.read_text() if state.exists() else message
        semantic = [path for path in captures.glob("*.md") if path.stat().st_mtime >= state.stat().st_mtime] if state.exists() else []
        if not semantic:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            output = captures / f"{stamp}-{uuid.uuid4().hex[:8]}-semantic-fallback.md"
            fd, temporary = tempfile.mkstemp(dir=captures, prefix=".capture-", text=True)
            with os.fdopen(fd, "w") as file:
                file.write(f"---\ntype: semantic-stop-capture\nstatus: pending-curation\nworkspace: {cwd}\nsession_id: {session_id}\nturn_id: {turn_id}\nfallback: true\n---\n\n# Auto-saved final result\n\n## Outcome\n\n{previous or 'Session completed; final response unavailable.'}\n\n## Decisions\n\n- Not extracted; review outcome during curation.\n\n## Artifacts\n\n- Not extracted; inspect workspace changes during curation.\n\n## Verification\n\n- Not extracted; review outcome during curation.\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, output)
        state.unlink(missing_ok=True)
    policy = (Path(__file__).resolve().parents[1] / "defaults" / "policy.md").read_text().strip()
    index = (root / "_index.md").read_text()[:4000]
    captures = sorted((root / "inbox" / "autosave").glob("*.md"))[-3:]
    recent = "\n\n".join(path.read_text()[:2000] for path in captures)
    text = f"{policy}\nWorkspace knowledge index:\n{index}\nRecent captures:\n{recent}"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "SessionStart"), "additionalContext": text}}))
