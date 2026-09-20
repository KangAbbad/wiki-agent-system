#!/usr/bin/env python3
import hashlib, json, sys, os, re, stat, subprocess, tempfile, time
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from wiki_ambient import capture, capture_intent, capture_key, parse_capture_message, redact, retrieve_memory
from evidence_verification import drain_due_queue as drain_public_verification_queue, preflight_context as public_verification_context
from youtube_fallback import (
    QueueError,
    acquire_file_lock,
    atomic_queue_write,
    canonical_video,
    drain_queue,
    ensure_queue,
    queue_id_for,
    queue_snapshot,
    recover_queue_leases,
    release_file_lock,
    safe_text,
    TIMEOUT_MIN,
    validate_caption_receipt,
)

IGNORED_WORKSPACE_DIRS = {".git", ".wiki", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", ".next", ".cache"}
WORKSPACE_SCHEMA_VERSION = 2
FINALIZER_STATE_SCHEMA_VERSION = 1
FOREGROUND_STATE_SCHEMA_VERSION = 1
FOREGROUND_LEGACY_STATE_SCHEMA_VERSION = 0
CAPTURE_KEY_PATTERN = re.compile(r"^[0-9a-f]{16}$")
QUEUE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
YOUTUBE_URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+")
YOUTUBE_ACTION_PATTERN = re.compile(
    r"\b(?:analy[sz]e|analisis|caption|cite|ingest|knowledge|kutip|pelajari|rangkum|research|riset|scrap(?:e|ing)?|subtitle|summar(?:ize|ise|y|izing|ising)|transcript|transkrip)\b",
    re.I,
)
YOUTUBE_PREFLIGHT_DEADLINE = 16
YOUTUBE_DRAIN_DEADLINE = 20
YOUTUBE_DRAIN_MAX_URLS = 2
YOUTUBE_HOOK_BUDGET = 40
FOREGROUND_LOOP_CAP = 4
FOREGROUND_STATE_DIRNAME = "foreground-loops"
FOREGROUND_LOCK_TIMEOUT = 0.5
FOREGROUND_STATES = frozenset({"pending", "running", "retryable", "evidence-ready", "verified", "exhausted", "blocked"})
FOREGROUND_ACTIONS = frozenset({"caption-attempt", "caption-retry", "knowledge-synthesis", "terminal-caption-result"})
FOREGROUND_TERMINAL_STATES = frozenset({"verified", "exhausted", "blocked"})
KNOWLEDGE_ARTIFACT_SCHEMA_VERSION = 1
KNOWLEDGE_ARTIFACT_MAX_FILES = 100
KNOWLEDGE_ARTIFACT_MAX_BYTES = 256 * 1024
KNOWLEDGE_ARTIFACT_HASH = re.compile(r"^[0-9a-f]{64}$")
TERMINAL_REPORT_PATTERN = re.compile(
    r"\b(?:status|state)\s*[:=]\s*(?:exhausted|blocked)\b.*\bprovenance\s*[:=]\s*[a-z-]+.*\b(?:transcript[_ -]?eligible|transcript)\s*[:=]\s*(?:false|no|not-available)",
    re.I | re.S,
)
TRANSCRIPT_CLAIM_PATTERN = re.compile(
    r"\b(?:transcript[- ]backed|canonical knowledge|caption evidence\s*=\s*verified)\b",
    re.I,
)

def atomic_json_write(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".schema-", text=True)
    with os.fdopen(fd, "w") as file:
        json.dump(data, file, sort_keys=True)
        file.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def foreground_controller_directory(root):
    directory = root / ".sessions" / "wiki-agent-system" / FOREGROUND_STATE_DIRNAME
    for parent in (root / ".sessions", root / ".sessions" / "wiki-agent-system", directory):
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise QueueError("foreground controller state path is not a private directory")
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(parent, 0o700)
    return directory


def foreground_controller_paths(root, queue_id):
    if not isinstance(queue_id, str) or not QUEUE_ID_PATTERN.fullmatch(queue_id):
        raise QueueError("invalid foreground controller queue reference")
    directory = root / ".sessions" / "wiki-agent-system" / FOREGROUND_STATE_DIRNAME
    return directory / f"{queue_id}.json", directory / f"{queue_id}.lock"


def foreground_state_timestamp(value):
    return type(value) in (int, float) and value >= 0 and value < 4102444800


def validate_foreground_controller(data, queue_id, schema_version=None):
    if not isinstance(data, dict):
        return "invalid", "foreground controller state is not an object"
    version = data.get("schema_version")
    expected = FOREGROUND_STATE_SCHEMA_VERSION if schema_version is None else schema_version
    if version != expected:
        if schema_version is None and isinstance(version, int) and version > FOREGROUND_STATE_SCHEMA_VERSION:
            return "future-schema", "foreground controller schema is newer than this runtime"
        return "invalid", "unsupported foreground controller schema"
    base = {
        "schema_version",
        "queue_id",
        "state",
        "action",
        "loop_count",
        "deadline_at",
        "budget_seconds",
        "revision",
        "reason",
        "report_attempts",
        "created_at",
        "updated_at",
    }
    legacy = base - {"budget_seconds", "report_attempts"}
    if set(data) != (base if expected == FOREGROUND_STATE_SCHEMA_VERSION else legacy):
        return "invalid", "foreground controller fields are invalid"
    if data.get("queue_id") != queue_id or data.get("state") not in FOREGROUND_STATES or data.get("action") not in FOREGROUND_ACTIONS:
        return "invalid", "foreground controller identity or transition is invalid"
    if type(data.get("loop_count")) is not int or not 0 <= data["loop_count"] <= FOREGROUND_LOOP_CAP:
        return "invalid", "foreground controller loop count is invalid"
    if type(data.get("revision")) is not int or not 0 <= data["revision"] <= 1000:
        return "invalid", "foreground controller revision is invalid"
    if type(data.get("budget_seconds")) is not int or not 1 <= data["budget_seconds"] <= YOUTUBE_HOOK_BUDGET:
        if expected == FOREGROUND_STATE_SCHEMA_VERSION:
            return "invalid", "foreground controller budget is invalid"
    if expected == FOREGROUND_STATE_SCHEMA_VERSION and (type(data.get("report_attempts")) is not int or not 0 <= data["report_attempts"] <= 1):
        return "invalid", "foreground controller report count is invalid"
    if not foreground_state_timestamp(data.get("deadline_at")) or not foreground_state_timestamp(data.get("created_at")) or not foreground_state_timestamp(data.get("updated_at")):
        return "invalid", "foreground controller timestamps are invalid"
    reason = data.get("reason")
    if not isinstance(reason, str) or not 1 <= len(reason) <= 160 or "\n" in reason or "\r" in reason:
        return "invalid", "foreground controller reason is invalid"
    return "valid", None


def read_foreground_controller(root, queue_id):
    path, _ = foreground_controller_paths(root, queue_id)
    if path.is_symlink():
        return "invalid", None, "foreground controller state is a symlink"
    if not path.exists():
        return "missing", None, None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return "invalid", None, "foreground controller state is unreadable"
    version = data.get("schema_version") if isinstance(data, dict) else None
    if version == FOREGROUND_LEGACY_STATE_SCHEMA_VERSION:
        status, error = validate_foreground_controller(data, queue_id, version)
        return ("legacy-schema", data, None) if status == "valid" else ("invalid", None, error)
    status, error = validate_foreground_controller(data, queue_id)
    return status, data if status == "valid" else None, error


def migrate_foreground_controller(data, queue_id):
    status, error = validate_foreground_controller(data, queue_id, FOREGROUND_LEGACY_STATE_SCHEMA_VERSION)
    if status != "valid":
        raise QueueError(error or "foreground controller migration failed")
    now = time.time()
    migrated = dict(data)
    migrated.update(
        {
            "schema_version": FOREGROUND_STATE_SCHEMA_VERSION,
            "budget_seconds": YOUTUBE_HOOK_BUDGET,
            "report_attempts": 0,
            "updated_at": now,
        }
    )
    status, error = validate_foreground_controller(migrated, queue_id)
    if status != "valid":
        raise QueueError(error or "foreground controller migration failed")
    return migrated


def new_foreground_controller(queue_id):
    now = time.time()
    return {
        "schema_version": FOREGROUND_STATE_SCHEMA_VERSION,
        "queue_id": queue_id,
        "state": "pending",
        "action": "caption-attempt",
        "loop_count": 0,
        "deadline_at": now + YOUTUBE_HOOK_BUDGET,
        "budget_seconds": YOUTUBE_HOOK_BUDGET,
        "revision": 0,
        "reason": "caption evidence pending",
        "report_attempts": 0,
        "created_at": now,
        "updated_at": now,
    }


def ensure_foreground_controller(root, queue_id):
    path, lock_path = foreground_controller_paths(root, queue_id)
    status, data, error = read_foreground_controller(root, queue_id)
    if status == "future-schema" or status == "invalid":
        return status, data, error
    if status == "valid":
        return status, data, error
    foreground_controller_directory(root)
    fd, reason = acquire_file_lock(lock_path, FOREGROUND_LOCK_TIMEOUT)
    if fd is None:
        return "unavailable", None, reason
    try:
        status, data, error = read_foreground_controller(root, queue_id)
        if status == "missing":
            data = new_foreground_controller(queue_id)
            atomic_queue_write(path, data)
            return "created", data, None
        if status == "legacy-schema":
            data = migrate_foreground_controller(data, queue_id)
            atomic_queue_write(path, data)
            return "migrated", data, None
        return status, data, error
    except (OSError, QueueError) as error:
        return "unavailable", None, safe_text(str(error), 120)
    finally:
        release_file_lock(fd)


def update_foreground_controller(root, queue_id, expected_revision, **changes):
    path, lock_path = foreground_controller_paths(root, queue_id)
    foreground_controller_directory(root)
    fd, reason = acquire_file_lock(lock_path, FOREGROUND_LOCK_TIMEOUT)
    if fd is None:
        return "unavailable", None, reason
    try:
        status, data, error = read_foreground_controller(root, queue_id)
        if status == "legacy-schema":
            data = migrate_foreground_controller(data, queue_id)
            status = "valid"
        if status != "valid":
            return status, data, error
        if expected_revision is not None and data["revision"] != expected_revision:
            return "stale", data, None
        for key, value in changes.items():
            if key not in data:
                return "invalid", None, "unknown foreground controller field"
            data[key] = value
        data["revision"] += 1
        data["updated_at"] = time.time()
        status, error = validate_foreground_controller(data, queue_id)
        if status != "valid":
            return status, None, error
        atomic_queue_write(path, data)
        return "updated", data, None
    except (OSError, QueueError) as error:
        return "unavailable", None, safe_text(str(error), 120)
    finally:
        release_file_lock(fd)

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


def foreground_queue_id(payload):
    if not isinstance(payload, dict):
        return None
    try:
        return queue_id_for(payload.get("session_id"), payload.get("turn_id"))
    except QueueError:
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
    queue_id = foreground_queue_id(payload)
    if queue_id:
        try:
            controller_status, _, _ = read_foreground_controller(root, queue_id)
        except (OSError, QueueError):
            controller_status = "invalid"
        if controller_status in {"valid", "legacy-schema"} and state_path.exists():
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
    if remaining <= TIMEOUT_MIN:
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
        return "caption_evidence=ineligible; reason=retry-pending; receipt=missing-or-invalid", "retry-scheduled"
    if item.get("status") == "exhausted":
        return "caption_evidence=ineligible; reason=retry-exhausted; receipt=missing-or-invalid", "exhausted"
    helper_status = item.get("helper_status")
    if helper_status == "stale-captions-ignored" or item.get("evidence_reason") == "stale-caption-ignored":
        return "caption_evidence=ineligible; reason=stale-or-unreceipted; receipt=missing-or-invalid", "stale-or-unreceipted"
    if item.get("status") == "ok" or item.get("files"):
        return "caption_evidence=ineligible; reason=unreceipted-or-invalid; receipt=missing-or-invalid", "unreceipted-or-invalid"
    return "caption_evidence=ineligible; reason=no-verified-caption; receipt=missing-or-invalid", "none"


def _knowledge_frontmatter(content):
    if not content.startswith("---\n"):
        return None, None
    marker = content.find("\n---\n", 4)
    if marker < 0:
        return None, None
    fields = {}
    for line in content[4:marker].splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key.strip()):
            return None, None
        value = value.strip()
        if len(value) > 4096:
            return None, None
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        fields[key.strip()] = value
    return fields, content[marker + len("\n---\n"):]


def _knowledge_list(value, separator=";"):
    if not isinstance(value, str) or not value:
        return []
    items = [entry.strip() for entry in value.split(separator)]
    return items if all(items) and len(items) == len(set(items)) else []


def _knowledge_artifact_path(wiki, path):
    if path.is_symlink() or not path.is_file():
        return False
    try:
        if not path.resolve().is_relative_to(wiki.resolve() / "wiki"):
            return False
    except OSError:
        return False
    current = path
    while current != wiki:
        if current.is_symlink():
            return False
        current = current.parent
    try:
        return stat.S_ISREG(path.lstat().st_mode) and path.stat().st_size <= KNOWLEDGE_ARTIFACT_MAX_BYTES
    except OSError:
        return False


def _knowledge_receipt_contract(wiki, snapshot):
    if snapshot.get("status") != "ok" or not snapshot.get("records"):
        return None
    records = []
    for item in snapshot["records"]:
        receipt = verified_caption_receipt(wiki, item)
        if not receipt:
            return None
        records.append((item, receipt))
    return records


def _knowledge_manifest_matches(path, fields, body, wiki, queue_id, records):
    if fields.get("type") != "youtube-knowledge" or fields.get("schema") != str(KNOWLEDGE_ARTIFACT_SCHEMA_VERSION):
        return False, "knowledge artifact type or schema is invalid"
    if fields.get("queue_id") != queue_id:
        return False, "knowledge artifact queue binding is invalid"
    expected_path = (Path("wiki") / path.relative_to(wiki / "wiki")).as_posix()
    if fields.get("artifact_path") != expected_path:
        return False, "knowledge artifact path binding is invalid"
    if fields.get("status") not in {"pending-curation", "canonical"}:
        return False, "knowledge artifact lifecycle is invalid"
    if fields.get("provenance_class") != "caption" or fields.get("evidence_status") != "verified":
        return False, "knowledge artifact provenance is invalid"
    if fields.get("grounded") != "true" or fields.get("quality_status") != "verified":
        return False, "knowledge artifact quality gate is invalid"
    artifact_hash = fields.get("artifact_sha256")
    if not KNOWLEDGE_ARTIFACT_HASH.fullmatch(artifact_hash or ""):
        return False, "knowledge artifact hash is invalid"
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != artifact_hash:
        return False, "knowledge artifact hash mismatch"
    expected_urls = [item["url"] for item, _ in records]
    expected_receipts = [receipt["receipt_id"] for _, receipt in records]
    expected_hashes = {
        f"{receipt['receipt_id']}/{entry['sha256']}"
        for _, receipt in records
        for entry in receipt["files"]
    }
    if set(_knowledge_list(fields.get("source_urls"))) != set(expected_urls):
        return False, "knowledge artifact source binding is invalid"
    if set(_knowledge_list(fields.get("receipt_ids"))) != set(expected_receipts):
        return False, "knowledge artifact receipt binding is invalid"
    if set(_knowledge_list(fields.get("receipt_hashes"))) != expected_hashes:
        return False, "knowledge artifact caption hash binding is invalid"
    if set(_knowledge_list(fields.get("claim_evidence"))) != expected_hashes:
        return False, "knowledge artifact claim evidence is incomplete"
    if not {"## Synthesis", "## Sources", "## Quality"}.issubset(set(re.findall(r"^## .+$", body, re.M))):
        return False, "knowledge artifact quality sections are missing"
    if len(body.strip()) < 80:
        return False, "knowledge artifact synthesis is too short"
    for item, receipt in records:
        if item["url"] not in body or receipt["receipt_id"] not in body:
            return False, "knowledge artifact synthesis is not grounded to its sources"
        for entry in receipt["files"]:
            if entry["sha256"] not in body:
                return False, "knowledge artifact synthesis is missing a caption hash reference"
    return True, None


def knowledge_artifact_snapshot(wiki, queue_id, snapshot):
    records = _knowledge_receipt_contract(wiki, snapshot)
    if records is None:
        return "unavailable", None, "receipt evidence is not fully verified"
    knowledge_root = wiki / "wiki"
    try:
        candidates = [path for path in knowledge_root.rglob("*.md") if path.is_file() and not path.is_symlink()]
    except OSError as error:
        return "unavailable", None, safe_text(str(error), 120)
    matches = []
    for path in candidates[:KNOWLEDGE_ARTIFACT_MAX_FILES]:
        try:
            if not _knowledge_artifact_path(wiki, path):
                continue
            content = path.read_text(encoding="utf-8")
            fields, body = _knowledge_frontmatter(content)
        except (OSError, UnicodeError):
            continue
        if not fields or fields.get("type") != "youtube-knowledge" or fields.get("queue_id") != queue_id:
            continue
        valid, reason = _knowledge_manifest_matches(path, fields, body, wiki, queue_id, records)
        matches.append((path, valid, reason))
    if len(matches) != 1:
        if len(matches) > 1:
            return "invalid", None, "duplicate knowledge artifacts"
        return "missing", None, "knowledge artifact is required"
    path, valid, reason = matches[0]
    return ("verified", path, None) if valid else ("invalid", None, reason)


def foreground_backoff_wait(root, queue_id, hook_deadline):
    """Wait only when the due time fits the current bounded hook budget."""
    snapshot = queue_snapshot(root, queue_id)
    if snapshot.get("status") != "ok":
        return False
    now = time.time()
    due_times = [
        item["next_retry_at"]
        for item in snapshot.get("records", [])
        if item.get("status") == "retryable"
        and type(item.get("next_retry_at")) in (int, float)
        and item["next_retry_at"] > now
    ]
    if not due_times:
        return False
    wait = min(due_times) - now
    remaining = hook_deadline - time.monotonic()
    if wait <= 0 or wait >= remaining:
        return False
    time.sleep(wait)
    return True


def foreground_outcome(wiki, snapshot):
    if snapshot.get("status") != "ok":
        return "blocked", "caption queue unavailable"
    records = snapshot.get("records") or []
    if not records:
        return "exhausted", "caption queue exhausted"
    statuses = {item.get("status") for item in records}
    if statuses & {"pending", "running"}:
        return "pending", "caption evidence pending"
    if "retryable" in statuses:
        return "retryable", "caption retry pending"
    if statuses & {"blocked", "blocked-install"}:
        return "blocked", "caption access boundary blocked"
    if all(item.get("status") == "ok" and verified_caption_receipt(wiki, item) for item in records):
        return "evidence-ready", "caption evidence verified; knowledge artifact required"
    return "exhausted", "caption queue exhausted"


def foreground_snapshot_signature(snapshot):
    if snapshot.get("status") != "ok":
        return (snapshot.get("status"), snapshot.get("error"))
    return tuple(
        (
            item.get("url"),
            item.get("status"),
            item.get("attempt"),
            item.get("retry_count"),
            item.get("next_retry_at"),
            item.get("lease_until"),
        )
        for item in snapshot.get("records", [])
    )


def foreground_context(root, snapshot, controller, urls):
    lines = ["YouTube evidence (bounded foreground acquisition):"]
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
            if item["status"] == "blocked":
                lines.append("  access=blocked; report bounded access boundary")
            if evidence == "retry-scheduled":
                lines.append("  retry=foreground worker bounded; no user action requested")
            if evidence == "exhausted":
                lines.append("  retry=automatic cap reached; transcript evidence remains unavailable")
            if transcript == "not-available":
                lines.append("  transcript=not-available; stale/unreceipted captions, metadata-only, and machine-transcription cannot support transcript claims")
        if all(item.get("status") == "ok" and verified_caption_receipt(root, item) for item in snapshot["records"]):
            artifact_status, _, _ = knowledge_artifact_snapshot(root, controller["queue_id"], snapshot)
            lines.append(f"- knowledge: artifact={artifact_status}; provenance=caption; quality={'verified' if artifact_status == 'verified' else 'required'}")
            if artifact_status != "verified":
                lines.append("  knowledge=artifact-required; finish the receipt-bound synthesis before completion")
        if len(urls) > 1:
            lines.append(f"- queue: terminal={snapshot['terminal']}; pending={snapshot['pending']}.")
    lines.append(f"- acquisition: state={controller['state']}")
    lines.append("Transcript evidence requires caption_evidence=verified with receipt_id and caption_sha256; stale/unreceipted captions, metadata-only, and machine-transcription are ineligible for transcript facts.")
    if controller["state"] == "evidence-ready":
        lines.append("Knowledge completion requires one receipt-bound, hash-bound, grounded artifact with verified provenance and quality sections before Stop can finish.")
    if controller["state"] == "verified":
        lines.append("Knowledge artifact finalization gate: verified.")
    return "\n".join(lines)


def advance_foreground_controller(root, cwd, prompt, payload):
    urls = youtube_prompt_urls(prompt)
    queue_id = foreground_queue_id(payload)
    if not urls and not queue_id:
        return ""
    if urls and not queue_id:
        return "YouTube evidence:\n- acquisition=unavailable; no transcript claim is eligible."
    try:
        if urls:
            ensure_queue(root, urls, queue_id)
            controller_status, controller, _ = ensure_foreground_controller(root, queue_id)
        else:
            controller_status, controller, _ = read_foreground_controller(root, queue_id)
            if controller_status == "missing":
                return ""
        if controller_status in {"future-schema", "invalid", "unavailable"}:
            return "YouTube evidence:\n- acquisition=unavailable; no transcript claim is eligible."
        if controller["state"] in FOREGROUND_TERMINAL_STATES:
            snapshot = queue_snapshot(root, queue_id)
            return foreground_context(root, snapshot, controller, urls)
        snapshot = queue_snapshot(root, queue_id)
        if controller["state"] == "evidence-ready":
            artifact_status, _, artifact_reason = knowledge_artifact_snapshot(root, queue_id, snapshot)
            if artifact_status == "verified":
                updated, next_controller, _ = update_foreground_controller(
                    root,
                    queue_id,
                    controller["revision"],
                    state="verified",
                    action="terminal-caption-result",
                    reason="knowledge artifact finalized",
                )
                controller = next_controller if updated in {"updated", "stale"} and next_controller else controller
                return foreground_context(root, snapshot, controller, urls)
            loop_count = controller["loop_count"] + 1
            state = "exhausted" if loop_count >= FOREGROUND_LOOP_CAP else "evidence-ready"
            action = "terminal-caption-result" if state == "exhausted" else "knowledge-synthesis"
            reason = "knowledge artifact gate exhausted" if state == "exhausted" else safe_text(artifact_reason or "knowledge artifact required", 160)
            updated, next_controller, _ = update_foreground_controller(
                root,
                queue_id,
                controller["revision"],
                state=state,
                action=action,
                loop_count=loop_count,
                reason=reason,
            )
            controller = next_controller if updated in {"updated", "stale"} and next_controller else controller
            return foreground_context(root, snapshot, controller, urls)
        now = time.time()
        hook_deadline = time.monotonic() + min(YOUTUBE_HOOK_BUDGET, max(1, controller["deadline_at"] - now))
        while True:
            now = time.time()
            if controller["loop_count"] >= FOREGROUND_LOOP_CAP or controller["deadline_at"] <= now:
                reason = "foreground loop budget exhausted" if controller["deadline_at"] <= now else "foreground loop cap exhausted"
                updated, next_controller, _ = update_foreground_controller(
                    root,
                    queue_id,
                    controller["revision"],
                    state="exhausted",
                    action="terminal-caption-result",
                    reason=reason,
                )
                controller = next_controller if updated in {"updated", "stale"} and next_controller else controller
                return foreground_context(root, queue_snapshot(root, queue_id), controller, urls)

            action = controller["action"]
            if action == "caption-retry":
                retry_snapshot = queue_snapshot(root, queue_id)
                has_future_retry = any(
                    item.get("status") == "retryable"
                    and type(item.get("next_retry_at")) in (int, float)
                    and item["next_retry_at"] > time.time()
                    for item in retry_snapshot.get("records", [])
                )
                if has_future_retry and not foreground_backoff_wait(root, queue_id, hook_deadline):
                    updated, next_controller, _ = update_foreground_controller(
                        root,
                        queue_id,
                        controller["revision"],
                        state="exhausted",
                        action="terminal-caption-result",
                        reason="foreground retry budget exhausted",
                    )
                    controller = next_controller if updated in {"updated", "stale"} and next_controller else controller
                    return foreground_context(root, queue_snapshot(root, queue_id), controller, urls)

            before_snapshot = queue_snapshot(root, queue_id)
            result = bounded_youtube_drain(
                cwd,
                queue_id,
                YOUTUBE_PREFLIGHT_DEADLINE if action == "caption-attempt" else YOUTUBE_DRAIN_DEADLINE,
                hook_deadline,
                YOUTUBE_DRAIN_MAX_URLS,
            )
            snapshot = queue_snapshot(root, queue_id)
            state, reason = foreground_outcome(root, snapshot)
            loop_count = controller["loop_count"] + 1
            progressed = (
                result.get("processed", 0) > 0
                and foreground_snapshot_signature(before_snapshot) != foreground_snapshot_signature(snapshot)
            )
            if not progressed and state not in FOREGROUND_TERMINAL_STATES:
                state, reason = "exhausted", "foreground worker made no progress"
            elif state not in FOREGROUND_TERMINAL_STATES and (
                loop_count >= FOREGROUND_LOOP_CAP or controller["deadline_at"] <= time.time()
            ):
                state, reason = "exhausted", "foreground loop budget exhausted"
            next_action = "terminal-caption-result" if state in FOREGROUND_TERMINAL_STATES else "caption-retry"
            if state == "pending":
                next_action = "caption-attempt"
            elif state == "evidence-ready":
                next_action = "knowledge-synthesis"
            updated, next_controller, _ = update_foreground_controller(
                root,
                queue_id,
                controller["revision"],
                state=state,
                action=next_action,
                loop_count=loop_count,
                reason=reason,
            )
            controller = next_controller if updated in {"updated", "stale"} and next_controller else controller
            if state in FOREGROUND_TERMINAL_STATES or state == "evidence-ready" or updated not in {"updated", "stale"}:
                return foreground_context(root, snapshot, controller, urls)
    except (OSError, QueueError):
        return "YouTube evidence:\n- acquisition=unavailable; no transcript claim is eligible."


def terminal_report_is_safe(payload, controller):
    message = payload.get("last_assistant_message") if isinstance(payload, dict) else None
    if not isinstance(message, str):
        return False
    message = redact(message).strip()
    if TRANSCRIPT_CLAIM_PATTERN.search(message):
        return False
    return bool(TERMINAL_REPORT_PATTERN.search(message)) and controller["state"] in {"exhausted", "blocked"}


def discard_finalizer_state(root, payload):
    state_path = finalizer_state(root, payload)
    if state_path is None:
        return
    try:
        state_path.unlink(missing_ok=True)
    except OSError:
        pass


def stop_foreground_gate(root, payload):
    queue_id = foreground_queue_id(payload)
    if not queue_id:
        return None
    try:
        status, controller, _ = read_foreground_controller(root, queue_id)
    except (OSError, QueueError):
        return None
    if status == "legacy-schema":
        status, controller, _ = ensure_foreground_controller(root, queue_id)
    if status in {"future-schema", "invalid", "unavailable"}:
        return {
            "block": False,
            "reason": "foreground evidence unavailable; semantic capture suppressed",
            "capture": False,
        }
    if status not in {"valid", "created", "migrated"} or not controller:
        return None
    snapshot = queue_snapshot(root, queue_id)
    if controller["state"] in {"evidence-ready", "verified"}:
        artifact_status, _, artifact_reason = knowledge_artifact_snapshot(root, queue_id, snapshot)
        if artifact_status != "verified":
            if controller["state"] == "verified":
                updated, next_controller, _ = update_foreground_controller(
                    root,
                    queue_id,
                    controller["revision"],
                    state="evidence-ready",
                    action="knowledge-synthesis",
                    reason="knowledge artifact invalidated",
                )
                controller = next_controller if updated in {"updated", "stale"} and next_controller else controller
            return {
                "block": True,
                "reason": "Caption evidence is ready; finish the receipt-bound knowledge artifact before completing.",
                "capture": False,
            }
        if controller["state"] == "evidence-ready":
            updated, next_controller, _ = update_foreground_controller(
                root,
                queue_id,
                controller["revision"],
                state="verified",
                action="terminal-caption-result",
                reason="knowledge artifact finalized",
            )
            controller = next_controller if updated in {"updated", "stale"} and next_controller else controller
    if controller["state"] not in FOREGROUND_TERMINAL_STATES:
        return {
            "block": False,
            "reason": "",
            "capture": True,
        }
    if controller["state"] == "exhausted" and snapshot.get("status") == "ok":
        if any(item.get("status") in {"pending", "running", "retryable"} for item in snapshot.get("records", [])):
            return {"block": False, "reason": "", "capture": True}
    if controller["state"] in {"exhausted", "blocked"} and not terminal_report_is_safe(payload, controller):
        if controller["report_attempts"] == 0:
            _, updated, _ = update_foreground_controller(
                root,
                queue_id,
                controller["revision"],
                reason="terminal provenance report required",
                report_attempts=1,
            )
            revision = updated["revision"] if updated else controller["revision"]
            return {
                "block": True,
                "reason": "A terminal YouTube evidence result needs a sanitized provenance report before completion.",
                "capture": False,
            }
        discard_finalizer_state(root, payload)
        return {"block": False, "reason": "terminal provenance report missing; semantic capture suppressed", "capture": False}
    return {"block": False, "reason": "", "capture": True}


def interrupt_foreground_controller(root, cwd, payload):
    queue_id = foreground_queue_id(payload)
    if not queue_id:
        return
    try:
        status, controller, _ = read_foreground_controller(root, queue_id)
        if status == "legacy-schema":
            status, controller, _ = ensure_foreground_controller(root, queue_id)
        if status not in {"valid", "created", "migrated"} or not controller:
            return
        if controller["state"] not in FOREGROUND_TERMINAL_STATES:
            recover_queue_leases(cwd, queue_id)
            update_foreground_controller(
                root,
                queue_id,
                controller["revision"],
                reason="interrupted checkpoint preserved",
            )
    except (OSError, QueueError, TypeError, ValueError):
        return


def youtube_preflight_context(root, cwd, prompt, payload):
    return advance_foreground_controller(root, cwd, prompt, payload)


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
    stop_gate = None
    if event == "SessionStart":
        run_scheduled_retention(root)
        run_scheduled_public_verification(root)
    if event == "UserPromptSubmit":
        write_finalizer_state(root, payload, prompt)
    if event == "Interrupt":
        interrupt_foreground_controller(root, cwd, payload)
    if event == "Stop":
        stop_gate = stop_foreground_gate(root, payload)
        if stop_gate and stop_gate["block"]:
            print(json.dumps({"decision": "block", "reason": stop_gate["reason"]}))
            raise SystemExit(0)
        if not stop_gate or stop_gate["capture"]:
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
