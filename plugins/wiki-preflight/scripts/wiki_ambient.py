#!/usr/bin/env python3
"""Validate and resolve user-owned ambient LLM Wiki routing."""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


CONFIG = Path(__file__).resolve().parents[1] / "defaults" / "ambient.json"
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SENSITIVE = re.compile(r"(?:api[_ -]?key|password|secret|token|private[_ -]?key|authorization)\s*[:=]", re.I)


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


def capture(cwd: str, summary: str, kind: str, artifacts: list[str]):
    data = load()
    if not data.get("auto_capture"):
        raise SystemExit("auto capture is disabled")
    if not summary.strip() or SENSITIVE.search(summary):
        raise SystemExit("capture summary is empty or sensitive")
    route = json.loads(capture_resolve(cwd))
    workspace = Path(route["cwd"])
    safe_artifacts = []
    for artifact in artifacts:
        candidate = Path(artifact)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise SystemExit("artifacts must be workspace-relative paths")
        safe_artifacts.append(candidate.as_posix())
    if route["local_wiki"] and route["local_wiki_status"] == "valid":
        destination = Path(route["local_wiki"]) / "inbox" / "autosave"
    elif route["topic"] and (Path.home() / "wiki" / "topics" / route["topic"]).is_dir():
        destination = Path.home() / "wiki" / "topics" / route["topic"] / "inbox" / "autosave"
    else:
        destination = Path.home() / "wiki" / ".sessions" / "autosave"
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = destination / f"{stamp}-{kind}.md"
    artifact_lines = "\n".join(f"- `{artifact}`" for artifact in safe_artifacts) or "- None"
    output.write_text(f"---\ntype: autosave-capture\nstatus: pending-curation\nkind: {kind}\nworkspace: {workspace}\ntopic: {route['topic'] or 'unresolved'}\ncaptured: {datetime.now(timezone.utc).date()}\n---\n\n# Auto-saved work capture\n\n{summary.strip()}\n\n## Artifacts\n\n{artifact_lines}\n")
    os.chmod(output, 0o600)
    print(json.dumps({"path": str(output), "topic": route["topic"], "status": "pending-curation"}))


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
    capture_parser.add_argument("--summary", required=True)
    capture_parser.add_argument("--kind", choices=["result", "decision", "research", "plan"], default="result")
    capture_parser.add_argument("--artifact", action="append", default=[])
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
        capture(args.cwd, args.summary, args.kind, args.artifact)
    else:
        resolve(args.cwd)


if __name__ == "__main__":
    main()
