#!/usr/bin/env python3
"""Bounded, public-source evidence verification for Wiki Preflight."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
import re
import socket
import sys
import tempfile
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
from youtube_fallback import (  # noqa: E402
    PROVENANCE_CLASSES,
    YOUTUBE_HOSTS,
    acquire_file_lock,
    release_file_lock,
    safe_text,
)
from canonical_evidence import canonical_source_path, render_canonical_source


SCHEMA_VERSION = 1
QUEUE_DIRNAME = "evidence-verification"
QUEUE_ID = re.compile(r"^[0-9a-f]{32}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_STATES = frozenset({"acquired", "unverified", "verifying", "verified", "exhausted", "blocked"})
QUEUE_STATES = frozenset({"pending", "running", "verified", "exhausted", "blocked"})
MAX_CLAIM_CHARS = 400
MAX_RESULT_CHARS = 400
MAX_SOURCE_BYTES = 512 * 1024
MAX_ATTEMPTS = 3
MAX_QUEUE_ITEMS = 256
MAX_SCHEDULED_ITEMS = 2
MAX_RETRIES = 3
MAX_DISCOVERY_RESULTS = 3
MIN_TIMEOUT = 1.0
MAX_TIMEOUT = 15.0
RETRY_DELAY_SECONDS = 30
DISCOVERY_ENDPOINT = "https://html.duckduckgo.com/html/"
DISCOVERY_HOSTS = frozenset({"html.duckduckgo.com", "duckduckgo.com"})
SENSITIVE = re.compile(
    r"((?:api[_ -]?key|password|secret|token|private[_ -]?key|authorization|cookie)\s*[:=])[^\r\n]*",
    re.I,
)
PUBLIC_GAP = re.compile(
    r"\b(?:authorit(?:y|ative)|database|vendor|provenance|spec(?:ification)?|reference|docs?|research|investigat\w*|lookup|source|citation|cite|verify|validation|check|audit|verifikasi|validasi|cek|uji)\b",
    re.I,
)
AUTHORITY_BINDING = re.compile(
    r"\b(?:authority[_ -]?host|trusted[_ -]?authority)\s*[:=]\s*([a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?)\b",
    re.I,
)
CLAIM_TOKEN = re.compile(r"[a-z][a-z0-9-]{2,}", re.I)
CLAIM_STOPWORDS = frozenset({
    "about", "according", "agent", "against", "an", "and", "are", "authoritative", "authority", "based",
    "be", "bisa", "can", "check", "cite", "citation", "claim", "claims", "dan", "dapat", "database",
    "dengan", "docs", "documentation", "dokumen", "dokumentasi", "do", "does", "find", "for", "from",
    "give", "how", "in", "ini", "is", "itu", "lookup", "official", "on", "or", "page", "pada", "please",
    "public", "reference", "research", "secara", "source", "spec", "specification", "tell", "the", "that",
    "this", "to", "untuk", "using", "verify", "verification", "verified", "what", "whether", "with",
    "without", "yang",
})
NEGATION_CUES = re.compile(
    r"\b(?:not|no|never|neither|nor|false|untrue|incorrect|unsupported|deny(?:s|ied)?|refute(?:s|d)?|"
    r"disprove(?:s|d)?|debunk(?:s|ed)?|contrary|without)\b|\bnot\s+(?:true|the\s+case|a\s+fact|supported|confirmed)\b",
    re.I,
)
UNCERTAINTY_CUES = re.compile(
    r"\b(?:unclear|unknown|unverified|unconfirmed|alleged|allegedly|rumou?r|speculative|"
    r"possibly|perhaps|maybe|might|may|could|would|should|questionable|disputed|if|unless|whether|assuming)\b|\?",
    re.I,
)
MATERIAL_QUALIFIER_CUES = re.compile(
    r"\b(?:hypothetical(?:ly)?|fictional|fiction|imaginary|illustrative|counterfactual|"
    r"thought[- ]experiment|deprecated|obsolete|legacy|historical(?:ly)?|outdated|"
    r"superseded|former(?:ly)?|previous(?:ly)?|prior|draft|proposed|experimental|"
    r"alpha|beta|sample|example|scenario|toy|mock|demo|sandbox|unreleased|future|planned|"
    r"development|staging|test(?:ing)?|version|ver\.?|scope|scoped|environment|context|"
    r"no\s+longer|not\s+current|as\s+of|at\s+the\s+time|since|until|before|after)\b"
    r"|\bv\d+(?:\.\d+)+(?:[-+][a-z0-9.-]+)?\b"
    r"|\b(?:only|limited|applies)\s+(?:in|for|on|under|within|to|this)\b"
    r"|\b(?:postgresql|mysql|mariadb|sqlite|oracle|mongodb|python|node(?:\.js)?)\s+\d+(?:\.\d+)+\b",
    re.I,
)
SUPPORT_CUES = re.compile(
    r"\b(?:support(?:s|ed)?|allow(?:s|ed)?|permit(?:s|ted)?|provide(?:s|d)?|enable(?:s|d)?|"
    r"include(?:s|d)?|have|has|is|are|was|were|can|does|do)\b",
    re.I,
)
BOUNDARY_PATTERNS = (
    (re.compile(r"\b(?:credential|password|api[_ -]?key|token|cookie|login|private source)\b", re.I), "required private credential or source"),
    (re.compile(r"\b(?:paywall|access[- ]?control|permission|private|internal[- ]only)\b", re.I), "access-control boundary"),
    (re.compile(r"(?=.*\b(?:production|prod|live)\b)(?=.*\b(?:drop|delete|truncate|alter|write|mutate|rollback)\b)", re.I), "destructive production verification"),
    (re.compile(r"\b(?:approve|authority|authorize|legal|compliance)\b", re.I), "explicit authority decision"),
)
AUTHORITY_REASONS = frozenset({
    "required private credential or source",
    "access-control boundary",
    "destructive production verification",
    "explicit authority decision",
})


def eligibility(provenance_class, evidence_status):
    if provenance_class not in PROVENANCE_CLASSES or evidence_status not in EVIDENCE_STATES:
        raise VerificationError("evidence provenance or lifecycle is invalid")
    if provenance_class == "caption":
        return evidence_status == "verified", evidence_status == "verified"
    if provenance_class in {"web-extraction", "metadata"}:
        return evidence_status in {"acquired", "unverified", "verifying", "verified"}, False
    return False, False


class VerificationError(ValueError):
    """A verification record cannot be safely read or written."""


class FutureSchema(VerificationError):
    """A newer record must remain read-only."""


class TitleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.parts.append(data)


class SearchLinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.links.append(value)


class EvidenceTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "template"}:
            self._ignored += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "template"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data):
        if not self._ignored:
            self.parts.append(data)


class PublicRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        canonical_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def redact(value, limit=MAX_RESULT_CHARS):
    return re.sub(r"\s+", " ", SENSITIVE.sub(lambda match: f"{match.group(1)} [REDACTED]", str(value or ""))).strip()[:limit]


def valid_local_wiki(wiki):
    wiki = Path(wiki).expanduser()
    return (
        wiki.is_dir()
        and not wiki.is_symlink()
        and all((wiki / name).is_dir() for name in ("raw", "wiki"))
        and (wiki / "config.md").is_file()
        and (wiki / "_index.md").is_file()
    )


def _public_host(host):
    host = (host or "").lower().rstrip(".")
    if not host or host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise VerificationError("source host is not public")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast or address.is_unspecified):
        raise VerificationError("source host is not public")
    try:
        resolved = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        resolved = []
    for result in resolved:
        try:
            address = ipaddress.ip_address(result[4][0])
        except (IndexError, ValueError):
            continue
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast or address.is_unspecified:
            raise VerificationError("source host resolves to a non-public address")
    return host


def canonical_public_url(value):
    if not isinstance(value, str):
        raise VerificationError("source URL must be a string")
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or parsed.username or parsed.password or not parsed.hostname:
        raise VerificationError("source URL must be an absolute public HTTP(S) URL")
    try:
        port = parsed.port
    except ValueError as error:
        raise VerificationError("source URL port is invalid") from error
    host = _public_host(parsed.hostname)
    netloc = host
    if port is not None and not ((parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)):
        netloc = f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))


def authority_host(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise VerificationError("authority host is invalid")
    host = value.lower().strip().rstrip(".")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host):
        raise VerificationError("authority host is invalid")
    return _public_host(host)


def host_matches(url, expected):
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return bool(expected) and (host == expected or host.endswith(f".{expected}"))


def normalize_claim(value):
    value = redact(value, MAX_CLAIM_CHARS)
    if not value:
        raise VerificationError("claim is empty")
    return value


def explicit_authority(value):
    match = AUTHORITY_BINDING.search(value or "")
    if not match:
        return None
    try:
        return authority_host(match.group(1))
    except VerificationError:
        return None


def claim_terms(claim):
    searchable = re.sub(r"https?://[^\s<>'\"]+", " ", claim or "")
    searchable = AUTHORITY_BINDING.sub(" ", searchable)
    terms = []
    for token in CLAIM_TOKEN.findall(searchable.lower()):
        if token in CLAIM_STOPWORDS or token in terms:
            continue
        terms.append(token)
    return terms[:12]


def evidence_sentences(body):
    parser = EvidenceTextParser()
    try:
        parser.feed(body[:MAX_SOURCE_BYTES])
        text = " ".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", body[:MAX_SOURCE_BYTES])
    text = re.sub(r"\s+", " ", text).strip()
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+|\n+", text) if sentence.strip()]


def _contains_term(term, text):
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text))


def claim_supported(claim, body):
    # ponytail: deterministic sentence-level entailment gate; fail closed on
    # negation, uncertainty, or material qualifiers until a vetted NLI
    # dependency is justified.
    if any(pattern.search(claim or "") for pattern in (
        NEGATION_CUES,
        UNCERTAINTY_CUES,
        MATERIAL_QUALIFIER_CUES,
    )):
        return False, []
    terms = claim_terms(claim)
    if len(terms) < 2 or not isinstance(body, str):
        return False, []
    matched = []
    supported = False
    sentences = evidence_sentences(body)
    risky = {
        index for index, sentence in enumerate(sentences)
        if any(pattern.search(sentence) for pattern in (
            NEGATION_CUES,
            UNCERTAINTY_CUES,
            MATERIAL_QUALIFIER_CUES,
        ))
    }
    for index, sentence in enumerate(sentences):
        plain = sentence.lower()
        sentence_matches = [term for term in terms if _contains_term(term, plain)]
        if len(sentence_matches) != len(terms):
            continue
        matched = sentence_matches
        if any(abs(index - risk_index) <= 1 for risk_index in risky):
            return False, matched
        if SUPPORT_CUES.search(plain):
            supported = True
    return supported, matched


def discovery_query(claim):
    query = re.sub(r"https?://[^\s<>'\"]+", " ", claim or "")
    query = AUTHORITY_BINDING.sub(" ", query)
    return redact(query, 240)


def claim_id_for(claim, source_url, expected_authority):
    payload = f"{claim.lower()}\0{source_url or ''}\0{expected_authority or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def boundary_reason(claim):
    for pattern, reason in BOUNDARY_PATTERNS:
        if pattern.search(claim):
            return reason
    return None


def queue_directory(wiki):
    requested = Path(wiki).expanduser()
    if requested.is_symlink():
        raise VerificationError("verification Wiki path is a symlink")
    wiki = requested.resolve()
    if not valid_local_wiki(wiki):
        raise VerificationError("verification requires a valid local LLM Wiki")
    current = wiki
    for name in (".sessions", "wiki-agent-system", QUEUE_DIRNAME):
        current = current / name
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise VerificationError("verification state path is not a private local directory")
        current.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(current, 0o700)
    return current


def queue_path(wiki, queue_id):
    if not QUEUE_ID.fullmatch(queue_id):
        raise VerificationError("invalid verification queue id")
    return queue_directory(wiki) / f"{queue_id}.json"


def lock_path(wiki, queue_id):
    return queue_path(wiki, queue_id).with_suffix(".lock")


def atomic_json(path, data):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise VerificationError("verification state path is a symlink")
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".verification-", text=True)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, ensure_ascii=False, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _timestamp(value, required=True):
    return value is None and not required or isinstance(value, str) and 1 <= len(value) <= 40 and "\n" not in value and "\r" not in value


def _frontmatter(content):
    if not content.startswith("---\n"):
        return {}
    marker = re.search(r"(?m)^---\s*$", content[4:])
    if not marker:
        return {}
    fields = {}
    for line in content[4 : 4 + marker.start()].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value == "null":
            fields[key.strip()] = None
        elif value.startswith('"'):
            try:
                fields[key.strip()] = json.loads(value)
            except json.JSONDecodeError:
                return {}
        else:
            fields[key.strip()] = value
    return fields


def _source_body(content):
    if not content.startswith("---\n"):
        return content
    marker = re.search(r"(?m)^---\s*$", content[4:])
    return content[4 + marker.end():] if marker else content


def _update_frontmatter(content, updates):
    marker = re.search(r"(?m)^---\s*$", content[4:]) if content.startswith("---\n") else None
    if not marker:
        raise VerificationError("evidence frontmatter is invalid")
    close = 4 + marker.start()
    lines = content[4:close].splitlines()
    replaced = set()
    for index, line in enumerate(lines):
        key = line.split(":", 1)[0].strip() if ":" in line else ""
        if key not in updates:
            continue
        value = updates[key]
        literal = "null" if value is None else json.dumps(value) if key in {"evidence_uri", "source_url", "source_path"} else str(value)
        lines[index] = f"{key}: {literal}"
        replaced.add(key)
    for key, value in updates.items():
        if key in replaced:
            continue
        literal = "null" if value is None else json.dumps(value) if key in {"evidence_uri", "source_url", "source_path"} else str(value)
        lines.append(f"{key}: {literal}")
    return "---\n" + "\n".join(lines) + "\n" + content[close:]


def _safe_path(wiki, path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise VerificationError("evidence source path is missing or a symlink")
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(Path(wiki).resolve()):
            raise VerificationError("evidence source path leaves the Wiki")
    except OSError as error:
        raise VerificationError("evidence source path is unreadable") from error
    return resolved


def existing_verified_source(wiki, source_url, expected_authority, claim):
    if not source_url or not expected_authority:
        return None
    root = Path(wiki) / "raw"
    if root.is_symlink() or not root.is_dir():
        return None
    for path in list(root.rglob("*.md"))[:512]:  # ponytail: bounded scan; use raw index when this grows materially.
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES + 8192:
                continue
            content = path.read_text(encoding="utf-8")
            fields = _frontmatter(content)
        except (OSError, UnicodeError):
            continue
        if fields.get("source_url") != source_url or fields.get("evidence_status") != "verified" or fields.get("provenance_class") != "web-extraction":
            continue
        if fields.get("verification_authority") != expected_authority or not host_matches(source_url, expected_authority):
            continue
        supported, _ = claim_supported(claim, _source_body(content))
        if supported:
            return path
    return None


def _source_title(body, url):
    parser = TitleParser()
    try:
        parser.feed(body[:8192])
    except Exception:
        pass
    title = redact(" ".join(parser.parts), 160)
    return title or (urlsplit(url).hostname or "Public source")


def persist_web_source(wiki, source_url, body, retrieved_at, *, evidence_status="unverified", verification_authority=None, content_sha256=None):
    if evidence_status not in {"unverified", "verified"}:
        raise VerificationError("web source status is invalid")
    source_url = canonical_public_url(source_url)
    if not isinstance(body, str):
        raise VerificationError("web source must be text")
    if not _timestamp(retrieved_at, required=False):
        raise VerificationError("web source retrieval time is invalid")
    authority = authority_host(verification_authority)
    if evidence_status == "verified" and not authority:
        raise VerificationError("verified web source requires an authority host")
    body_bytes = body.encode("utf-8")
    if not body.strip() or len(body_bytes) > MAX_SOURCE_BYTES or SENSITIVE.search(body):
        raise VerificationError("web source is empty, sensitive, or over the size limit")
    digest = hashlib.sha256(body_bytes).hexdigest()
    if content_sha256 is not None and content_sha256 != digest:
        raise VerificationError("web source hash is invalid")
    raw = Path(wiki) / "raw"
    if raw.is_symlink() or not raw.is_dir():
        raise VerificationError("raw evidence directory is unavailable")
    articles = raw / "articles"
    if articles.is_symlink() or (articles.exists() and not articles.is_dir()):
        raise VerificationError("raw articles directory is unavailable")
    articles.mkdir(mode=0o700, parents=True, exist_ok=True)
    title = _source_title(body, source_url)
    suffix = ""
    output = canonical_source_path(raw, title, digest)
    while output.exists():
        if output.is_symlink():
            raise VerificationError("existing web evidence is a symlink")
        try:
            existing = output.read_text(encoding="utf-8")
            fields = _frontmatter(existing)
        except (OSError, UnicodeError) as error:
            raise VerificationError("existing web evidence is unreadable") from error
        if fields.get("schema") not in {"1", 1} or fields.get("content_sha256") != digest:
            raise FutureSchema("existing web evidence is newer or malformed")
        if fields.get("source_url") == source_url and fields.get("provenance_class") == "web-extraction":
            return output
        if suffix:
            raise FutureSchema("duplicate web evidence destination")
        suffix = f"-{hashlib.sha256(source_url.encode('utf-8')).hexdigest()[:8]}"
        output = canonical_source_path(raw, title, digest, suffix)
    retrieved_at = retrieved_at or now_iso()
    evidence_uri = f"wiki://workspace/evidence/{digest}"
    canonical_uri = evidence_uri if not suffix else f"{evidence_uri}{suffix}"
    content = render_canonical_source(
        title=title,
        source=source_url,
        ingested=retrieved_at[:10],
        provenance_class="web-extraction",
        evidence_status=evidence_status,
        body=f"\n{body.rstrip()}\n",
        fields=(
            ("schema", 1),
            ("source_url", source_url),
            ("retrieval_method", "public-http"),
            ("retrieved_at", retrieved_at),
            ("retrieved", retrieved_at[:10]),
            ("content_sha256", digest),
            ("provenance_class", "web-extraction"),
            ("evidence_status", evidence_status),
            ("evidence_eligible", True),
            ("transcript_eligible", False),
            ("verification_authority", authority),
            ("verification_method", "public-http-authority-and-claim-match" if evidence_status == "verified" else "none"),
            ("evidence_uri", evidence_uri),
            ("canonical_uri", canonical_uri),
            ("scope", "workspace"),
            ("status", "canonical"),
            ("supersedes", None),
            ("valid_from", retrieved_at),
            ("valid_until", None),
        ),
    )
    descriptor, temporary = tempfile.mkstemp(dir=raw, prefix=".web-evidence-", text=True)
    try:
        with os.fdopen(descriptor, "w") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return output


def mark_source_verified(path, authority):
    authority = authority_host(authority)
    if not authority:
        raise VerificationError("verified web source requires an authority host")
    path = Path(path)
    wiki = path.parent.parent.resolve()
    path = _safe_path(wiki, path)
    content = path.read_text(encoding="utf-8")
    fields = _frontmatter(content)
    schema = fields.get("schema")
    if schema not in {"1", 1}:
        if isinstance(schema, int) and schema > SCHEMA_VERSION:
            raise FutureSchema("web evidence schema is newer than this runtime")
        raise VerificationError("web evidence schema is invalid")
    if fields.get("provenance_class") != "web-extraction":
        raise VerificationError("only web extraction can be verified by this queue")
    if fields.get("evidence_status") == "verified":
        return path
    updated = _update_frontmatter(
        content,
        {
            "evidence_status": "verified",
            "verification_authority": authority,
            "verification_method": "public-http-authority-and-claim-match",
            "verified_at": now_iso(),
        },
    )
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".web-evidence-", text=True)
    try:
        with os.fdopen(descriptor, "w") as file:
            file.write(updated)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def fetch_public_source(source_url, timeout):
    if timeout <= 0:
        return {"kind": "retry", "error_class": "deadline-exhausted"}
    request = Request(
        source_url,
        headers={
            "Accept": "text/html,text/plain,application/json,application/xml;q=0.9",
            "User-Agent": "Wiki-Preflight/1",
        },
    )
    try:
        with build_opener(PublicRedirectHandler).open(request, timeout=max(MIN_TIMEOUT, min(timeout, MAX_TIMEOUT))) as response:
            final_url = canonical_public_url(response.geturl())
            status = int(getattr(response, "status", response.getcode()))
            if status < 200 or status >= 300:
                return {"kind": "retry" if status >= 500 else "exhausted", "status_code": status, "error_class": f"http-{status}"}
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "text/plain", "application/json", "application/xml", "text/xml"}:
                return {"kind": "exhausted", "status_code": status, "error_class": "unsupported-content-type"}
            data = response.read(MAX_SOURCE_BYTES + 1)
            if len(data) > MAX_SOURCE_BYTES:
                return {"kind": "exhausted", "status_code": status, "error_class": "source-too-large"}
            body = data.decode(response.headers.get_content_charset() or "utf-8", errors="strict")
            if not body.strip():
                return {"kind": "exhausted", "status_code": status, "error_class": "empty-source"}
            if SENSITIVE.search(body):
                return {"kind": "blocked", "status_code": status, "error_class": "sensitive-source"}
            return {"kind": "success", "status_code": status, "body": body, "final_url": final_url}
    except HTTPError as error:
        status = int(error.code)
        return {"kind": "blocked" if status in {401, 403} else "retry" if status >= 500 else "exhausted", "status_code": status, "error_class": f"http-{status}"}
    except (TimeoutError, socket.timeout):
        return {"kind": "retry", "error_class": "timeout"}
    except VerificationError as error:
        return {"kind": "blocked", "error_class": redact(str(error), 80) or "public-source-boundary"}
    except (URLError, OSError, UnicodeError) as error:
        return {"kind": "retry", "error_class": redact(str(error), 80) or "network-error"}


def discover_public_sources(claim, timeout, excluded=()):
    query = discovery_query(claim)
    if not query or timeout <= 0:
        return {"kind": "exhausted", "urls": [], "error_class": "empty-discovery-query"}
    search_url = f"{DISCOVERY_ENDPOINT}?{urlencode({'q': query})}"
    result = fetch_public_source(search_url, timeout)
    if result["kind"] != "success":
        return {"kind": result["kind"], "urls": [], "error_class": result.get("error_class", "discovery-unavailable"), "status_code": result.get("status_code")}
    parser = SearchLinkParser()
    try:
        parser.feed(result["body"][:MAX_SOURCE_BYTES])
    except Exception:
        return {"kind": "exhausted", "urls": [], "error_class": "discovery-parse-error"}
    excluded = set(excluded)
    urls = []
    for href in parser.links:
        candidate = urljoin(DISCOVERY_ENDPOINT, href)
        parsed = urlsplit(candidate)
        target = parse_qs(parsed.query).get("uddg", [None])[0]
        candidate = unquote(target or candidate)
        try:
            candidate = canonical_public_url(candidate)
        except VerificationError:
            continue
        host = (urlsplit(candidate).hostname or "").lower().rstrip(".")
        if host in YOUTUBE_HOSTS or host in DISCOVERY_HOSTS or candidate in excluded or candidate in urls:
            continue
        urls.append(candidate)
        if len(urls) >= MAX_DISCOVERY_RESULTS:
            break
    return {"kind": "success", "urls": urls, "status_code": result.get("status_code"), "route": "discovery-search"}


def _allowed_attempts(value):
    if not isinstance(value, list) or len(value) > 4:
        return False
    for attempt in value:
        if not isinstance(attempt, dict) or set(attempt) - {"route", "attempt", "status_code", "error_class", "at"}:
            return False
        if attempt.get("route") not in {"public-http", "discovery-search", "existing-source"} or type(attempt.get("attempt")) is not int or not 1 <= attempt["attempt"] <= MAX_ATTEMPTS:
            return False
        status = attempt.get("status_code")
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            return False
        if not _timestamp(attempt.get("at")):
            return False
    return True


def validate_item(item):
    required = {
        "claim_id", "claim", "claim_sha256", "source_url", "evidence_source_url", "authority_host", "status", "provenance_class",
        "evidence_status", "evidence_eligible", "transcript_eligible", "retrieval_method", "source_path",
        "content_sha256", "retrieved_at", "attempt", "retry_count", "attempts", "next_retry_at", "deadline_at",
        "lease_until", "error_class", "result_excerpt", "updated_at",
    }
    if not isinstance(item, dict) or set(item) != required:
        return False
    if not QUEUE_ID.fullmatch(str(item["claim_id"])) or not isinstance(item["claim"], str) or not 1 <= len(item["claim"]) <= MAX_CLAIM_CHARS:
        return False
    if type(item["claim_sha256"]) is not str or not SHA256.fullmatch(item["claim_sha256"]):
        return False
    if item["claim_sha256"] != hashlib.sha256(item["claim"].encode("utf-8")).hexdigest():
        return False
    try:
        url = canonical_public_url(item["source_url"]) if item["source_url"] is not None else None
        expected_authority = authority_host(item["authority_host"])
    except VerificationError:
        return False
    if url != item["source_url"] or expected_authority != item["authority_host"] or item["status"] not in QUEUE_STATES or item["evidence_status"] not in EVIDENCE_STATES:
        return False
    if item["provenance_class"] not in {"web-extraction", "none"} or type(item["evidence_eligible"]) is not bool or type(item["transcript_eligible"]) is not bool:
        return False
    try:
        expected_evidence, expected_transcript = eligibility(item["provenance_class"], item["evidence_status"])
    except VerificationError:
        return False
    if (item["evidence_eligible"], item["transcript_eligible"]) != (expected_evidence, expected_transcript):
        return False
    if item["source_url"] is not None and not isinstance(item["source_url"], str):
        return False
    try:
        evidence_url = canonical_public_url(item["evidence_source_url"]) if item["evidence_source_url"] is not None else None
    except VerificationError:
        return False
    if evidence_url != item["evidence_source_url"]:
        return False
    if item["retrieval_method"] != "public-http" or not _timestamp(item["retrieved_at"], required=False) or not _timestamp(item["updated_at"]):
        return False
    source_path = item["source_path"]
    if source_path is not None and (not isinstance(source_path, str) or not source_path.startswith("raw/") or ".." in Path(source_path).parts):
        return False
    if item["content_sha256"] is not None and not SHA256.fullmatch(str(item["content_sha256"])):
        return False
    if type(item["attempt"]) is not int or not 0 <= item["attempt"] <= MAX_ATTEMPTS or type(item["retry_count"]) is not int or not 0 <= item["retry_count"] <= MAX_RETRIES:
        return False
    if not _allowed_attempts(item["attempts"]):
        return False
    for field in ("next_retry_at", "deadline_at", "lease_until"):
        value = item[field]
        if value is not None and (type(value) not in {int, float} or not math.isfinite(value) or value < 0):
            return False
    for field in ("error_class", "result_excerpt"):
        value = item[field]
        if value is not None and (not isinstance(value, str) or len(value) > MAX_RESULT_CHARS or "\n" in value or "\r" in value):
            return False
    if item["status"] == "verified" and (
        item["provenance_class"] != "web-extraction"
        or item["evidence_status"] != "verified"
        or not item["source_path"]
        or not item["content_sha256"]
        or not item["retrieved_at"]
    ):
        return False
    if item["status"] == "running" and (item["evidence_status"] != "verifying" or item["deadline_at"] is None or item["lease_until"] is None):
        return False
    if item["status"] != "running" and (item["deadline_at"] is not None or item["lease_until"] is not None):
        return False
    expected_id = claim_id_for(item["claim"], item["source_url"], item["authority_host"])
    return expected_id == item["claim_id"]


def read_queue(wiki, queue_id):
    path = queue_path(wiki, queue_id)
    if path.is_symlink():
        return "invalid", None, "verification queue is a symlink"
    if not path.exists():
        return "missing", None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return "invalid", None, f"verification queue is unreadable: {redact(str(error), 120)}"
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data.get("schema_version") != SCHEMA_VERSION:
        if isinstance(data, dict) and type(data.get("schema_version")) is int and data["schema_version"] > SCHEMA_VERSION:
            return "future-schema", None, "verification queue schema is newer than this runtime"
        return "invalid", None, "verification queue schema is invalid"
    if data.get("queue_id") != queue_id or not _timestamp(data.get("created_at")) or not _timestamp(data.get("updated_at")):
        return "invalid", None, "verification queue metadata is invalid"
    items = data.get("items")
    if isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict) and "evidence_source_url" not in items[0]:
        items[0] = {**items[0], "evidence_source_url": None}
    if not isinstance(items, list) or len(items) != 1 or any(not validate_item(item) for item in items):
        return "invalid", None, "verification queue item is invalid"
    source_path = items[0]["source_path"]
    if source_path:
        try:
            source = _safe_path(wiki, Path(wiki) / source_path)
            fields = _frontmatter(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, VerificationError):
            return "invalid", None, "verification queue source is invalid"
        expected_source = items[0]["evidence_source_url"] or items[0]["source_url"]
        if fields.get("source_url") != expected_source or fields.get("content_sha256") != items[0]["content_sha256"]:
            return "invalid", None, "verification queue source provenance does not match"
    return "valid", data, None


def new_item(claim, source_url, expected_authority):
    claim = normalize_claim(claim)
    source_url = canonical_public_url(source_url) if source_url else None
    expected_authority = authority_host(expected_authority)
    queue_id = claim_id_for(claim, source_url, expected_authority)
    reason = boundary_reason(claim)
    status = "blocked" if reason else "pending"
    evidence_status = "blocked" if reason else "unverified"
    stamp = now_iso()
    return queue_id, {
        "claim_id": queue_id,
        "claim": claim,
        "claim_sha256": hashlib.sha256(claim.encode("utf-8")).hexdigest(),
        "source_url": source_url,
        "evidence_source_url": None,
        "authority_host": expected_authority,
        "status": status,
        "provenance_class": "none",
        "evidence_status": evidence_status,
        "evidence_eligible": False,
        "transcript_eligible": False,
        "retrieval_method": "public-http",
        "source_path": None,
        "content_sha256": None,
        "retrieved_at": None,
        "attempt": 0,
        "retry_count": 0,
        "attempts": [],
        "next_retry_at": None,
        "deadline_at": None,
        "lease_until": None,
        "error_class": reason,
        "result_excerpt": None,
        "updated_at": stamp,
    }


def ensure_queue(wiki, claim, source_url, expected_authority=None):
    queue_id, item = new_item(claim, source_url, expected_authority)
    path = queue_path(wiki, queue_id)
    lock = lock_path(wiki, queue_id)
    status, data, error = read_queue(wiki, queue_id)
    if status == "future-schema":
        raise FutureSchema(error)
    if status == "invalid":
        raise VerificationError(error)
    fd, reason = acquire_file_lock(lock)
    if fd is None:
        raise VerificationError(f"verification queue lock unavailable: {reason}")
    try:
        status, data, error = read_queue(wiki, queue_id)
        if status == "future-schema":
            raise FutureSchema(error)
        if status == "invalid":
            raise VerificationError(error)
        if status == "missing":
            queued = sum(1 for candidate in path.parent.iterdir() if candidate.is_file() and candidate.name.endswith(".json"))
            if queued >= MAX_QUEUE_ITEMS:
                raise VerificationError("verification queue is full; retention must run before new public claims are queued")
            stamp = now_iso()
            data = {"schema_version": SCHEMA_VERSION, "queue_id": queue_id, "created_at": stamp, "updated_at": stamp, "items": [item]}
            atomic_json(path, data)
        return queue_id, data
    finally:
        release_file_lock(fd)


def _claim(wiki, queue_id, lease_seconds):
    path = queue_path(wiki, queue_id)
    lock = lock_path(wiki, queue_id)
    fd, reason = acquire_file_lock(lock)
    if fd is None:
        return {"status": "unavailable", "error": reason}
    try:
        status, data, error = read_queue(wiki, queue_id)
        if status != "valid":
            return {"status": status, "error": error}
        item = data["items"][0]
        if item["status"] in {"verified", "blocked"}:
            return {"status": "ok", "claim": None, "item": item}
        if item["status"] == "running" and item["lease_until"] and item["lease_until"] > time.time():
            return {"status": "ok", "claim": None, "item": item}
        current_time = time.time()
        if item["next_retry_at"] is not None and item["next_retry_at"] > current_time:
            return {"status": "ok", "claim": None, "item": item}
        if item["status"] == "exhausted" and item["next_retry_at"] is not None:
            if item["retry_count"] >= MAX_RETRIES:
                item["next_retry_at"] = None
                item["updated_at"] = now_iso()
                atomic_json(path, data)
                return {"status": "ok", "claim": None, "item": item}
            item["status"] = "pending"
            item["evidence_status"] = "unverified"
            item["retry_count"] += 1
            item["attempt"] = 0
            item["next_retry_at"] = None
            item["error_class"] = "scheduled-retry"
            item["updated_at"] = now_iso()
            atomic_json(path, data)
        if item["attempt"] >= MAX_ATTEMPTS:
            item["status"] = "exhausted"
            item["evidence_status"] = "exhausted" if item["source_path"] is None else "unverified"
            item["next_retry_at"] = None
            item["updated_at"] = now_iso()
            atomic_json(path, data)
            return {"status": "ok", "claim": None, "item": item}
        item["status"] = "running"
        item["evidence_status"] = "verifying"
        item["attempt"] += 1
        item["deadline_at"] = time.time() + lease_seconds
        item["lease_until"] = item["deadline_at"]
        item["updated_at"] = now_iso()
        atomic_json(path, data)
        return {"status": "ok", "claim": {"queue_id": queue_id, "attempt": item["attempt"], "source_url": item["source_url"], "authority_host": item["authority_host"], "claim": item["claim"]}, "item": item}
    finally:
        release_file_lock(fd)


def _finish(wiki, queue_id, claim, outcome):
    path = queue_path(wiki, queue_id)
    lock = lock_path(wiki, queue_id)
    fd, reason = acquire_file_lock(lock)
    if fd is None:
        return {"status": "unavailable", "error": reason}
    try:
        status, data, error = read_queue(wiki, queue_id)
        if status != "valid":
            return {"status": status, "error": error}
        item = data["items"][0]
        if item["status"] != "running" or item["attempt"] != claim["attempt"]:
            return {"status": "stale-claim"}
        attempt = {"route": outcome.get("route", "public-http"), "attempt": claim["attempt"], "at": now_iso()}
        if outcome.get("status_code") is not None:
            attempt["status_code"] = outcome["status_code"]
        if outcome.get("error_class"):
            attempt["error_class"] = redact(outcome["error_class"], 80)
        item["attempts"] = (item["attempts"] + [attempt])[-4:]
        state = outcome.get("state", "exhausted")
        evidence_status = outcome.get("evidence_status", "unverified")
        next_retry_at = outcome.get("next_retry_at")
        if state == "pending" and claim["attempt"] >= MAX_ATTEMPTS:
            state = "exhausted"
            evidence_status = "unverified" if outcome.get("source_path") else "exhausted"
            next_retry_at = None
        if state in {"pending", "exhausted"} and next_retry_at is None and item["retry_count"] < MAX_RETRIES:
            next_retry_at = time.time() + RETRY_DELAY_SECONDS
        if state == "pending" and item["retry_count"] >= MAX_RETRIES and next_retry_at is None:
            state = "exhausted"
            evidence_status = "unverified" if outcome.get("source_path") else "exhausted"
        item["status"] = state if state in QUEUE_STATES else "exhausted"
        item["evidence_status"] = evidence_status
        item["provenance_class"] = outcome.get("provenance_class", "none")
        item["evidence_eligible"] = outcome.get("evidence_eligible", False)
        item["transcript_eligible"] = False
        item["source_path"] = outcome.get("source_path")
        item["evidence_source_url"] = outcome.get("evidence_source_url", item["evidence_source_url"])
        item["content_sha256"] = outcome.get("content_sha256")
        item["retrieved_at"] = outcome.get("retrieved_at")
        item["error_class"] = redact(outcome.get("error_class"), 80) if outcome.get("error_class") else None
        item["result_excerpt"] = redact(outcome.get("result_excerpt"), MAX_RESULT_CHARS) if outcome.get("result_excerpt") else None
        item["next_retry_at"] = next_retry_at
        item["lease_until"] = None
        item["deadline_at"] = None
        item["updated_at"] = now_iso()
        data["updated_at"] = item["updated_at"]
        atomic_json(path, data)
        return {"status": "updated", "item": item}
    finally:
        release_file_lock(fd)


def _existing_outcome(wiki, path):
    fields = _frontmatter(path.read_text(encoding="utf-8"))
    return {
        "state": "verified",
        "evidence_status": "verified",
        "provenance_class": "web-extraction",
        "evidence_eligible": True,
        "source_path": str(path.relative_to(Path(wiki))),
        "evidence_source_url": fields.get("source_url"),
        "content_sha256": fields.get("content_sha256"),
        "retrieved_at": fields.get("retrieved_at"),
        "route": "existing-source",
        "result_excerpt": "claim-evidence=existing verified source",
    }


def _acquired_outcome(wiki, claim, source_url, result, route):
    retrieved_at = now_iso()
    path = persist_web_source(wiki, source_url, result["body"], retrieved_at)
    digest = hashlib.sha256(result["body"].encode("utf-8")).hexdigest()
    supported, matched = claim_supported(claim["claim"], result["body"])
    authority_match = bool(claim["authority_host"] and host_matches(result["final_url"], claim["authority_host"]))
    evidence = ",".join(matched) or "none"
    if supported and authority_match:
        mark_source_verified(path, claim["authority_host"])
        return {
            "state": "verified",
            "evidence_status": "verified",
            "provenance_class": "web-extraction",
            "evidence_eligible": True,
            "source_path": str(path.relative_to(Path(wiki))),
            "evidence_source_url": source_url,
            "content_sha256": digest,
            "retrieved_at": retrieved_at,
            "status_code": result.get("status_code"),
            "route": route,
            "result_excerpt": f"claim-evidence=matched:{evidence}",
        }
    if not supported:
        reason = "claim-not-supported"
        excerpt = f"claim-not-supported; matched:{evidence}"
    elif not claim["authority_host"]:
        reason = "authority-binding-required"
        excerpt = f"claim-evidence=matched:{evidence}; explicit-authority-required"
    else:
        reason = "no-authoritative-match"
        excerpt = f"claim-evidence=matched:{evidence}; no-authoritative-match"
    return {
        "state": "exhausted",
        "evidence_status": "unverified",
        "provenance_class": "web-extraction",
        "evidence_eligible": True,
        "source_path": str(path.relative_to(Path(wiki))),
        "evidence_source_url": source_url,
        "content_sha256": digest,
        "retrieved_at": retrieved_at,
        "status_code": result.get("status_code"),
        "route": route,
        "error_class": reason,
        "result_excerpt": excerpt,
    }


def verify_claim(wiki, claim, timeout):
    started = time.monotonic()
    direct_url = claim.get("source_url")
    candidates = []
    best = None
    failure = None

    def remaining():
        return timeout - (time.monotonic() - started)

    def consider(url, route):
        nonlocal best, failure
        if not url or url in candidates:
            return None
        candidates.append(url)
        existing = existing_verified_source(wiki, url, claim["authority_host"], claim["claim"])
        if existing:
            return _existing_outcome(wiki, existing)
        if remaining() < MIN_TIMEOUT:
            return None
        result = fetch_public_source(url, min(MAX_TIMEOUT, remaining()))
        if result["kind"] == "success":
            try:
                outcome = _acquired_outcome(wiki, claim, url, result, route)
            except (FutureSchema, OSError, UnicodeError, VerificationError) as error:
                return {"state": "blocked", "evidence_status": "blocked", "error_class": str(error), "route": route}
            if outcome["state"] == "verified":
                return outcome
            best = best or outcome
            return None
        failure = result
        return None

    if direct_url:
        outcome = consider(direct_url, "public-http")
        if outcome:
            return outcome

    discovered = {"kind": "exhausted", "urls": [], "error_class": "discovery-not-run"}
    if remaining() >= MIN_TIMEOUT:
        discovered = discover_public_sources(claim["claim"], min(MAX_TIMEOUT, remaining()), candidates)
        for url in discovered.get("urls", []):
            outcome = consider(url, "discovery-search")
            if outcome:
                return outcome

    if best:
        return best
    if failure and failure.get("kind") == "blocked":
        reason = failure.get("error_class", "authority-boundary")
        if failure.get("status_code") in {401, 403}:
            reason = "access-control boundary"
        return {"state": "blocked", "evidence_status": "blocked", "error_class": reason, "status_code": failure.get("status_code"), "route": "public-http"}
    if (failure and failure.get("kind") == "retry") or discovered.get("kind") == "retry":
        retry_error = (failure or discovered).get("error_class", "network-error")
        return {"state": "pending", "evidence_status": "unverified", "error_class": retry_error, "status_code": (failure or discovered).get("status_code"), "next_retry_at": time.time() + RETRY_DELAY_SECONDS, "route": "public-http"}
    error_class = (failure or discovered).get("error_class", "source-unavailable")
    if discovered.get("kind") == "success" and not discovered.get("urls"):
        error_class = "discovery-no-results"
    return {"state": "exhausted", "evidence_status": "exhausted", "error_class": error_class, "status_code": (failure or discovered).get("status_code"), "route": "public-http"}


def drain_queue(wiki, queue_id, deadline=6.0):
    if not isinstance(deadline, (int, float)) or not math.isfinite(deadline) or not MIN_TIMEOUT <= deadline <= MAX_TIMEOUT:
        raise VerificationError("--deadline must be between 1 and 15 seconds")
    started = time.monotonic()
    claimed = _claim(wiki, queue_id, deadline)
    if claimed["status"] != "ok" or claimed.get("claim") is None:
        return queue_snapshot(wiki, queue_id)
    remaining = deadline - (time.monotonic() - started)
    if remaining < MIN_TIMEOUT:
        outcome = {
            "state": "pending",
            "evidence_status": "unverified",
            "error_class": "deadline-exhausted",
            "next_retry_at": time.time() + RETRY_DELAY_SECONDS,
            "route": "public-http",
        }
    else:
        outcome = verify_claim(wiki, claimed["claim"], min(MAX_TIMEOUT, remaining))
    result = _finish(wiki, queue_id, claimed["claim"], outcome)
    if result.get("status") == "updated":
        return {"status": "ok", "processed": 1, "queue_id": queue_id, "item": result["item"]}
    return result


def drain_due_queue(wiki, deadline=4.0, limit=MAX_SCHEDULED_ITEMS):
    if not isinstance(deadline, (int, float)) or not math.isfinite(deadline) or not MIN_TIMEOUT <= deadline <= MAX_TIMEOUT:
        raise VerificationError("scheduled verification deadline must be between 1 and 15 seconds")
    if type(limit) is not int or not 1 <= limit <= MAX_QUEUE_ITEMS:
        raise VerificationError("scheduled verification limit is invalid")
    root = queue_directory(wiki)
    due = []
    current_time = time.time()
    for path in sorted(root.glob("*.json"))[:MAX_QUEUE_ITEMS]:
        queue_id = path.stem
        if not QUEUE_ID.fullmatch(queue_id):
            continue
        status, data, _ = read_queue(wiki, queue_id)
        if status != "valid":
            continue
        item = data["items"][0]
        retry_at = item.get("next_retry_at")
        if item["status"] == "pending" and (retry_at is None or retry_at <= current_time):
            due.append(queue_id)
        elif item["status"] == "exhausted" and retry_at is not None and retry_at <= current_time:
            due.append(queue_id)
        elif item["status"] == "running" and item.get("lease_until") is not None and item["lease_until"] <= current_time:
            due.append(queue_id)
        if len(due) >= limit:
            break
    started = time.monotonic()
    processed = 0
    for queue_id in due:
        remaining = deadline - (time.monotonic() - started)
        if remaining < MIN_TIMEOUT:
            break
        result = drain_queue(wiki, queue_id, min(MAX_TIMEOUT, remaining))
        processed += int(result.get("processed", 0))
    return {"status": "ok", "processed": processed, "queued": len(due)}


def queue_snapshot(wiki, queue_id):
    status, data, error = read_queue(wiki, queue_id)
    if status != "valid":
        return {"status": status, "queue_id": queue_id, "error": error}
    return {"status": "ok", "queue_id": queue_id, "item": data["items"][0], "processed": 0}


def retry_queue(wiki, queue_id):
    path = queue_path(wiki, queue_id)
    lock = lock_path(wiki, queue_id)
    fd, reason = acquire_file_lock(lock)
    if fd is None:
        raise VerificationError(f"verification queue lock unavailable: {reason}")
    try:
        status, data, error = read_queue(wiki, queue_id)
        if status != "valid":
            raise VerificationError(error)
        item = data["items"][0]
        if item["status"] not in {"exhausted", "blocked"}:
            raise VerificationError("only terminal verification records can be retried")
        if item["retry_count"] >= MAX_RETRIES:
            raise VerificationError("verification retry limit reached")
        item["status"] = "pending"
        item["evidence_status"] = "unverified"
        item["retry_count"] += 1
        item["next_retry_at"] = None
        item["error_class"] = "explicit-retry"
        item["updated_at"] = now_iso()
        data["updated_at"] = item["updated_at"]
        atomic_json(path, data)
        return queue_snapshot(wiki, queue_id)
    finally:
        release_file_lock(fd)


def prompt_items(prompt):
    if not prompt:
        return []
    url_matches = list(re.finditer(r"https?://[^\s<>'\"]+", prompt))
    if not PUBLIC_GAP.search(prompt) and not url_matches and not explicit_authority(prompt):
        return []
    urls = []
    for match in url_matches:
        candidate = match.group(0).rstrip(".,;:!?)]}")
        try:
            url = canonical_public_url(candidate)
        except VerificationError:
            continue
        if (urlsplit(url).hostname or "").lower().rstrip(".") in YOUTUBE_HOSTS:
            continue
        if url not in urls:
            urls.append(url)
    expected_authority = explicit_authority(prompt)
    if not urls:
        return [(redact(prompt, MAX_CLAIM_CHARS), None, expected_authority)]
    return [
        (
            redact(prompt, MAX_CLAIM_CHARS),
            url,
            expected_authority,
        )
        for url in urls[:8]
    ]


def context_for_item(wiki, item):
    source = item.get("source_url", "")
    if source:
        parsed = urlsplit(source)
        safe_source = urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))
    else:
        safe_source = "discovery"
    fields = [
        f"source={safe_source}",
        f"status={item.get('status', 'unavailable')}",
        f"evidence_status={item.get('evidence_status', 'unverified')}",
        f"provenance={item.get('provenance_class', 'none')}",
        f"evidence={'eligible' if item.get('evidence_eligible') else 'ineligible'}",
    ]
    if item.get("content_sha256"):
        fields.append(f"content_sha256={item['content_sha256']}")
    if item.get("evidence_source_url"):
        evidence = urlsplit(item["evidence_source_url"])
        fields.append(f"evidence_source={urlunsplit((evidence.scheme, evidence.netloc, evidence.path or '/', '', ''))}")
    if item.get("source_path"):
        fields.append(f"source_path={safe_text(item['source_path'], 160)}")
    if item.get("next_retry_at"):
        fields.append("retry=durable")
    if item.get("authority_host"):
        fields.append(f"authority={safe_text(item['authority_host'], 120)}")
    if item.get("status") == "blocked" and item.get("error_class") in AUTHORITY_REASONS:
        fields.append(f"authority_request={safe_text(item.get('error_class') or 'specific authority boundary', 120)}")
    return "- " + "; ".join(fields)


def preflight_context(wiki, prompt, deadline=6.0):
    items = prompt_items(prompt)
    if not items:
        if PUBLIC_GAP.search(prompt):
            return "Public evidence verification: status=unverified; source=not-found; agent-owned bounded lookup/retry required."
        return ""
    lines = ["Public evidence verification (agent-owned; bounded; public sources only):"]
    started = time.monotonic()
    for index, (claim, source_url, expected) in enumerate(items):
        try:
            queue_id, _ = ensure_queue(wiki, claim, source_url, expected)
            remaining = deadline - (time.monotonic() - started)
            snapshot = (
                drain_queue(wiki, queue_id, min(MAX_TIMEOUT, remaining))
                if index < 2 and remaining >= MIN_TIMEOUT
                else queue_snapshot(wiki, queue_id)
            )
            item = snapshot.get("item")
            if item:
                lines.append(context_for_item(wiki, item))
            else:
                lines.append(f"- source={safe_text(source_url, 160)}; status={snapshot.get('status', 'unavailable')}; evidence_status=unverified")
        except (OSError, VerificationError) as error:
            lines.append(f"- source={safe_text(source_url, 160)}; status=unverified; evidence_status=unverified; retry=durable; reason={safe_text(str(error), 120)}")
    if len(items) > 2:
        lines.append(f"- queued={len(items) - 2} additional public source(s); automatic retry remains agent-owned.")
    lines.append("Public-source gaps must be reported as unverified, exhausted, or blocked; do not delegate routine verification to the user.")
    return "\n".join(lines)


def self_test():
    assert len(claim_id_for("claim", "https://example.test/spec", None)) == 32
    assert claim_id_for("claim", None, None) != claim_id_for("claim", "https://example.test/spec", None)
    assert canonical_public_url("https://example.test/spec#fragment") == "https://example.test/spec"
    assert explicit_authority("docs authority_host=vendor.example") == "vendor.example"
    assert explicit_authority("docs https://vendor.example/spec") is None
    assert claim_supported("PostgreSQL supports online index builds", "PostgreSQL supports online index builds.") == (True, ["postgresql", "supports", "online", "index", "builds"])
    assert claim_supported("PostgreSQL supports online index builds", "PostgreSQL has indexes.")[0] is False
    assert claim_supported("PostgreSQL supports online index builds", "It is not true that PostgreSQL supports online index builds.")[0] is False
    assert claim_supported("PostgreSQL supports online index builds", "PostgreSQL supports online index builds. It is not true.")[0] is False
    assert claim_supported("PostgreSQL supports online index builds", "PostgreSQL supports online index builds only in a hypothetical example.")[0] is False
    assert claim_supported("PostgreSQL supports online index builds", "A deprecated draft says PostgreSQL supports online index builds.")[0] is False
    assert claim_supported("PostgreSQL supports online index builds", "PostgreSQL supports online index builds, but only in this fictional scenario.")[0] is False
    assert boundary_reason("verify production drop database") == "destructive production verification"
    assert not boundary_reason("verify public vendor docs")
    print("evidence verification self-test passed")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    queue = commands.add_parser("queue")
    queue.add_argument("--workspace", required=True)
    queue.add_argument("--claim", required=True)
    queue.add_argument("--source-url")
    queue.add_argument("--authority-host")
    drain = commands.add_parser("drain")
    drain.add_argument("--workspace", required=True)
    drain.add_argument("--queue-id", required=True)
    drain.add_argument("--deadline", type=float, default=6.0)
    retry = commands.add_parser("retry")
    retry.add_argument("--workspace", required=True)
    retry.add_argument("--queue-id", required=True)
    scheduled = commands.add_parser("scheduled-drain")
    scheduled.add_argument("--workspace", required=True)
    scheduled.add_argument("--deadline", type=float, default=4.0)
    scheduled.add_argument("--limit", type=int, default=MAX_SCHEDULED_ITEMS)
    commands.add_parser("self-test")
    args = parser.parse_args()
    try:
        if args.command == "self-test":
            self_test()
            return
        wiki = Path(args.workspace).expanduser().resolve() / ".wiki"
        if args.command == "queue":
            queue_id, data = ensure_queue(wiki, args.claim, args.source_url, args.authority_host)
            print(json.dumps({"status": "ok", "queue_id": queue_id, "item": data["items"][0]}, ensure_ascii=False, sort_keys=True))
        elif args.command == "drain":
            print(json.dumps(drain_queue(wiki, args.queue_id, args.deadline), ensure_ascii=False, sort_keys=True))
        elif args.command == "retry":
            print(json.dumps(retry_queue(wiki, args.queue_id), ensure_ascii=False, sort_keys=True))
        else:
            print(json.dumps(drain_due_queue(wiki, args.deadline, args.limit), ensure_ascii=False, sort_keys=True))
    except (OSError, VerificationError) as error:
        print(json.dumps({"status": "invalid" if isinstance(error, VerificationError) else "unavailable", "error": safe_text(str(error), 200)}, ensure_ascii=False))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
