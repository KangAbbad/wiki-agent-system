#!/usr/bin/env python3
"""Validate and resolve user-owned ambient LLM Wiki routing."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
from youtube_fallback import YOUTUBE_HOSTS, acquire_file_lock, canonical_video, read_caption_receipt, release_file_lock


CONFIG_TEMPLATE = Path(__file__).resolve().parents[1] / "defaults" / "ambient.json"
USER_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "llm-wiki" / "wiki-agent-system.json"
AMBIENT_SCHEMA_VERSION = 4
CAPTURE_SCHEMA_VERSION = 2
CAPTURE_SCOPES = frozenset({"workspace", "user", "personal", "uncertain"})
LIFECYCLE_STATES = frozenset({"pending-curation", "canonical", "superseded", "retracted"})
LIFECYCLE_TRANSITIONS = {
    "pending-curation": frozenset({"canonical", "superseded", "retracted"}),
    "canonical": frozenset({"superseded", "retracted"}),
    "superseded": frozenset(),
    "retracted": frozenset(),
}
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SENSITIVE = re.compile(
    r"((?:api[_ -]?key|password|secret|token|private[_ -]?key|authorization)\s*[:=])[^\r\n]*",
    re.I,
)
MNEMOSYNE_TIMEOUT_SECONDS = 2.0
PERSONAL_MEMORY_MAX_CHARS = 240
PERSONAL_TRANSCRIPT_MARKERS = re.compile(
    r"(?:^|\s)(?:user|assistant|system|developer|human|agent)\s*:", re.I
)
RETRIEVAL_MAX_RESULTS = 5
RETRIEVAL_MAX_BYTES = 6000
RETRIEVAL_MAX_FILE_BYTES = 32768
RETRIEVAL_SIGNALS = {
    "continuation": re.compile(r"\b(?:continue|resume|previous|earlier|last time|as before|pick up)\b", re.I),
    "prior-decision": re.compile(r"\b(?:decision|decided|rationale|trade[- ]?off|agreed|why did we)\b", re.I),
    "research": re.compile(r"\b(?:research|investigat|evidence|source|paper|spec(?:ification)?|reference)\b", re.I),
    "architecture": re.compile(r"\b(?:architect(?:ure)?|design|schema|migration|component|integration)\b", re.I),
    "repeated-investigation": re.compile(r"\b(?:same (?:bug|issue|problem)|regression|revisit|historical)\b", re.I),
}
CAPTURE_INTENT_SIGNALS = {
    **RETRIEVAL_SIGNALS,
    "implementation": re.compile(
        r"\b(?:implement|build|create|modify|edit|fix|debug|refactor|write|update|remove|delete|migrat(?:e|ion)|deploy|release|patch|change|kerjakan|buat|perbaiki|ubah|hapus|migrasi|rilis)\b",
        re.I,
    ),
    "synthesis": re.compile(
        r"\b(?:analy[sz]e|synthesi[sz]e|summar(?:ize|ise|y|izing|ising)|review|rangkum|sintesis|analisis)\b",
        re.I,
    ),
    "verification": re.compile(
        r"\b(?:verify|validate|test|audit|check|acceptance|verifikasi|validasi|uji)\b",
        re.I,
    ),
    "plan": re.compile(r"\b(?:plan|planning|roadmap|rencana|rencanakan)\b", re.I),
}
RETRIEVAL_STOPWORDS = {
    "about",
    "again",
    "after",
    "and",
    "are",
    "been",
    "before",
    "from",
    "have",
    "into",
    "that",
    "the",
    "this",
    "with",
    "what",
    "when",
    "where",
    "which",
    "will",
    "would",
}
CAPTURE_MESSAGE_MAX_CHARS = 12000
CAPTURE_SECTION_MAX_CHARS = 3000
CAPTURE_SECTION_ITEM_MAX_CHARS = 800
CAPTURE_SECTION_ITEM_MAX_COUNT = 16
CAPTURE_HEADING = re.compile(
    r"^#{1,3}\s+(?P<name>outcome|decisions?|artifacts?|files?|verifications?|tests?|sources?|references?|open questions?|confidence)\s*:?[ \t]*$",
    re.I | re.M,
)
CAPTURE_SECTION_NAMES = {
    "outcome": "outcome",
    "decision": "decisions",
    "decisions": "decisions",
    "artifact": "artifacts",
    "artifacts": "artifacts",
    "file": "artifacts",
    "files": "artifacts",
    "verification": "verifications",
    "verifications": "verifications",
    "test": "verifications",
    "tests": "verifications",
    "source": "sources",
    "sources": "sources",
    "reference": "sources",
    "references": "sources",
    "open question": "open_questions",
    "open questions": "open_questions",
    "confidence": "confidence",
}


def git_root(path: Path):
    resolved = path.expanduser().resolve()
    return next((candidate for candidate in (resolved, *resolved.parents) if (candidate / ".git").exists()), None)


def ensure_private_state_outside_git():
    targets = {
        "home": Path.home(),
        "user configuration": USER_CONFIG,
        "user Wiki": Path.home() / "wiki",
    }
    for label, path in targets.items():
        repository = git_root(path)
        if repository:
            raise SystemExit(f"private {label} path must be outside Git repository: {path}")


def load():
    ensure_private_state_outside_git()
    user_config_exists = USER_CONFIG.exists()
    try:
        data = json.loads(USER_CONFIG.read_text() if user_config_exists else CONFIG_TEMPLATE.read_text())
    except FileNotFoundError:
        raise SystemExit(f"missing config template: {CONFIG_TEMPLATE}")
    except json.JSONDecodeError as error:
        raise SystemExit(f"invalid JSON in {USER_CONFIG if user_config_exists else CONFIG_TEMPLATE}: {error}")
    version = data.get("schema_version")
    if type(version) is int and version > AMBIENT_SCHEMA_VERSION:
        raise SystemExit("ambient config schema is newer; install a compatible runtime before writing")
    migrated = False
    if data.get("schema_version") == 1:
        topics = {}
        for workspace, topic in data.get("workspace_topics", {}).items():
            topics.setdefault(topic, {"workspace_roots": [], "aliases": []})["workspace_roots"].append(workspace)
        data["schema_version"] = 2
        data["topics"] = topics
        migrated = True
    if data.get("schema_version") == 2:
        data["schema_version"] = 3
        migrated = True
    if data.get("schema_version") == 3:
        retention = data.get("retention")
        if isinstance(retention, dict):
            if retention.get("trash_days") == 30:
                retention["trash_days"] = 7
            elif "trash_days" not in retention:
                retention["trash_days"] = 7
        data["schema_version"] = 4
        migrated = True
    if isinstance(data.get("retention"), dict) and "max_bytes" not in data["retention"]:
        data["retention"]["max_bytes"] = 1073741824
        migrated = True
    if data.get("schema_version") != AMBIENT_SCHEMA_VERSION or not isinstance(data.get("workspace_topics"), dict) or not isinstance(data.get("topics"), dict):
        raise SystemExit("invalid ambient config schema")
    capture = data.get("capture")
    if (
        not isinstance(capture, dict)
        or capture.get("schema_version") != 1
        or capture.get("status") != "pending-curation"
        or capture.get("required") != ["outcome"]
        or not isinstance(capture.get("fields"), list)
        or capture.get("redact_sensitive") is not True
    ):
        raise SystemExit("invalid capture config schema")
    retention = data.get("retention")
    if (
        not isinstance(retention, dict)
        or not isinstance(retention.get("max_bytes"), int)
        or retention["max_bytes"] <= 0
        or not isinstance(retention.get("trash_days"), int)
        or retention["trash_days"] <= 0
        or any(not isinstance(retention.get(name), int) or not 0 < retention[name] <= 100 for name in ("warn_percent", "plan_percent", "block_large_write_percent"))
        or not retention["warn_percent"] < retention["plan_percent"] < retention["block_large_write_percent"]
    ):
        raise SystemExit("invalid retention quota schema")
    for workspace, topic in data["workspace_topics"].items():
        if not Path(workspace).is_absolute() or not isinstance(topic, str) or not SLUG.fullmatch(topic):
            raise SystemExit("workspace_topics must map absolute paths to lowercase topic slugs")
    for topic, metadata in data["topics"].items():
        if not SLUG.fullmatch(topic) or not isinstance(metadata, dict):
            raise SystemExit("topics must use lowercase topic slugs")
        roots = metadata.get("workspace_roots", [])
        aliases = metadata.get("aliases", [])
        if not isinstance(roots, list) or not isinstance(aliases, list) or any(not Path(root).is_absolute() for root in roots) or any(not isinstance(alias, str) or not SLUG.fullmatch(alias) for alias in aliases):
            raise SystemExit("invalid topic metadata")
    if not user_config_exists or migrated:
        save(data)
    return data


def save(data):
    USER_CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=USER_CONFIG.parent, delete=False) as file:
        json.dump(data, file, indent=2)
        file.write("\n")
        temporary = file.name
    os.chmod(temporary, 0o600)
    os.replace(temporary, USER_CONFIG)


def wiki_status(root):
    if root is None:
        return "absent"
    required = ("config.md", "_index.md", "raw", "wiki")
    return "valid" if all((root / item).exists() for item in required) else "foreign"


def resolve(cwd: str):
    data = load()
    path = Path(cwd).resolve()
    local_root = next((candidate / ".wiki" for candidate in (path, *path.parents) if (candidate / ".wiki").is_dir()), None)
    matches = [(Path(workspace), topic) for workspace, topic in data["workspace_topics"].items() if path.is_relative_to(Path(workspace))]
    if matches:
        topic = max(matches, key=lambda item: len(item[0].parts))[1]
        reason = "workspace"
        candidates = [topic]
    else:
        names = {candidate.name.lower().replace("_", "-") for candidate in (path, *path.parents)}
        candidates = sorted(topic for topic, metadata in data["topics"].items() if {topic, *metadata["aliases"]} & names)
        topic = candidates[0] if len(candidates) == 1 else None
        reason = "alias" if topic else None
    print(json.dumps({"enabled": data["enabled"], "cwd": str(path), "topic": topic, "reason": reason, "candidates": candidates, "local_wiki": str(local_root) if local_root else None, "local_wiki_status": wiki_status(local_root)}))


def redact(value: str) -> str:
    """Remove credential values while retaining enough context for curation."""
    return SENSITIVE.sub(lambda match: f"{match.group(1)} [REDACTED]", value).strip()


def mnemosyne_scope() -> str:
    scope = os.environ.get("MNEMOSYNE_DEFAULT_SCOPE", "").strip().lower()
    return scope if scope in {"session", "global"} else "session"


def mnemosyne_executable():
    configured = os.environ.get("MNEMOSYNE_CLI", "").strip()
    if configured:
        return shutil.which(configured) or (configured if Path(configured).is_file() else None)
    return shutil.which("mnemosyne")


def adapter_detail(value: str) -> str:
    return redact(value or "").replace("\n", " ")[:240]


def mnemosyne_command(arguments: list[str], scope: str, timeout: float = MNEMOSYNE_TIMEOUT_SECONDS) -> dict:
    executable = mnemosyne_executable()
    if not executable:
        return {"status": "unavailable", "provider": "mnemosyne", "scope": scope, "reason": "cli-not-found"}
    environment = {
        name: os.environ[name]
        for name in ("HOME", "PATH", "TMPDIR", "XDG_CONFIG_HOME")
        if os.environ.get(name)
    }
    environment["MNEMOSYNE_DEFAULT_SCOPE"] = scope
    try:
        result = subprocess.run(
            [executable, *arguments],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "provider": "mnemosyne", "scope": scope, "reason": "adapter-timeout"}
    except OSError as error:
        return {"status": "unavailable", "provider": "mnemosyne", "scope": scope, "reason": adapter_detail(str(error))}
    if result.returncode:
        return {"status": "error", "provider": "mnemosyne", "scope": scope, "reason": adapter_detail(result.stderr or result.stdout) or "adapter-failed"}
    return {"status": "ok", "provider": "mnemosyne", "scope": scope, "stdout": result.stdout}


def personal_memory_summary(decisions: list[str]) -> str:
    if len(decisions) != 1:
        return ""
    candidate = decisions[0]
    if "\n" in candidate or "\r" in candidate or PERSONAL_TRANSCRIPT_MARKERS.search(candidate):
        return ""
    candidate = re.sub(r"\s+", " ", redact(candidate)).strip()
    return candidate if 0 < len(candidate) <= PERSONAL_MEMORY_MAX_CHARS else ""


def mnemosyne_remember(summary: str, canonical_uri: str) -> dict:
    if not summary:
        return {"status": "abstained", "provider": "mnemosyne", "scope": mnemosyne_scope(), "reason": "no-explicit-personal-fact"}
    scope = mnemosyne_scope()
    result = mnemosyne_command(["store", summary, f"wiki-preflight:{canonical_uri}", "0.5"], scope)
    if result["status"] != "ok":
        return result
    if not result.get("stdout", "").lstrip().startswith("Stored:"):
        return {"status": "invalid", "provider": "mnemosyne", "scope": scope, "reason": "invalid-adapter-response"}
    return {"status": "stored", "provider": "mnemosyne", "scope": scope}


def mnemosyne_recall(query: str, limit: int = 2, timeout: float = MNEMOSYNE_TIMEOUT_SECONDS) -> dict:
    scope = mnemosyne_scope()
    if not query:
        return {"status": "abstained", "provider": "mnemosyne", "scope": scope, "results": [], "reason": "empty-query"}
    result = mnemosyne_command(["recall", query, str(limit), "--json"], scope, timeout)
    if result["status"] != "ok":
        result["results"] = []
        result.pop("stdout", None)
        return result
    try:
        payload = json.loads(result.get("stdout", ""))
        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            raise ValueError("results must be a list")
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"status": "invalid", "provider": "mnemosyne", "scope": scope, "results": [], "reason": "invalid-adapter-response"}
    results = []
    for item in raw_results[:limit]:
        if not isinstance(item, dict):
            continue
        content = re.sub(r"\s+", " ", redact(str(item.get("content", "")))).strip()[:400]
        try:
            score = float(item.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        if content:
            results.append({"content": content, "score": score})
    return {"status": "ok", "provider": "mnemosyne", "scope": scope, "results": results}


def lines(values: list[str]) -> str:
    return "\n".join(f"- {redact(value)}" for value in values if redact(value)) or "- None"


def safe_artifact_paths(artifacts: list[str]) -> list[str]:
    safe = []
    for artifact in artifacts:
        candidate = Path(artifact)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise SystemExit("artifacts must be workspace-relative paths")
        safe.append(candidate.as_posix())
    return safe


def atomic_write(path: Path, content: str):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".capture-", text=True)
    try:
        with os.fdopen(descriptor, "w") as file:
            file.write(content)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def capture_key(identity=None) -> str:
    """Keep one capture per Codex task without exposing its runtime identifier."""
    session = identity or os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_THREAD_ID") or uuid.uuid4().hex
    return hashlib.sha256(session.encode()).hexdigest()[:16]


def capture_section_items(body: str) -> list[str]:
    items = []
    for line in body[:CAPTURE_SECTION_MAX_CHARS].splitlines():
        match = re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+?)\s*$", line)
        if not match:
            continue
        value = redact(match.group(1)).strip()[:CAPTURE_SECTION_ITEM_MAX_CHARS]
        if value:
            items.append(value)
        if len(items) == CAPTURE_SECTION_ITEM_MAX_COUNT:
            break
    return items


def capture_artifact_items(items: list[str]) -> list[str]:
    artifacts = []
    for item in items:
        candidate = item.strip()
        if candidate.startswith("`") and candidate.endswith("`"):
            candidate = candidate[1:-1].strip()
        else:
            link = re.fullmatch(r"\[[^\]]+\]\(([^)]+)\)", candidate)
            if link:
                candidate = link.group(1).strip()
            elif " " in candidate or not re.search(r"[./]", candidate):
                continue
        try:
            path = Path(candidate)
        except (OSError, TypeError, ValueError):
            continue
        if not candidate or candidate == "." or path.is_absolute() or ".." in path.parts or any(character in candidate for character in "\x00<>[]()"):
            continue
        artifacts.append(path.as_posix())
    return list(dict.fromkeys(artifacts))


def parse_capture_message(message: str) -> dict:
    text = redact(str(message or ""))[:CAPTURE_MESSAGE_MAX_CHARS].strip()
    sections = {}
    headings = list(CAPTURE_HEADING.finditer(text))[:16]
    preamble = text[:headings[0].start()].strip() if headings else text
    for index, heading in enumerate(headings):
        body_end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        name = CAPTURE_SECTION_NAMES[heading.group("name").lower()]
        sections[name] = text[heading.end():body_end].strip()[:CAPTURE_SECTION_MAX_CHARS]
    outcome = (sections.get("outcome") or preamble or text).strip()[:CAPTURE_SECTION_MAX_CHARS]
    confidence = sections.get("confidence", "").strip().lower()
    if confidence not in {"low", "medium", "high", "unverified"}:
        confidence_match = re.search(r"(?im)^\s*confidence\s*:\s*(low|medium|high|unverified)\s*$", text)
        confidence = confidence_match.group(1).lower() if confidence_match else "unverified"
    return {
        "outcome": outcome,
        "decisions": capture_section_items(sections.get("decisions", "")),
        "artifacts": capture_artifact_items(capture_section_items(sections.get("artifacts", ""))),
        "verifications": capture_section_items(sections.get("verifications", "")),
        "sources": capture_section_items(sections.get("sources", "")),
        "confidence": confidence,
        "open_questions": capture_section_items(sections.get("open_questions", "")),
    }


def canonical_capture_uri(scope: str, origin_workspace: str, key: str) -> str:
    if scope not in CAPTURE_SCOPES:
        raise SystemExit("capture scope is invalid")
    origin = str(Path(origin_workspace).resolve())
    origin_hash = hashlib.sha256(origin.encode()).hexdigest()[:16]
    return f"wiki://{scope}/capture/{origin_hash}/{key}"


def capture_schema(content: str) -> int:
    """Read known capture versions; leave unknown future records untouched."""
    header = content.split("\n---", 1)[0] if content.startswith("---\n") else ""
    match = re.search(r"^schema:\s*(.+?)\s*$", header, re.M)
    if not match:
        return 1
    try:
        schema = int(match.group(1))
    except ValueError:
        raise SystemExit("capture schema is invalid")
    if schema < 1:
        raise SystemExit("capture schema is invalid")
    if schema > CAPTURE_SCHEMA_VERSION:
        raise SystemExit("capture schema is newer; install a compatible runtime before writing")
    return schema


def frontmatter_fields(content: str) -> dict:
    if not content.startswith("---\n"):
        raise SystemExit("record frontmatter is missing")
    marker = re.search(r"(?m)^---\s*$", content[4:])
    if not marker:
        raise SystemExit("record frontmatter is invalid")
    fields = {}
    for line in content[4 : 4 + marker.start()].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value == "null":
            fields[key] = None
        elif value.startswith('"'):
            try:
                fields[key] = json.loads(value)
            except json.JSONDecodeError:
                raise SystemExit("record frontmatter is invalid")
        else:
            fields[key] = value
    return fields


def frontmatter_literal(key: str, value) -> str:
    if value is None:
        return "null"
    if key in {"canonical_uri", "origin_workspace", "supersedes"}:
        return json.dumps(value)
    return str(value)


def update_frontmatter(content: str, updates: dict) -> str:
    marker = re.search(r"(?m)^---\s*$", content[4:]) if content.startswith("---\n") else None
    if not marker:
        raise SystemExit("record frontmatter is invalid")
    close = 4 + marker.start()
    lines = content[4:close].splitlines()
    replaced = set()
    for index, line in enumerate(lines):
        key = line.split(":", 1)[0].strip() if ":" in line else ""
        if key in updates:
            lines[index] = f"{key}: {frontmatter_literal(key, updates[key])}"
            replaced.add(key)
    lines.extend(
        f"{key}: {frontmatter_literal(key, value)}"
        for key, value in updates.items()
        if key not in replaced
    )
    return "---\n" + "\n".join(lines) + "\n" + content[close:]


def valid_canonical_uri(value) -> bool:
    if not isinstance(value, str) or any(character.isspace() for character in value):
        return False
    parsed = urlparse(value)
    parts = parsed.path.split("/")
    return (
        parsed.scheme == "wiki"
        and parsed.netloc in CAPTURE_SCOPES
        and len(parts) == 4
        and parts[1] == "capture"
        and all(parts[2:])
        and not parsed.query
        and not parsed.fragment
    )


def resolve_local_canonical_record(local_wiki: Path, canonical_uri: str) -> Path:
    matches = []
    # ponytail: bounded local scan; use a Wiki index only when repository size makes this measurable.
    for path in local_wiki.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            if not path.resolve().is_relative_to(local_wiki):
                continue
            content = path.read_text(encoding="utf-8")
            if not content.startswith("---\n"):
                continue
            fields = frontmatter_fields(content)
        except (OSError, UnicodeError, SystemExit):
            continue
        if fields.get("canonical_uri") == canonical_uri:
            matches.append((path, fields.get("status")))
    if len(matches) != 1 or matches[0][1] != "canonical":
        raise SystemExit("supersedes URI must resolve to exactly one local canonical record")
    return matches[0][0]


def lifecycle_record(cwd: str, record: str):
    load()
    route = json.loads(capture_resolve(cwd))
    if route["local_wiki_status"] != "valid":
        raise SystemExit("lifecycle transition requires a valid local LLM Wiki")
    local_wiki = Path(route["local_wiki"]).resolve()
    workspace = local_wiki.parent
    candidate = Path(record).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    candidate = candidate.resolve()
    if not candidate.is_relative_to(local_wiki) or not candidate.is_file():
        raise SystemExit("record must be an existing file inside the local Wiki")
    content = candidate.read_text(encoding="utf-8")
    schema = capture_schema(content)
    if schema < CAPTURE_SCHEMA_VERSION:
        raise SystemExit("lifecycle transition requires capture schema 2")
    fields = frontmatter_fields(content)
    if fields.get("type") not in {"autosave-capture", "semantic-stop-capture"}:
        raise SystemExit("only capture records can be transitioned")
    current = fields.get("status")
    canonical_uri = fields.get("canonical_uri")
    if current not in LIFECYCLE_STATES or current not in LIFECYCLE_TRANSITIONS:
        raise SystemExit("record lifecycle status is invalid")
    if not valid_canonical_uri(canonical_uri):
        raise SystemExit("record canonical_uri is invalid")
    return route, local_wiki, candidate, content, fields


def transition_record(cwd: str, record: str, status: str, supersedes):
    route, local_wiki, source, content, fields = lifecycle_record(cwd, record)
    current = fields["status"]
    if status not in LIFECYCLE_STATES or status == "pending-curation":
        raise SystemExit("target lifecycle status is invalid")
    if status not in LIFECYCLE_TRANSITIONS[current]:
        raise SystemExit(f"invalid lifecycle transition: {current} -> {status}")
    if status == "canonical":
        if supersedes is not None:
            raise SystemExit("canonical records cannot declare supersedes")
        updates = {"status": "canonical", "valid_until": None}
    else:
        if not valid_canonical_uri(supersedes):
            raise SystemExit("superseded and retracted records require a canonical supersedes URI")
        if supersedes == fields["canonical_uri"]:
            raise SystemExit("record cannot supersede itself")
        resolve_local_canonical_record(local_wiki, supersedes)
        updates = {"status": status, "supersedes": supersedes, "valid_until": datetime.now(timezone.utc).isoformat()}
    updated = update_frontmatter(content, updates)
    canonical_root = local_wiki / "wiki"
    destination = source
    if not source.is_relative_to(canonical_root):
        destination = canonical_root / "captures" / source.name
        if destination.exists():
            raise SystemExit("canonical destination already exists")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination != source:
        source.rename(destination)
    atomic_write(destination, updated)
    print(json.dumps({
        "path": str(destination),
        "previous_path": str(source) if destination != source else None,
        "canonical_uri": fields["canonical_uri"],
        "status": status,
        "supersedes": updates.get("supersedes"),
        "valid_from": fields.get("valid_from"),
        "valid_until": updates.get("valid_until", fields.get("valid_until")),
    }))


def redact_values(values: list[str]) -> list[str]:
    redacted = []
    for value in values:
        value = redact(value)
        if value:
            redacted.append(value)
    return redacted


def resolve_capture_scope(requested: str, route: dict) -> str:
    if requested == "auto":
        if route["local_wiki_status"] == "valid":
            return "workspace"
        if route["local_wiki_status"] == "foreign":
            raise SystemExit("auto scope cannot bypass a foreign or incomplete local Wiki")
        return "user" if route["topic"] else "uncertain"
    if requested not in CAPTURE_SCOPES:
        raise SystemExit("capture scope is invalid")
    if requested == "workspace" and route["local_wiki_status"] != "valid":
        raise SystemExit("workspace scope requires a valid local LLM Wiki")
    return requested


def workspace_identity(route: dict, workspace: Path) -> Path:
    if route.get("local_wiki"):
        return Path(route["local_wiki"]).parent
    if route.get("workspace_root"):
        return Path(route["workspace_root"])
    return workspace


def capture_destination(scope: str, route: dict) -> Path:
    if scope == "workspace":
        return Path(route["local_wiki"]) / "inbox" / "autosave"
    user_root = (Path.home() / "wiki").resolve()
    if scope == "user":
        if route["topic"] and (user_root / "topics" / route["topic"]).is_dir():
            return user_root / "topics" / route["topic"] / "inbox" / "autosave"
        return user_root / "inbox" / "autosave"
    if scope == "uncertain":
        return user_root / "inbox" / "pending-scope"
    raise SystemExit("capture scope has no Wiki destination")


def capture_lock_path(scope: str, route: dict) -> Path:
    if scope == "workspace":
        root = Path(route["local_wiki"]).resolve()
    else:
        root = (Path.home() / "wiki").resolve()
    return root / ".sessions" / "wiki-agent-system" / "capture.lock"


def personal_handoff(
    workspace: Path,
    key: str,
    canonical_uri: str,
    captured: datetime,
    outcome: str,
    kind: str,
    artifacts: list[str],
    decisions: list[str],
    verifications: list[str],
    sources: list[str],
    confidence: str,
    open_questions: list[str],
):
    adapter = mnemosyne_remember(personal_memory_summary(decisions), canonical_uri)
    return {
        "status": "handoff-required",
        "scope": "personal",
        "provider": "mnemosyne",
        "adapter": adapter,
        "canonical_uri": canonical_uri,
        "origin_workspace": str(workspace),
        "capture_key": key,
        "record": {
            "schema": CAPTURE_SCHEMA_VERSION,
            "status": "pending-curation",
            "supersedes": None,
            "valid_from": captured.isoformat(),
            "valid_until": None,
        },
        "payload": {
            "outcome": outcome,
            "kind": kind,
            "artifacts": artifacts,
            "decisions": redact_values(decisions),
            "verification": redact_values(verifications),
            "sources": redact_values(sources),
            "confidence": confidence,
            "open_questions": redact_values(open_questions),
        },
    }


def prior_items(content: str, heading: str) -> list[str]:
    match = re.search(rf"^## {re.escape(heading)}\n\n(.*?)(?=^## |\Z)", content, re.M | re.S)
    if not match:
        return []
    return [line[2:].strip() for line in match.group(1).splitlines() if line.startswith("- ") and line[2:].strip() != "None"]


def merge_items(previous: list[str], current: list[str]) -> list[str]:
    return list(dict.fromkeys([*previous, *current]))


def highest_confidence(previous: str, current: str) -> str:
    levels = {"unverified": 0, "low": 1, "medium": 2, "high": 3}
    return max((previous, current), key=lambda value: levels.get(value, 0))


def storage_report(root: Path) -> dict:
    active = quarantine = total = 0
    if not root.exists() or root.is_symlink():
        return {"active_autosave_bytes": active, "quarantine_bytes": quarantine, "total_bytes": total}
    for path in root.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            size = path.stat().st_size
            relative = path.relative_to(root)
            total += size
            if relative.parts[:2] == ("inbox", "autosave"):
                active += size
            elif relative.parts[:1] == (".trash",):
                quarantine += size
        except OSError:
            continue
    return {"active_autosave_bytes": active, "quarantine_bytes": quarantine, "total_bytes": total}


def storage_diagnostics(storage: dict) -> list[dict]:
    if not storage["quarantine_bytes"]:
        return []
    return [{
        "code": "quarantine-counts-toward-quota",
        "message": "Quarantine does not reclaim disk: quarantined files remain on disk and count toward the total Wiki quota.",
        "quarantine_bytes": storage["quarantine_bytes"],
    }]


def quota_status(data: dict, root: Path, projected_bytes=None, storage=None) -> dict:
    retention = data["retention"]
    storage = storage or storage_report(root)
    used = storage["total_bytes"]
    projected = used if projected_bytes is None else projected_bytes
    max_bytes = retention["max_bytes"]
    percent = projected * 100 / max_bytes
    if percent >= retention["block_large_write_percent"]:
        level = "block"
    elif percent >= retention["plan_percent"]:
        level = "plan"
    elif percent >= retention["warn_percent"]:
        level = "warn"
    else:
        level = "normal"
    return {
        "level": level,
        "used_bytes": used,
        "projected_bytes": projected,
        "max_bytes": max_bytes,
        "percent": round(percent, 2),
        "basis": "total_bytes",
        "storage": storage,
        "diagnostics": storage_diagnostics(storage),
    }


def capture(
    cwd: str,
    outcome: str,
    kind: str,
    artifacts: list[str],
    decisions: list[str],
    verifications: list[str],
    sources: list[str],
    confidence: str,
    open_questions: list[str],
    scope: str = "auto",
    task_key=None,
):
    data = load()
    if not data.get("auto_capture"):
        raise SystemExit("auto capture is disabled")
    outcome = redact(outcome)
    if not outcome:
        raise SystemExit("capture outcome is empty")
    route = json.loads(capture_resolve(cwd))
    workspace = Path(route["cwd"])
    origin_workspace = workspace_identity(route, workspace)
    scope = resolve_capture_scope(scope, route)
    safe_artifacts = safe_artifact_paths(artifacts)
    captured = datetime.now(timezone.utc)
    key = task_key or capture_key()
    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{16}", key):
        raise SystemExit("capture key is invalid")
    canonical_uri = canonical_capture_uri(scope, str(origin_workspace), key)
    if scope == "personal":
        return personal_handoff(
            origin_workspace,
            key,
            canonical_uri,
            captured,
            outcome,
            kind,
            safe_artifacts,
            decisions,
            verifications,
            sources,
            confidence,
            open_questions,
        )
    destination = capture_destination(scope, route)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    output = destination / f"session-{key}.md"
    lock_fd, lock_reason = acquire_file_lock(capture_lock_path(scope, route))
    if lock_fd is None:
        raise SystemExit(f"capture lock unavailable: {lock_reason}")
    try:
        previous = output.read_text() if output.exists() else ""
        if previous:
            capture_schema(previous)
        previous_confidence = re.search(r"^confidence: (.+)$", previous, re.M)
        decisions = merge_items(prior_items(previous, "Decisions"), redact_values(decisions))
        artifacts = merge_items(prior_items(previous, "Artifacts"), [f"`{artifact}`" for artifact in safe_artifacts])
        verifications = merge_items(prior_items(previous, "Verification"), redact_values(verifications))
        sources = merge_items(prior_items(previous, "Sources"), redact_values(sources))
        open_questions = merge_items(prior_items(previous, "Open questions"), redact_values(open_questions))
        confidence = highest_confidence(previous_confidence.group(1).strip() if previous_confidence else "unverified", confidence)
        content = f"""---
type: autosave-capture
schema: {CAPTURE_SCHEMA_VERSION}
scope: {scope}
canonical_uri: {json.dumps(canonical_uri)}
origin_workspace: {json.dumps(str(origin_workspace))}
status: pending-curation
supersedes: null
valid_from: {captured.isoformat()}
valid_until: null
kind: {kind}
workspace: {origin_workspace}
topic: {route['topic'] or 'unresolved'}
capture_key: {key}
captured: {captured.isoformat()}
confidence: {confidence}
---

# Auto-saved work capture

## Outcome

{outcome}

## Decisions

{lines(decisions)}

## Artifacts

{lines(artifacts)}

## Verification

{lines(verifications)}

## Sources

{lines(sources)}

## Open questions

{lines(open_questions)}
"""
        quota = None
        if scope == "workspace":
            existing_bytes = output.stat().st_size if output.exists() else 0
            storage = storage_report(Path(route["local_wiki"]))
            projected = storage["total_bytes"] - existing_bytes + len(content.encode("utf-8"))
            quota = quota_status(data, Path(route["local_wiki"]), projected, storage)
            if quota["level"] == "block" and len(content.encode("utf-8")) > 256 * 1024:
                raise SystemExit("workspace wiki quota blocks this large capture; run retention report and quarantine expired operational data")
        atomic_write(output, content)
        return {"path": str(output), "topic": route["topic"], "scope": scope, "canonical_uri": canonical_uri, "status": "updated" if previous else "pending-curation", "quota": quota}
    finally:
        release_file_lock(lock_fd)


def source_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "source"


def canonicalize(cwd: str, source: str, source_url: str, title: str, receipt_id=None):
    """Promote only supplied, attributable, non-sensitive source material to raw/."""
    load()
    route = json.loads(capture_resolve(cwd))
    if route["local_wiki_status"] != "valid":
        raise SystemExit("canonicalization requires a valid local LLM Wiki")
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SystemExit("source URL must be an absolute http(s) URL")
    receipt = None
    if (parsed.hostname or "").lower().rstrip(".") in YOUTUBE_HOSTS:
        try:
            video_id, source_url = canonical_video(source_url)
        except ValueError as error:
            raise SystemExit(f"source URL is not a valid YouTube video: {error}") from error
        if not receipt_id:
            raise SystemExit("YouTube caption canonicalization requires --receipt")
        try:
            receipt = read_caption_receipt(
                route["local_wiki"],
                receipt_id,
                expected_url=source_url,
                expected_video_id=video_id,
                verify_files=True,
            )
        except (OSError, TypeError, ValueError) as error:
            raise SystemExit(f"caption receipt rejected: {error}") from error
    clean_title = title.strip()
    if not clean_title or "\n" in clean_title or SENSITIVE.search(clean_title):
        raise SystemExit("source title is empty, multiline, or sensitive")
    source_candidate = Path(source).expanduser()
    if source_candidate.is_symlink():
        raise SystemExit("source must not be a symlink")
    source_path = source_candidate.resolve()
    if not source_path.is_file():
        raise SystemExit("source must be a readable file")
    if source_path.name.startswith(".env"):
        raise SystemExit("environment files cannot be canonicalized")
    if source_path.stat().st_size > 2 * 1024 * 1024:
        raise SystemExit("source exceeds 2 MiB; store a source reference instead")
    try:
        body_bytes = source_path.read_bytes()
        body = body_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise SystemExit("source must be UTF-8 text")
    if not body.strip() or SENSITIVE.search(body):
        raise SystemExit("source is empty or contains sensitive material")
    digest = hashlib.sha256(body_bytes).hexdigest()
    if receipt:
        try:
            source_relative = source_path.relative_to(Path(route["local_wiki"]).resolve()).as_posix()
        except ValueError as error:
            raise SystemExit("caption source must stay inside the active Wiki") from error
        matching = [entry for entry in receipt["files"] if entry["path"] == source_relative]
        if len(matching) != 1 or matching[0]["sha256"] != digest:
            raise SystemExit("source does not match the caption receipt file and hash")
    destination = Path(route["local_wiki"]) / "raw"
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    output = destination / f"{source_slug(clean_title)}-{digest[:12]}.md"
    if receipt and output.is_symlink():
        raise SystemExit("existing canonical evidence is a symlink")
    origin_workspace = workspace_identity(route, Path(route["cwd"]))
    canonical_uri = canonical_capture_uri("workspace", str(origin_workspace), f"source-{digest}")
    if not output.exists():
        retrieved_at = datetime.now(timezone.utc)
        retrieved = retrieved_at.date().isoformat()
        receipt_fields = (
            f"receipt_id: {receipt['receipt_id']}\n"
            f"transcript_sha256: {digest}\n"
            "provenance_class: caption\n"
            "evidence_status: verified\n"
            if receipt
            else ""
        )
        content = f"""---
type: raw-source
title: {clean_title}
source_url: {source_url}
retrieved: {retrieved}
content_sha256: {digest}
{receipt_fields}scope: workspace
canonical_uri: {json.dumps(canonical_uri)}
origin_workspace: {json.dumps(str(origin_workspace))}
status: canonical
supersedes: null
valid_from: {retrieved_at.isoformat()}
valid_until: null
---

# {clean_title}

{body.rstrip()}
        """
        atomic_write(output, content)
    if receipt:
        try:
            existing = frontmatter_fields(output.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SystemExit) as error:
            raise SystemExit("existing canonical evidence is not receipt-bound") from error
        if any(existing.get(key) != value for key, value in {
            "receipt_id": receipt["receipt_id"],
            "transcript_sha256": digest,
            "content_sha256": digest,
            "provenance_class": "caption",
            "evidence_status": "verified",
        }.items()):
            raise SystemExit("existing canonical evidence is not receipt-bound")
    result = {"path": str(output), "status": "canonical-evidence", "canonical_uri": canonical_uri, "source_url": source_url, "content_sha256": digest}
    if receipt:
        result.update({"receipt_id": receipt["receipt_id"], "transcript_sha256": digest})
    print(json.dumps(result))


def intent_gate(prompt: str) -> dict:
    text = redact(str(prompt or "")).strip()
    signals = [name for name, pattern in RETRIEVAL_SIGNALS.items() if pattern.search(text)]
    tokens = []
    for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text.lower()):
        if token not in RETRIEVAL_STOPWORDS and token not in tokens:
            tokens.append(token)
    return {"matched": bool(signals), "signals": signals, "query": " ".join(tokens[:24])}


def capture_intent(prompt: str) -> dict:
    text = redact(str(prompt or ""))[:4000].strip()
    signals = [name for name, pattern in CAPTURE_INTENT_SIGNALS.items() if pattern.search(text)]
    return {"matched": bool(signals), "signals": signals[:8]}


def retrieval_roots(root: Path, user: bool) -> list[Path]:
    roots = [root / "wiki", root / "raw"]
    if user:
        topics = root / "topics"
        if topics.is_dir():
            roots.extend(topic / section for topic in sorted(topics.iterdir()) if topic.is_dir() for section in ("wiki", "raw"))
    return [path for path in roots if path.is_dir()]


def canonical_retrieval_record(path: Path, root: Path, source: str, tokens: list[str]):
    try:
        if path.stat().st_size > RETRIEVAL_MAX_FILE_BYTES:
            return None
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    fields = {}
    if content.startswith("---\n"):
        try:
            fields = frontmatter_fields(content)
        except SystemExit:
            return None
    if fields.get("status") != "canonical" or not valid_canonical_uri(fields.get("canonical_uri")):
        return None
    searchable = content.lower()
    score = sum(searchable.count(token) for token in tokens)
    if not score:
        return None
    body = content.split("\n---", 1)[1] if content.startswith("---\n") and "\n---" in content else content
    match = next((re.search(re.escape(token), body, re.I) for token in tokens if re.search(re.escape(token), body, re.I)), None)
    start = max(0, (match.start() if match else 0) - 120)
    snippet = re.sub(r"\s+", " ", redact(body[start:])).strip()[:480]
    title = fields.get("title") or next((heading.strip() for heading in re.findall(r"^#\s+(.+)$", body, re.M)), path.stem)
    return {
        "source": source,
        "path": path.relative_to(root).as_posix(),
        "title": redact(str(title))[:160],
        "snippet": snippet,
        "canonical_uri": fields["canonical_uri"],
        "score": score,
        "confidence": "canonical",
    }


def bounded_canonical_retrieval(root: Path, source: str, user: bool, tokens: list[str], limit: int, timeout: float) -> tuple[list[dict], str]:
    deadline = time.monotonic() + timeout
    results = []
    seen = set()
    for base in retrieval_roots(root, user):
        try:
            paths = base.rglob("*.md")
            for path in paths:
                if time.monotonic() >= deadline:
                    return sorted(results, key=lambda item: (-item["score"], item["path"]))[:limit], "timeout"
                resolved = path.resolve()
                if resolved in seen or not resolved.is_relative_to(root):
                    continue
                seen.add(resolved)
                record = canonical_retrieval_record(resolved, root, source, tokens)
                if record:
                    results.append(record)
        except OSError:
            continue
    return sorted(results, key=lambda item: (-item["score"], item["path"]))[:limit], "ok"


def trim_retrieval_results(results: list[dict], limit: int, max_bytes: int) -> list[dict]:
    selected = []
    used = 0
    for result in results:
        encoded = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        if len(selected) >= limit or used + encoded > max_bytes:
            break
        selected.append(result)
        used += encoded
    return selected


def retrieve_memory(cwd: str, prompt: str, limit: int = RETRIEVAL_MAX_RESULTS, max_bytes: int = RETRIEVAL_MAX_BYTES, timeout: float = MNEMOSYNE_TIMEOUT_SECONDS) -> dict:
    gate = intent_gate(prompt)
    result = {"status": "abstained", "intent": gate, "results": [], "diagnostics": []}
    if not gate["matched"] or not gate["query"]:
        result["reason"] = "no-intent-signal"
        return result
    route = json.loads(capture_resolve(cwd))
    tokens = gate["query"].split()
    results = []
    if route["local_wiki_status"] == "valid":
        local_results, status = bounded_canonical_retrieval(Path(route["local_wiki"]), "workspace", False, tokens, limit, timeout)
        results.extend(local_results)
        result["diagnostics"].append({"source": "workspace", "status": status, "count": len(local_results)})
    user_root = (Path.home() / "wiki").resolve()
    if len(results) < limit and user_root.is_dir():
        user_results, status = bounded_canonical_retrieval(user_root, "user", True, tokens, limit - len(results), timeout)
        results.extend(user_results)
        result["diagnostics"].append({"source": "user", "status": status, "count": len(user_results)})
    if len(results) < limit:
        memory = mnemosyne_recall(gate["query"], min(2, limit - len(results)), timeout)
        result["diagnostics"].append({"source": "mnemosyne", **{key: value for key, value in memory.items() if key != "results" and key != "stdout"}})
        for item in memory.get("results", []):
            overlap = sum(token in item["content"].lower() for token in tokens)
            if item.get("score", 0) < 0.2 and not overlap:
                continue
            results.append({
                "source": "mnemosyne",
                "path": None,
                "title": "Private continuity hint",
                "snippet": item["content"],
                "canonical_uri": None,
                "score": item.get("score", 0),
                "confidence": "hint",
            })
    result["results"] = trim_retrieval_results(results, limit, max_bytes)
    result["status"] = "ok" if result["results"] else "no-result"
    if not result["results"]:
        result["reason"] = "no-relevant-canonical-result"
    return result


def capture_resolve(cwd: str):
    data = load()
    path = Path(cwd).resolve()
    local_root = next((candidate / ".wiki" for candidate in (path, *path.parents) if (candidate / ".wiki").is_dir()), None)
    matches = [(Path(workspace), topic) for workspace, topic in data["workspace_topics"].items() if path.is_relative_to(Path(workspace))]
    if matches:
        topic = max(matches, key=lambda item: len(item[0].parts))[1]
    else:
        names = {candidate.name.lower().replace("_", "-") for candidate in (path, *path.parents)}
        candidates = sorted(topic for topic, metadata in data["topics"].items() if {topic, *metadata["aliases"]} & names)
        topic = candidates[0] if len(candidates) == 1 else None
    workspace_root = local_root.parent if local_root else (max(matches, key=lambda item: len(item[0].parts))[0] if matches else None)
    return json.dumps({"cwd": str(path), "topic": topic, "local_wiki": str(local_root) if local_root else None, "local_wiki_status": wiki_status(local_root), "workspace_root": str(workspace_root) if workspace_root else None})


def map_workspace(cwd: str, topic: str):
    if not SLUG.fullmatch(topic):
        raise SystemExit("topic must be a lowercase slug")
    data = load()
    workspace = str(Path(cwd).resolve())
    data["workspace_topics"][workspace] = topic
    metadata = data["topics"].setdefault(topic, {"workspace_roots": [], "aliases": []})
    if workspace not in metadata["workspace_roots"]:
        metadata["workspace_roots"].append(workspace)
    alias = Path(workspace).name.lower().replace("_", "-")
    if SLUG.fullmatch(alias) and alias not in metadata["aliases"]:
        metadata["aliases"].append(alias)
    save(data)
    print("mapped")


def unmap_workspace(cwd: str, confirm: bool):
    if not confirm:
        raise SystemExit("unmap requires --confirm")
    data = load()
    workspace = str(Path(cwd).resolve())
    if workspace not in data["workspace_topics"]:
        raise SystemExit("workspace is not mapped")
    topic = data["workspace_topics"].pop(workspace)
    metadata = data["topics"].get(topic)
    if metadata:
        metadata["workspace_roots"] = [root for root in metadata["workspace_roots"] if root != workspace]
    save(data)
    print("unmapped")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    resolve_parser = commands.add_parser("resolve")
    resolve_parser.add_argument("--cwd", default=".")
    map_parser = commands.add_parser("map")
    map_parser.add_argument("--cwd", default=".")
    map_parser.add_argument("--topic", required=True)
    unmap_parser = commands.add_parser("unmap")
    unmap_parser.add_argument("--cwd", default=".")
    unmap_parser.add_argument("--confirm", action="store_true")
    capture_parser = commands.add_parser("capture")
    capture_parser.add_argument("--cwd", default=".")
    capture_parser.add_argument("--scope", choices=["auto", "workspace", "user", "personal", "uncertain"], default="auto")
    capture_parser.add_argument("--outcome", required=True)
    capture_parser.add_argument("--kind", choices=["result", "decision", "research", "plan"], default="result")
    capture_parser.add_argument("--artifact", action="append", default=[])
    capture_parser.add_argument("--decision", action="append", default=[])
    capture_parser.add_argument("--verification", action="append", default=[])
    capture_parser.add_argument("--source", action="append", default=[])
    capture_parser.add_argument("--confidence", choices=["low", "medium", "high", "unverified"], default="unverified")
    capture_parser.add_argument("--open-question", action="append", default=[])
    canonicalize_parser = commands.add_parser("canonicalize")
    canonicalize_parser.add_argument("--cwd", default=".")
    canonicalize_parser.add_argument("--source", required=True)
    canonicalize_parser.add_argument("--source-url", required=True)
    canonicalize_parser.add_argument("--title", required=True)
    canonicalize_parser.add_argument("--receipt", "--receipt-id", dest="receipt_id")
    transition_parser = commands.add_parser("transition")
    transition_parser.add_argument("--cwd", default=".")
    transition_parser.add_argument("--record", required=True)
    transition_parser.add_argument("--status", choices=["canonical", "superseded", "retracted"], required=True)
    transition_parser.add_argument("--supersedes")
    retrieve_parser = commands.add_parser("retrieve")
    retrieve_parser.add_argument("--cwd", default=".")
    retrieve_parser.add_argument("--prompt", required=True)
    retrieve_parser.add_argument("--limit", type=int, default=RETRIEVAL_MAX_RESULTS)
    retrieve_parser.add_argument("--max-bytes", type=int, default=RETRIEVAL_MAX_BYTES)
    retrieve_parser.add_argument("--timeout", type=float, default=MNEMOSYNE_TIMEOUT_SECONDS)
    commands.add_parser("validate")
    args = parser.parse_args()
    if args.command == "validate":
        load()
        print("valid")
    elif args.command == "map":
        map_workspace(args.cwd, args.topic)
    elif args.command == "unmap":
        unmap_workspace(args.cwd, args.confirm)
    elif args.command == "capture":
        print(json.dumps(capture(
            args.cwd,
            args.outcome,
            args.kind,
            args.artifact,
            args.decision,
            args.verification,
            args.source,
            args.confidence,
            args.open_question,
            args.scope,
        )))
    elif args.command == "canonicalize":
        canonicalize(args.cwd, args.source, args.source_url, args.title, args.receipt_id)
    elif args.command == "transition":
        transition_record(args.cwd, args.record, args.status, args.supersedes)
    elif args.command == "retrieve":
        if not 1 <= args.limit <= RETRIEVAL_MAX_RESULTS or not 512 <= args.max_bytes <= RETRIEVAL_MAX_BYTES or not 0.1 <= args.timeout <= 10:
            raise SystemExit("retrieval bounds are invalid")
        print(json.dumps(retrieve_memory(args.cwd, args.prompt, args.limit, args.max_bytes, args.timeout), ensure_ascii=False))
    else:
        resolve(args.cwd)


if __name__ == "__main__":
    main()
