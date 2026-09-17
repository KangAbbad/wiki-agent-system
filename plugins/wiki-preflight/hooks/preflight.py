#!/usr/bin/env python3
import hashlib, json, sys, os, re, subprocess, tempfile, time
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from wiki_ambient import capture, capture_intent, capture_key, parse_capture_message, redact, retrieve_memory
from evidence_verification import drain_due_queue as drain_public_verification_queue, preflight_context as public_verification_context
from youtube_fallback import (
    QueueError,
    canonical_video,
    drain_due_queues,
    drain_queue,
    ensure_queue,
    queue_id_for,
    queue_snapshot,
    safe_text,
    validate_caption_receipt,
)

IGNORED_WORKSPACE_DIRS = {".git", ".wiki", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", ".next", ".cache"}
WORKSPACE_SCHEMA_VERSION = 2
FINALIZER_STATE_SCHEMA_VERSION = 1
CAPTURE_KEY_PATTERN = re.compile(r"^[0-9a-f]{16}$")
YOUTUBE_URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+")
YOUTUBE_ACTION_PATTERN = re.compile(
    r"\b(?:analy[sz]e|analisis|caption|cite|ingest|knowledge|kutip|pelajari|rangkum|research|riset|scrap(?:e|ing)?|subtitle|summar(?:ize|ise|y|izing|ising)|transcript|transkrip)\b",
    re.I,
)
YOUTUBE_PREFLIGHT_MAX_URLS = 2
YOUTUBE_PREFLIGHT_DEADLINE = 16
YOUTUBE_DRAIN_DEADLINE = 20
YOUTUBE_DRAIN_MAX_URLS = 2
YOUTUBE_HOOK_BUDGET = 40

def atomic_json_write(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".schema-", text=True)
    with os.fdopen(fd, "w") as file:
        json.dump(data, file, sort_keys=True)
        file.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)

def marker_path(root):
    """Keep plugin-owned state outside the LLM Wiki content schema."""
    return root / ".sessions" / "wiki-agent-system" / "marker.json"


def marker_data(marker):
    try:
        data = json.loads(marker.read_text())
        version = data.get("schema_version")
    except (OSError, json.JSONDecodeError, AttributeError):
        return "invalid", None
    if version == WORKSPACE_SCHEMA_VERSION:
        return "current", data
    if version == 1:
        data["schema_version"] = WORKSPACE_SCHEMA_VERSION
        return "migrated", data
    return ("future" if isinstance(version, int) and version > WORKSPACE_SCHEMA_VERSION else "invalid"), None


def migrate_marker(root):
    """Migrate the legacy root marker without exposing it to wiki lint."""
    marker = marker_path(root)
    legacy = root / ".wiki-agent-system.json"
    if marker.exists():
        state, data = marker_data(marker)
        if state in {"future", "invalid"}:
            return state
        if legacy.exists():
            legacy_state, legacy_data = marker_data(legacy)
            if legacy_state in {"future", "invalid"}:
                return legacy_state
            if data != legacy_data:
                return "conflict"
            try:
                legacy.unlink()
            except OSError:
                return "invalid"
        if state == "migrated":
            atomic_json_write(marker, data)
        return state
    if legacy.exists():
        state, data = marker_data(legacy)
        if state in {"future", "invalid"}:
            return state
        # Write the runtime copy first.  An interrupted migration leaves the
        # legacy marker intact rather than losing compatibility state.
        atomic_json_write(marker, data)
        try:
            legacy.unlink()
        except OSError:
            return "invalid"
        return state
    atomic_json_write(marker, {"schema_version": WORKSPACE_SCHEMA_VERSION})
    return "created"

def task_identity(payload):
    if not isinstance(payload, dict):
        return None
    for field in ("session_id", "thread_id", "conversation_id"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def finalizer_state(root, payload):
    identity = task_identity(payload)
    if identity is None:
        return None
    turn_id = payload.get("turn_id")
    turn_id = turn_id.strip() if isinstance(turn_id, str) and turn_id.strip() else "unknown"
    key = hashlib.sha256(f"{identity}:{turn_id}".encode()).hexdigest()[:16]
    return root / ".sessions" / "wiki-agent-system" / "finalizers" / f"{key}.json"


def write_finalizer_state(root, payload, prompt):
    state_path = finalizer_state(root, payload)
    if state_path is None:
        return
    atomic_json_write(
        state_path,
        {
            "schema_version": FINALIZER_STATE_SCHEMA_VERSION,
            "started_at": time.time(),
            "capture_key": capture_key(task_identity(payload)),
            "prompt_intent": capture_intent(prompt),
        },
    )


def stop_owned_capture(root, cwd, payload):
    state_path = finalizer_state(root, payload)
    if state_path is None:
        return
    cleanup_state = False
    try:
        if not state_path.exists():
            return
        try:
            state = json.loads(state_path.read_text())
        except (OSError, json.JSONDecodeError, TypeError):
            return
        if state.get("schema_version") != FINALIZER_STATE_SCHEMA_VERSION:
            return
        cleanup_state = True
        started_at = state.get("started_at")
        key = state.get("capture_key")
        intent = state.get("prompt_intent")
        if not isinstance(started_at, (int, float)) or not isinstance(key, str) or not CAPTURE_KEY_PATTERN.fullmatch(key):
            return
        message = payload.get("last_assistant_message")
        if not isinstance(message, str):
            return
        message = redact(message).strip()
        if not message:
            return
        prompt_qualifies = isinstance(intent, dict) and intent.get("matched") is True
        if not prompt_qualifies and not workspace_changed_since(cwd, started_at):
            return
        parsed = parse_capture_message(message)
        capture(
            str(cwd),
            parsed["outcome"],
            "result",
            parsed["artifacts"],
            parsed["decisions"],
            parsed["verifications"],
            parsed["sources"],
            parsed["confidence"],
            parsed["open_questions"],
            "workspace",
            task_key=key,
        )
    except (OSError, SystemExit, TypeError, ValueError):
        # Stop is fail-open: capture failure must not interrupt the response.
        return
    finally:
        if cleanup_state:
            try:
                state_path.unlink(missing_ok=True)
            except OSError:
                pass

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


def run_scheduled_public_verification(root):
    """Drain due public evidence claims without requiring a new prompt."""
    try:
        drain_public_verification_queue(root, deadline=4.0, limit=2)
    except (OSError, SystemExit, TypeError, ValueError):
        return


def run_scheduled_youtube_retries(root):
    """Recover due caption retries without delaying the user prompt."""
    try:
        drain_due_queues(root.parent, deadline_seconds=8, limit=2)
    except (OSError, QueueError, SystemExit, TypeError, ValueError):
        return


def prompt_text(payload):
    for key in ("prompt", "user_prompt", "user_message", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value[:4000]
    return ""


def youtube_prompt_urls(prompt):
    if not prompt or not YOUTUBE_ACTION_PATTERN.search(prompt):
        return []
    urls = []
    for match in YOUTUBE_URL_PATTERN.finditer(prompt):
        candidate = match.group(0).rstrip(".,;:!?)]}")
        try:
            _, canonical = canonical_video(candidate)
        except ValueError:
            continue
        if canonical not in urls:
            urls.append(canonical)
    return urls


def bounded_youtube_drain(cwd, queue_id, max_seconds, hook_deadline, limit):
    remaining = hook_deadline - time.monotonic()
    if remaining < 1:
        return {"status": "deadline", "queue_id": queue_id, "processed": 0}
    return drain_queue(
        cwd,
        queue_id,
        min(max_seconds, remaining),
        concurrency=1,
        limit=limit,
    )


def verified_caption_receipt(wiki, item):
    receipt = item.get("receipt")
    if (
        item.get("status") != "ok"
        or item.get("provenance_class") != "caption"
        or item.get("evidence_eligible") is not True
        or item.get("transcript_eligible") is not True
        or not isinstance(receipt, dict)
    ):
        return None
    try:
        validate_caption_receipt(
            receipt,
            wiki=wiki,
            expected_url=item.get("url"),
            expected_video_id=item.get("video_id"),
            verify_files=True,
        )
        receipt_files = [PurePosixPath(entry["path"]).name for entry in receipt["files"]]
    except (OSError, TypeError, ValueError, KeyError):
        return None
    return receipt if item.get("files") == receipt_files else None


def evidence_context(wiki, item):
    receipt = verified_caption_receipt(wiki, item)
    if receipt:
        hashes = ",".join(entry["sha256"] for entry in receipt["files"])
        files = "; ".join(PurePosixPath(entry["path"]).name for entry in receipt["files"])
        return (
            f"caption_evidence=verified; receipt_id={safe_text(receipt['receipt_id'], 80)}; "
            f"caption_sha256={safe_text(hashes, 400)}; receipt_files={safe_text(files, 400)}",
            "eligible",
        )
    if item.get("status") == "metadata-only" or (item.get("status") == "no-captions" and item.get("metadata_state") == "acquired"):
        return "caption_evidence=ineligible; reason=metadata-only; receipt=missing-or-invalid", "metadata-only"
    if item.get("status") == "retryable":
        category = safe_text(str(item.get("error_class") or "caption-fetch-failed"), 80)
        return f"caption_evidence=ineligible; reason=retry-scheduled; category={category}; user-retry=not-required", "retry-scheduled"
    if item.get("status") == "exhausted":
        category = safe_text(str(item.get("error_class") or "caption-fetch-failed"), 80)
        return f"caption_evidence=ineligible; reason=retry-exhausted; category={category}; receipt=missing-or-invalid", "exhausted"
    helper_status = item.get("helper_status")
    if helper_status == "stale-captions-ignored" or item.get("evidence_reason") == "stale-caption-ignored":
        return "caption_evidence=ineligible; reason=stale-or-unreceipted; receipt=missing-or-invalid", "stale-or-unreceipted"
    if item.get("status") == "ok" or item.get("files"):
        return "caption_evidence=ineligible; reason=unreceipted-or-invalid; receipt=missing-or-invalid", "unreceipted-or-invalid"
    return "caption_evidence=ineligible; reason=no-verified-caption; receipt=missing-or-invalid", "none"


def youtube_preflight_context(root, cwd, prompt, payload):
    urls = youtube_prompt_urls(prompt)
    if not urls:
        return ""

    lines = ["YouTube automatic fallback preflight (bounded; no installer approval supplied):"]
    try:
        queue_id = queue_id_for(payload.get("session_id"), payload.get("turn_id"))
        ensure_queue(root, urls, queue_id)
        hook_deadline = time.monotonic() + YOUTUBE_HOOK_BUDGET
        initial = bounded_youtube_drain(cwd, queue_id, YOUTUBE_PREFLIGHT_DEADLINE, hook_deadline, YOUTUBE_PREFLIGHT_MAX_URLS)
        automatic = bounded_youtube_drain(cwd, queue_id, YOUTUBE_DRAIN_DEADLINE, hook_deadline, YOUTUBE_DRAIN_MAX_URLS) if initial.get("status") == "ok" else initial
        snapshot = queue_snapshot(root, queue_id)
    except (OSError, QueueError) as error:
        lines.append(f"- queue: status=unavailable; reason={safe_text(str(error), 160)}")
        lines.append("Transcript evidence requires a verified receipt; stale/unreceipted captions, metadata-only, and machine-transcription are ineligible for transcript facts.")
        return "\n".join(lines)

    if snapshot.get("status") != "ok":
        lines.append(f"- queue: status={safe_text(str(snapshot.get('status') or 'unavailable'), 80)}; no mutation")
    else:
        for item in snapshot["records"]:
            files = item.get("files") if isinstance(item.get("files"), list) else []
            provenance = safe_text(str(item.get("provenance_class") or "none"), 40)
            evidence_detail, evidence = evidence_context(root, item)
            transcript = "eligible" if evidence == "eligible" else "not-available"
            lines.append(
                f"- {item['url']}: status={item['status']}; provenance={provenance}; "
                f"evidence={evidence}; transcript={transcript}; files={len(files)}"
            )
            lines.append(f"  {evidence_detail}")
            if files and evidence == "eligible":
                lines.append(f"  caption_files={'; '.join(files[:8])}")
            elif files:
                lines.append(f"  stale_caption_files={'; '.join(safe_text(str(file), 120) for file in files[:8])}")
            if item["status"] == "blocked-install":
                lines.append("  installation=blocked; no user action requested; report bounded verification status")
            if evidence == "retry-scheduled":
                lines.append("  retry=automatic on a later SessionStart; do not ask the user to repeat the prompt")
            if evidence == "exhausted":
                lines.append("  retry=automatic cap reached; transcript evidence remains unavailable")
            if transcript == "not-available":
                lines.append("  transcript=not-available; stale/unreceipted captions, metadata-only, and machine-transcription cannot support transcript claims")
        if len(urls) > YOUTUBE_PREFLIGHT_MAX_URLS:
            lines.append(
                f"- queued={len(urls) - YOUTUBE_PREFLIGHT_MAX_URLS} additional YouTube URL(s); "
                f"automatic drain processed={automatic.get('processed', 0)}; "
                f"terminal={snapshot['terminal']}; pending={snapshot['pending']}."
            )
    lines.append("Transcript evidence requires caption_evidence=verified with receipt_id and caption_sha256; stale/unreceipted captions, metadata-only, and machine-transcription are ineligible for transcript facts.")
    return "\n".join(lines)


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
    marker_state = migrate_marker(root)
    if marker_state in {"future", "invalid", "conflict"}:
        detail = "newer" if marker_state == "future" else "conflicting" if marker_state == "conflict" else "invalid"
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": f"Workspace Wiki Agent System schema is {detail}; do not modify it until a compatible plugin is installed."}}))
        raise SystemExit(0)
    if event == "SessionStart":
        run_scheduled_retention(root)
        run_scheduled_public_verification(root)
        run_scheduled_youtube_retries(root)
    if event == "UserPromptSubmit":
        write_finalizer_state(root, payload, prompt)
    if event == "Stop":
        stop_owned_capture(root, cwd, payload)
    retrieval = safe_retrieve(cwd, prompt) if event == "UserPromptSubmit" else None
    youtube_context = youtube_preflight_context(root, cwd, prompt, payload) if event == "UserPromptSubmit" else ""
    if event == "UserPromptSubmit":
        try:
            public_context = public_verification_context(root, prompt)
        except (OSError, SystemExit, TypeError, ValueError):
            public_context = "Public evidence verification: status=unverified; agent-owned bounded lookup/retry required."
    else:
        public_context = ""
    policy = (Path(__file__).resolve().parents[1] / "defaults" / "policy.md").read_text().strip()
    index = (root / "_index.md").read_text()[:4000]
    captures = sorted((root / "inbox" / "autosave").glob("*.md"))[-3:]
    recent = "\n\n".join(path.read_text()[:2000] for path in captures) if not prompt or (retrieval and retrieval.get("intent", {}).get("matched")) else ""
    recent_context = f"\nRecent captures:\n{recent}" if recent else ""
    memory_context = f"\n{retrieval_context(retrieval)}" if retrieval and retrieval.get("intent", {}).get("matched") else ""
    youtube_context = f"\n{youtube_context}" if youtube_context else ""
    public_context = f"\n{public_context}" if public_context else ""
    text = f"{policy}\nWorkspace knowledge index:\n{index}{memory_context}{youtube_context}{public_context}{recent_context}"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "SessionStart"), "additionalContext": text}}))
