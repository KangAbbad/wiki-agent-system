#!/usr/bin/env python3
"""Shared rendering rules for canonical Wiki evidence sources."""

from __future__ import annotations

import json
import re
from pathlib import Path


SHA256 = re.compile(r"^[0-9a-f]{64}$")
PROVENANCE_CLASSES = frozenset({"caption", "web-extraction", "metadata", "machine-transcription", "none"})
RESERVED_FIELDS = {"title", "source", "type", "ingested", "tags", "summary"}
UNQUOTED_FIELDS = {
    "schema",
    "retrieval_method",
    "retrieved",
    "content_sha256",
    "transcript_sha256",
    "provenance_class",
    "evidence_status",
    "knowledge_readiness",
    "evidence_eligible",
    "transcript_eligible",
    "receipt_id",
    "scope",
    "status",
    "valid_from",
    "valid_until",
}


def source_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug[:80] or "source"


def canonical_source_path(raw_root, title: str, digest: str, suffix: str = "") -> Path:
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise ValueError("canonical evidence content hash is invalid")
    if suffix and (not isinstance(suffix, str) or not re.fullmatch(r"-[0-9a-f]{1,16}", suffix)):
        raise ValueError("canonical evidence path suffix is invalid")
    return Path(raw_root) / "articles" / f"{source_slug(title)}-{digest[:12]}{suffix}.md"


def canonical_summary(provenance_class: str, evidence_status: str) -> str:
    if provenance_class not in PROVENANCE_CLASSES:
        raise ValueError("canonical evidence provenance is invalid")
    if not isinstance(evidence_status, str) or not re.fullmatch(r"[a-z-]{1,32}", evidence_status):
        raise ValueError("canonical evidence lifecycle is invalid")
    return f"Wiki Preflight {provenance_class} evidence; lifecycle={evidence_status}."


def _yaml_value(value, key: str | None = None) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, (list, tuple)):
        return json.dumps(list(value), ensure_ascii=False)
    text = str(value)
    if "\n" in text or "\r" in text:
        raise ValueError("canonical evidence frontmatter value is multiline")
    if key in UNQUOTED_FIELDS and re.fullmatch(r"[A-Za-z0-9+:.TZ_/-]+", text):
        return text
    return json.dumps(text, ensure_ascii=False)


def render_canonical_source(
    *,
    title: str,
    source: str,
    ingested: str,
    provenance_class: str,
    evidence_status: str,
    body: str,
    fields=(),
) -> str:
    """Render one LLM Wiki raw/articles record with additive evidence fields."""
    if not isinstance(title, str) or not title.strip() or "\n" in title or "\r" in title:
        raise ValueError("canonical evidence title is invalid")
    if not isinstance(source, str) or not source.strip() or "\n" in source or "\r" in source:
        raise ValueError("canonical evidence source is invalid")
    if not isinstance(ingested, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", ingested):
        raise ValueError("canonical evidence ingestion date is invalid")
    if not isinstance(body, str):
        raise ValueError("canonical evidence body is invalid")
    tags = ["wiki-preflight", "evidence", provenance_class]
    if provenance_class not in PROVENANCE_CLASSES:
        raise ValueError("canonical evidence provenance is invalid")
    lines = [
        "---",
        f"title: {_yaml_value(title.strip())}",
        f"source: {_yaml_value(source.strip())}",
        "type: articles",
        f"ingested: {ingested}",
        f"tags: [{', '.join(tags)}]",
        f"summary: {_yaml_value(canonical_summary(provenance_class, evidence_status))}",
    ]
    seen = set(RESERVED_FIELDS)
    for key, value in fields:
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", key) or key in seen:
            raise ValueError("canonical evidence additive field is invalid")
        lines.append(f"{key}: {_yaml_value(value, key)}")
        seen.add(key)
    return "\n".join(lines) + "\n---\n" + body
