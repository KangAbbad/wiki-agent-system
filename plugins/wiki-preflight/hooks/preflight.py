#!/usr/bin/env python3
import hashlib, json, sys, os, re, subprocess, tempfile, time, uuid
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from wiki_ambient import CAPTURE_SCHEMA_VERSION, canonical_capture_uri, retrieve_memory

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

def ensure_sessions_ignored(root, cwd):
    """Keep plugin runtime state out of Git without touching non-Git workspaces."""
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if result.returncode:
        return
    repo = Path(result.stdout.strip())
    try:
        pattern = (root.relative_to(repo) / ".sessions").as_posix() + "/"
    except ValueError:
        return
    gitignore = repo / ".gitignore"
    try:
        current = gitignore.read_text() if gitignore.exists() else ""
    except OSError:
        return
    if any(line.strip().lstrip("/").rstrip("/") == pattern.rstrip("/") for line in current.splitlines()):
        return
    suffix = "" if not current or current.endswith("\n") else "\n"
    fd, temporary = tempfile.mkstemp(dir=repo, prefix=".gitignore.", text=True)
    try:
        with os.fdopen(fd, "w") as file:
            file.write(f"{current}{suffix}# Wiki Agent System runtime state\n{pattern}\n")
        if gitignore.exists():
            os.chmod(temporary, gitignore.stat().st_mode & 0o777)
        os.replace(temporary, gitignore)
    except OSError:
        Path(temporary).unlink(missing_ok=True)


def run_scheduled_retention(root):
    """Best-effort maintenance; retention must never block a hook event."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "retention.py"
    try:
        subprocess.run(
            [sys.executable, str(script), str(root.parent), "--apply", "--scheduled"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1.5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


def prompt_text(payload):
    for key in ("prompt", "user_prompt", "user_message", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value[:4000]
    return ""


def safe_retrieve(cwd, prompt):
    try:
        return retrieve_memory(str(cwd), prompt)
    except (OSError, ValueError, SystemExit) as error:
        return {
            "status": "unavailable",
            "intent": {"matched": False, "signals": [], "query": ""},
            "results": [],
            "diagnostics": [{"source": "retrieval", "status": "error", "reason": str(error)[:160]}],
        }


def retrieval_context(result):
    if result is None:
        return ""
    lines = ["Bounded memory context: current instruction wins; Workspace Wiki > User Wiki > Mnemosyne hints."]
    intent = result.get("intent", {})
    if not intent.get("matched"):
        lines.append("Memory retrieval: abstained; no continuation, prior-decision, research, architecture, or repeated-investigation signal.")
    elif result.get("results"):
        lines.append(f"Memory retrieval: {len(result['results'])} bounded result(s); status={result.get('status', 'ok')}.")
        for item in result["results"]:
            source = item.get("source", "unknown")
            title = item.get("title", "Untitled")
            snippet = item.get("snippet", "")
            lines.append(f"- [{source}] {title}: {snippet}")
    else:
        lines.append(f"Memory retrieval: no relevant canonical result ({result.get('reason', 'no-result')}).")
    diagnostics = [item for item in result.get("diagnostics", []) if item.get("status") not in {"ok", "stored"}]
    if diagnostics:
        lines.append("Memory diagnostics: " + "; ".join(f"{item.get('source', item.get('provider', 'unknown'))}={item.get('status', 'unknown')}" for item in diagnostics))
    return "\n".join(lines)

payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
cwd = Path(payload.get("cwd") or payload.get("workspace_root") or ".").resolve()
event = payload.get("hook_event_name", "SessionStart")
prompt = prompt_text(payload)
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
    ensure_sessions_ignored(root, cwd)
    marker = root / ".wiki-agent-system.json"
    marker_state = migrate_marker(marker)
    if marker_state in {"future", "invalid"}:
        detail = "newer" if marker_state == "future" else "invalid"
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": f"Workspace Wiki Agent System schema is {detail}; do not modify it until a compatible plugin is installed."}}))
        raise SystemExit(0)
    if event == "SessionStart":
        run_scheduled_retention(root)
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
            captured = datetime.now(timezone.utc)
            stamp = captured.strftime("%Y%m%dT%H%M%SZ")
            output = captures / f"{stamp}-{uuid.uuid4().hex[:8]}-semantic-fallback.md"
            capture_key = hashlib.sha256(f"{session_id}:{turn_id}".encode()).hexdigest()[:16]
            origin_workspace = root.parent if root else cwd
            canonical_uri = canonical_capture_uri("workspace", str(origin_workspace), capture_key)
            fd, temporary = tempfile.mkstemp(dir=captures, prefix=".capture-", text=True)
            with os.fdopen(fd, "w") as file:
                file.write(f"---\ntype: semantic-stop-capture\nschema: {CAPTURE_SCHEMA_VERSION}\nscope: workspace\ncanonical_uri: {json.dumps(canonical_uri)}\norigin_workspace: {json.dumps(str(origin_workspace))}\nstatus: pending-curation\nsupersedes: null\nvalid_from: {captured.isoformat()}\nvalid_until: null\nworkspace: {origin_workspace}\nsession_id: {session_id}\nturn_id: {turn_id}\nfallback: true\n---\n\n# Auto-saved final result\n\n## Outcome\n\n{message}\n\n## Decisions\n\n- Not extracted; review outcome during curation.\n\n## Artifacts\n\n- Not extracted; inspect workspace changes during curation.\n\n## Verification\n\n- Not extracted; review outcome during curation.\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, output)
        state.unlink(missing_ok=True)
    retrieval = safe_retrieve(cwd, prompt) if event == "UserPromptSubmit" else None
    policy = (Path(__file__).resolve().parents[1] / "defaults" / "policy.md").read_text().strip()
    index = (root / "_index.md").read_text()[:4000]
    captures = sorted((root / "inbox" / "autosave").glob("*.md"))[-3:]
    recent = "\n\n".join(path.read_text()[:2000] for path in captures) if not prompt or (retrieval and retrieval.get("intent", {}).get("matched")) else ""
    recent_context = f"\nRecent captures:\n{recent}" if recent else ""
    memory_context = f"\n{retrieval_context(retrieval)}" if retrieval and retrieval.get("intent", {}).get("matched") else ""
    text = f"{policy}\nWorkspace knowledge index:\n{index}{memory_context}{recent_context}"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "SessionStart"), "additionalContext": text}}))
