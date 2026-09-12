#!/usr/bin/env python3
"""Validate and resolve user-owned ambient LLM Wiki routing."""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


CONFIG = Path(__file__).resolve().parents[1] / "defaults" / "ambient.json"
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SENSITIVE = re.compile(
    r"((?:api[_ -]?key|password|secret|token|private[_ -]?key|authorization)\s*[:=])[^\r\n]*",
    re.I,
)


def load():
    try:
        data = json.loads(CONFIG.read_text())
    except FileNotFoundError:
        raise SystemExit(f"missing config: {CONFIG}")
    except json.JSONDecodeError as error:
        raise SystemExit(f"invalid JSON in {CONFIG}: {error}")
    if data.get("schema_version") == 1:
        topics = {}
        for workspace, topic in data.get("workspace_topics", {}).items():
            topics.setdefault(topic, {"workspace_roots": [], "aliases": []})["workspace_roots"].append(workspace)
        data["schema_version"] = 2
        data["topics"] = topics
    if data.get("schema_version") != 2 or not isinstance(data.get("workspace_topics"), dict) or not isinstance(data.get("topics"), dict):
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
    return data


def save(data):
    CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=CONFIG.parent, delete=False) as file:
        json.dump(data, file, indent=2)
        file.write("\n")
        temporary = file.name
    os.chmod(temporary, 0o600)
    os.replace(temporary, CONFIG)


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
):
    data = load()
    if not data.get("auto_capture"):
        raise SystemExit("auto capture is disabled")
    outcome = redact(outcome)
    if not outcome:
        raise SystemExit("capture outcome is empty")
    route = json.loads(capture_resolve(cwd))
    workspace = Path(route["cwd"])
    safe_artifacts = safe_artifact_paths(artifacts)
    if route["local_wiki"] and route["local_wiki_status"] == "valid":
        destination = Path(route["local_wiki"]) / "inbox" / "autosave"
    elif route["topic"] and (Path.home() / "wiki" / "topics" / route["topic"]).is_dir():
        destination = Path.home() / "wiki" / "topics" / route["topic"] / "inbox" / "autosave"
    else:
        destination = Path.home() / "wiki" / ".sessions" / "autosave"
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    captured = datetime.now(timezone.utc)
    stamp = captured.strftime("%Y%m%dT%H%M%SZ")
    output = destination / f"{stamp}-{uuid.uuid4().hex[:8]}-{kind}.md"
    content = f"""---
type: autosave-capture
schema: 1
status: pending-curation
kind: {kind}
workspace: {workspace}
topic: {route['topic'] or 'unresolved'}
captured: {captured.isoformat()}
confidence: {confidence}
---

# Auto-saved work capture

## Outcome

{outcome}

## Decisions

{lines(decisions)}

## Artifacts

{lines([f'`{artifact}`' for artifact in safe_artifacts])}

## Verification

{lines(verifications)}

## Sources

{lines(sources)}

## Open questions

{lines(open_questions)}
"""
    atomic_write(output, content)
    print(json.dumps({"path": str(output), "topic": route["topic"], "status": "pending-curation"}))


def source_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "source"


def canonicalize(cwd: str, source: str, source_url: str, title: str):
    """Promote only supplied, attributable, non-sensitive source material to raw/."""
    load()
    route = json.loads(capture_resolve(cwd))
    if route["local_wiki_status"] != "valid":
        raise SystemExit("canonicalization requires a valid local LLM Wiki")
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SystemExit("source URL must be an absolute http(s) URL")
    clean_title = title.strip()
    if not clean_title or "\n" in clean_title or SENSITIVE.search(clean_title):
        raise SystemExit("source title is empty, multiline, or sensitive")
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise SystemExit("source must be a readable file")
    if source_path.name.startswith(".env"):
        raise SystemExit("environment files cannot be canonicalized")
    if source_path.stat().st_size > 2 * 1024 * 1024:
        raise SystemExit("source exceeds 2 MiB; store a source reference instead")
    try:
        body = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise SystemExit("source must be UTF-8 text")
    if not body.strip() or SENSITIVE.search(body):
        raise SystemExit("source is empty or contains sensitive material")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    destination = Path(route["local_wiki"]) / "raw"
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    output = destination / f"{source_slug(clean_title)}-{digest[:12]}.md"
    if not output.exists():
        retrieved = datetime.now(timezone.utc).date().isoformat()
        content = f"""---
type: raw-source
title: {clean_title}
source_url: {source_url}
retrieved: {retrieved}
content_sha256: {digest}
---

# {clean_title}

{body.rstrip()}
"""
        atomic_write(output, content)
    print(json.dumps({"path": str(output), "status": "canonical-evidence", "source_url": source_url, "content_sha256": digest}))


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
    return json.dumps({"cwd": str(path), "topic": topic, "local_wiki": str(local_root) if local_root else None, "local_wiki_status": wiki_status(local_root)})


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
        capture(
            args.cwd,
            args.outcome,
            args.kind,
            args.artifact,
            args.decision,
            args.verification,
            args.source,
            args.confidence,
            args.open_question,
        )
    elif args.command == "canonicalize":
        canonicalize(args.cwd, args.source, args.source_url, args.title)
    else:
        resolve(args.cwd)


if __name__ == "__main__":
    main()
