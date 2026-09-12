#!/usr/bin/env python3
import hashlib, json, sys, os, re, tempfile, time, uuid
from datetime import datetime, timezone
from pathlib import Path

SENSITIVE = re.compile(r"((?:api[_ -]?key|password|secret|token|private[_ -]?key|authorization)\s*[:=])[^\r\n]*", re.I)
IGNORED_WORKSPACE_DIRS = {".git", ".wiki", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", ".next", ".cache"}
WORKSPACE_SCHEMA_VERSION = 2

def atomic_json_write(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".schema-", text=True)
    with os.fdopen(fd, "w") as file:
        json.dump(data, file, sort_keys=True)
        file.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)

def migrate_marker(marker):
    if not marker.exists():
        atomic_json_write(marker, {"schema_version": WORKSPACE_SCHEMA_VERSION})
        return "created"
    try:
        data = json.loads(marker.read_text())
        version = data.get("schema_version")
    except (OSError, json.JSONDecodeError, AttributeError):
        return "invalid"
    if version == WORKSPACE_SCHEMA_VERSION:
        return "current"
    if version == 1:
        data["schema_version"] = WORKSPACE_SCHEMA_VERSION
        atomic_json_write(marker, data)
        return "migrated"
    return "future" if isinstance(version, int) and version > WORKSPACE_SCHEMA_VERSION else "invalid"

def finalizer_state(root, payload):
    session_id = str(payload.get("session_id") or "unknown")
    turn_id = str(payload.get("turn_id") or "unknown")
    key = hashlib.sha256(f"{session_id}:{turn_id}".encode()).hexdigest()[:16]
    return root / ".sessions" / "wiki-agent-system" / "finalizers" / f"{key}.json"

def workspace_changed_since(cwd, started_at):
    """Bounded mtime scan; semantic capture remains the primary signal."""
    deadline = time.monotonic() + 1.5
    for base, directories, files in os.walk(cwd):
        directories[:] = [name for name in directories if name not in IGNORED_WORKSPACE_DIRS]
        for name in files:
            try:
                if (Path(base) / name).stat().st_mtime >= started_at:
                    return True
            except OSError:
                continue
            if time.monotonic() >= deadline:
                return False
    return False

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
    event = payload.get("hook_event_name", "SessionStart")
    marker_state = migrate_marker(marker)
    if marker_state in {"future", "invalid"}:
        detail = "newer" if marker_state == "future" else "invalid"
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": f"Workspace Wiki Agent System schema is {detail}; do not modify it until a compatible plugin is installed."}}))
        raise SystemExit(0)
    if event == "UserPromptSubmit":
        state = finalizer_state(root, payload)
        state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=state.parent, prefix=".turn-", text=True)
        with os.fdopen(fd, "w") as file:
            json.dump({"started_at": time.time()}, file)
        os.chmod(temporary, 0o600)
        os.replace(temporary, state)
    if event == "Stop":
        captures = root / "inbox" / "autosave"
        captures.mkdir(parents=True, exist_ok=True)
        session_id = str(payload.get("session_id") or "unknown")
        turn_id = str(payload.get("turn_id") or "unknown")
        state = finalizer_state(root, payload)
        message = payload.get("last_assistant_message")
        message = SENSITIVE.sub(lambda match: f"{match.group(1)} [REDACTED]", message or "").strip()
        try:
            started_at = json.loads(state.read_text()).get("started_at") if state.exists() else None
        except (OSError, json.JSONDecodeError):
            started_at = None
        semantic = [path for path in captures.glob("*.md") if started_at and path.stat().st_mtime >= started_at]
        if message and started_at and not semantic and workspace_changed_since(cwd, started_at):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            output = captures / f"{stamp}-{uuid.uuid4().hex[:8]}-semantic-fallback.md"
            fd, temporary = tempfile.mkstemp(dir=captures, prefix=".capture-", text=True)
            with os.fdopen(fd, "w") as file:
                file.write(f"---\ntype: semantic-stop-capture\nstatus: pending-curation\nworkspace: {cwd}\nsession_id: {session_id}\nturn_id: {turn_id}\nfallback: true\n---\n\n# Auto-saved final result\n\n## Outcome\n\n{message}\n\n## Decisions\n\n- Not extracted; review outcome during curation.\n\n## Artifacts\n\n- Not extracted; inspect workspace changes during curation.\n\n## Verification\n\n- Not extracted; review outcome during curation.\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, output)
        state.unlink(missing_ok=True)
    policy = (Path(__file__).resolve().parents[1] / "defaults" / "policy.md").read_text().strip()
    index = (root / "_index.md").read_text()[:4000]
    captures = sorted((root / "inbox" / "autosave").glob("*.md"))[-3:]
    recent = "\n\n".join(path.read_text()[:2000] for path in captures)
    text = f"{policy}\nWorkspace knowledge index:\n{index}\nRecent captures:\n{recent}"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "SessionStart"), "additionalContext": text}}))
