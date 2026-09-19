#!/usr/bin/env python3
"""Fetch YouTube captions and explicitly agent-owned local transcriptions."""

import argparse
import hashlib
import importlib
from importlib import metadata as importlib_metadata
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlparse

try:
    import fcntl
except ImportError:  # pragma: no cover - the package currently targets POSIX hosts.
    fcntl = None


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
LANGUAGES = re.compile(r"^[A-Za-z0-9_.*,-]{1,200}$")
FORMATS = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
YTDLP_VERSION = "2026.08.19"
TIMEOUT_MIN = 5
TIMEOUT_MAX = 600
QUEUE_SCHEMA_VERSION = 3
QUEUE_LEGACY_SCHEMA_VERSION = 1
QUEUE_PREVIOUS_SCHEMA_VERSION = 2
QUEUE_DIRNAME = "youtube-queues"
RECEIPT_SCHEMA_VERSION = 1
ADAPTER_RECEIPT_SCHEMA_VERSION = 2
TRANSCRIPT_API_VERSION = "1.2.4"
RECEIPT_DIRNAME = "youtube-receipts"
QUEUE_ID = re.compile(r"^[0-9a-f]{32}$")
RECEIPT_ID = re.compile(r"^[0-9a-f]{32}$")
QUEUE_STATES = frozenset({"pending", "running", "retryable", "ok", "no-captions", "exhausted", "error", "blocked", "blocked-install"})
QUEUE_TERMINAL_STATES = frozenset({"ok", "no-captions", "exhausted", "error", "blocked", "blocked-install"})
QUEUE_RETRYABLE_STATES = frozenset({"pending", "retryable"})
QUEUE_AUTOMATIC_RETRY_CAP = 3
QUEUE_RETRY_BASE_SECONDS = 5
QUEUE_RETRY_MAX_SECONDS = 60
RETRYABLE_CAPTION_ERRORS = frozenset({"network", "timeout", "process-failed", "deadline-exhausted", "caption-lock", "caption-fetch-failed", "invalid-helper-output", "transcript-api-parser"})
QUEUE_LOCK_TIMEOUT = 0.5
DRAIN_DEFAULT_DEADLINE = 20
DRAIN_DEFAULT_CONCURRENCY = 1
DRAIN_DEFAULT_LIMIT = 2
DRAIN_PER_URL_TIMEOUT = 12
RESULT_STATUSES = frozenset({"ok", "no-captions", "stale-captions-ignored", "metadata-only", "error", "blocked", "blocked-install", "install-approval-required", "installer-failed"})
RESULT_STATUSES = RESULT_STATUSES | frozenset({"machine-transcription", "machine-transcription-existing"})
PROVENANCE_CLASSES = frozenset({"caption", "web-extraction", "metadata", "machine-transcription", "none"})
TRANSCRIPTION_LANGUAGE = re.compile(r"^(?:auto|[A-Za-z]{2,10})$")
MAX_TRANSCRIPTION_DURATION = 7200
MAX_TRANSCRIPTION_AUDIO_BYTES = 1024 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 25 * 1024 * 1024
SENSITIVE = re.compile(
    r"((?:api[_ -]?key|password|secret|token|private[_ -]?key|authorization)\s*[:=])[^\r\n]*",
    re.I,
)


def safe_text(value, limit=1200):
    value = SENSITIVE.sub(lambda match: f"{match.group(1)} [REDACTED]", value or "")
    value = re.sub(
        r"https?://\S+",
        lambda match: urlparse(match.group(0))._replace(query="", fragment="").geturl(),
        value,
    )
    return re.sub(r"\s+", " ", value).strip()[:limit]


def emit(payload, exit_code=0):
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    raise SystemExit(exit_code)


def safe_attempts(attempts):
    result = []
    for item in (attempts or [])[:4]:
        if not isinstance(item, dict):
            continue
        entry = {}
        route = item.get("route")
        if route in {"caption", "transcript-api", "metadata", "audio", "transcription"}:
            entry["route"] = route
        attempt = item.get("attempt")
        if type(attempt) is int and 1 <= attempt <= 4:
            entry["attempt"] = attempt
        returncode = item.get("returncode")
        if type(returncode) is int and -255 <= returncode <= 255:
            entry["returncode"] = returncode
        error_class = item.get("error_class")
        if error_class:
            entry["error_class"] = safe_text(str(error_class), 80)
        if entry:
            result.append(entry)
    return result


def safe_metadata(data):
    if not isinstance(data, dict):
        return None
    result = {}
    for field in ("title", "uploader", "channel", "upload_date", "description"):
        value = data.get(field)
        if isinstance(value, str):
            value = safe_text(value, 2000 if field == "description" else 300)
            if value:
                result[field] = value
    duration = data.get("duration")
    if type(duration) in (int, float) and math.isfinite(duration) and 0 <= duration <= 86400:
        result["duration"] = int(duration)
    return result or None


def safe_transcription(data):
    if not isinstance(data, dict):
        return None
    allowed = {"schema_version", "engine", "language", "model", "model_sha256", "transcript_sha256", "duration_seconds", "created_at"}
    if set(data) != allowed or data.get("schema_version") != 1:
        return None
    if data.get("engine") != "whisper-cli" or not TRANSCRIPTION_LANGUAGE.fullmatch(str(data.get("language") or "")):
        return None
    if not isinstance(data.get("model"), str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", data["model"]):
        return None
    for name in ("model_sha256", "transcript_sha256"):
        if not isinstance(data.get(name), str) or not re.fullmatch(r"[0-9a-f]{64}", data[name]):
            return None
    if type(data.get("duration_seconds")) is not int or not 0 <= data["duration_seconds"] <= 86400:
        return None
    if not isinstance(data.get("created_at"), str) or len(data["created_at"]) > 40:
        return None
    return data


def safe_file_names(paths):
    names = []
    for value in (paths or []):
        if not isinstance(value, (str, Path)):
            continue
        name = Path(value).name
        if name and name not in {".", ".."} and len(name) <= 255:
            names.append(name)
    return names


def caption_receipt_parts(value):
    if not isinstance(value, str) or not value or len(value) > 400 or "\x00" in value or "\\" in value:
        raise ValueError("caption receipt file path is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.parts[:2] != ("inbox", "youtube")
        or path.suffix != ".vtt"
    ):
        raise ValueError("caption receipt file path is invalid")
    return path.parts


def receipt_file_path(wiki, value):
    parts = caption_receipt_parts(value)
    wiki = Path(wiki).expanduser().resolve()
    if not valid_wiki(wiki):
        raise ValueError("caption receipt requires a valid local LLM Wiki")
    path = wiki
    for part in parts:
        path /= part
        if path.is_symlink():
            raise ValueError("caption receipt file path is a symlink")
    try:
        if not path.resolve().is_relative_to(wiki):
            raise ValueError("caption receipt file path leaves the Wiki")
    except OSError as error:
        raise ValueError("caption receipt file path is unreadable") from error
    return path


def validate_caption_receipt(data, *, wiki=None, expected_url=None, expected_video_id=None, verify_files=False):
    fields = {
        "schema_version",
        "receipt_id",
        "canonical_url",
        "video_id",
        "status",
        "provenance_class",
        "attempt",
        "attempted_at",
        "files",
    }
    if not isinstance(data, dict):
        raise ValueError("caption receipt schema is invalid")
    version = data.get("schema_version")
    adapter_fields = {
        "adapter",
        "adapter_version",
        "track_language_code",
        "track_language",
        "track_is_generated",
        "track_is_translatable",
        "translated_from",
        "normalized_sha256",
    }
    if version == RECEIPT_SCHEMA_VERSION and set(data) != fields:
        raise ValueError("caption receipt schema is invalid")
    if version == ADAPTER_RECEIPT_SCHEMA_VERSION and set(data) != fields | adapter_fields:
        raise ValueError("caption receipt schema is invalid")
    if version not in {RECEIPT_SCHEMA_VERSION, ADAPTER_RECEIPT_SCHEMA_VERSION}:
        raise ValueError("caption receipt schema is invalid")
    if not isinstance(data.get("receipt_id"), str) or not RECEIPT_ID.fullmatch(data["receipt_id"]):
        raise ValueError("caption receipt id is invalid")
    try:
        video_id, canonical = canonical_video(data.get("canonical_url"))
    except (TypeError, ValueError) as error:
        raise ValueError("caption receipt URL is invalid") from error
    if canonical != data.get("canonical_url") or video_id != data.get("video_id"):
        raise ValueError("caption receipt URL is not canonical")
    if expected_url is not None and canonical != expected_url:
        raise ValueError("caption receipt URL does not match the requested video")
    if expected_video_id is not None and video_id != expected_video_id:
        raise ValueError("caption receipt video id does not match the requested video")
    if data.get("status") != "ok" or data.get("provenance_class") != "caption":
        raise ValueError("caption receipt provenance is invalid")
    if type(data.get("attempt")) is not int or not 1 <= data["attempt"] <= 3:
        raise ValueError("caption receipt attempt is invalid")
    attempted_at = data.get("attempted_at")
    if not isinstance(attempted_at, str) or not 1 <= len(attempted_at) <= 40 or "\n" in attempted_at or "\r" in attempted_at:
        raise ValueError("caption receipt timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(attempted_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("caption receipt timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError("caption receipt timestamp must include a timezone")
    if version == ADAPTER_RECEIPT_SCHEMA_VERSION:
        if data.get("adapter") != "youtube-transcript-api":
            raise ValueError("caption receipt adapter is invalid")
        if not isinstance(data.get("adapter_version"), str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", data["adapter_version"]):
            raise ValueError("caption receipt adapter version is invalid")
        if not isinstance(data.get("track_language_code"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", data["track_language_code"]):
            raise ValueError("caption receipt track language is invalid")
        if not isinstance(data.get("track_language"), str) or not 1 <= len(data["track_language"]) <= 120 or "\n" in data["track_language"] or "\r" in data["track_language"]:
            raise ValueError("caption receipt track name is invalid")
        if type(data.get("track_is_generated")) is not bool or type(data.get("track_is_translatable")) is not bool:
            raise ValueError("caption receipt track flags are invalid")
        translated_from = data.get("translated_from")
        if translated_from is not None and (not isinstance(translated_from, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", translated_from)):
            raise ValueError("caption receipt translation source is invalid")
        if not isinstance(data.get("normalized_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", data["normalized_sha256"]):
            raise ValueError("caption receipt normalized hash is invalid")
    files = data.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 20:
        raise ValueError("caption receipt files are invalid")
    seen = set()
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise ValueError("caption receipt file entry is invalid")
        parts = caption_receipt_parts(entry.get("path"))
        if parts in seen:
            raise ValueError("caption receipt contains duplicate files")
        seen.add(parts)
        if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise ValueError("caption receipt file hash is invalid")
        if verify_files:
            path = receipt_file_path(wiki, entry["path"])
            try:
                metadata = path.lstat()
                if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or metadata.st_size <= 0:
                    raise ValueError("caption receipt file is not a regular VTT")
                actual = sha256_file(path)
            except OSError as error:
                raise ValueError("caption receipt file is unreadable") from error
            if actual != entry["sha256"]:
                raise ValueError("caption receipt file hash mismatch")
    return data


def valid_caption_receipt(data, *, expected_url=None, expected_video_id=None):
    try:
        validate_caption_receipt(data, expected_url=expected_url, expected_video_id=expected_video_id)
    except (OSError, TypeError, ValueError):
        return False
    return True


def result_contract(
    status,
    video_id,
    url,
    *,
    files=None,
    attempts=None,
    existing_files_ignored=None,
    error_class=None,
    error=None,
    metadata=None,
    transcription=None,
    evidence_reason=None,
    receipt=None,
    evidence_eligible=None,
    transcript_eligible=None,
):
    if status not in RESULT_STATUSES:
        raise ValueError("unsupported helper result status")
    receipt_valid = valid_caption_receipt(receipt, expected_url=url, expected_video_id=video_id)
    receipt_files = [PurePosixPath(entry["path"]).name for entry in receipt["files"]] if receipt_valid else []
    if status == "ok" and (not files or not receipt_valid or receipt_files != safe_file_names(files)):
        raise ValueError("ok result requires fresh caption files and a valid receipt")
    if status == "metadata-only" and not safe_metadata(metadata):
        raise ValueError("metadata-only result requires metadata")
    if status == "machine-transcription" and not files:
        raise ValueError("machine-transcription result requires a fresh transcript file")
    if status == "machine-transcription" and not safe_transcription(transcription):
        raise ValueError("machine-transcription result requires provenance metadata")
    provenance_class = {
        "ok": "caption",
        "metadata-only": "metadata",
        "machine-transcription": "machine-transcription",
    }.get(status, "none")
    reason = evidence_reason or {
        "ok": "fresh-regular-vtt",
        "no-captions": "no-fresh-caption",
        "stale-captions-ignored": "stale-caption-ignored",
        "metadata-only": "metadata-only-not-transcript",
        "error": "caption-route-failed",
        "blocked": "access-boundary",
        "blocked-install": "installer-approval-required",
        "install-approval-required": "installer-approval-required",
        "installer-failed": "installer-failed",
        "machine-transcription": "local-stt-not-caption-evidence",
        "machine-transcription-existing": "existing-machine-transcription-ignored",
    }[status]
    payload = {
        "status": status,
        "video_id": video_id,
        "url": url,
        "provenance_class": provenance_class,
        "evidence_eligible": status in {"ok", "metadata-only"} if evidence_eligible is None else evidence_eligible,
        "transcript_eligible": status == "ok" if transcript_eligible is None else transcript_eligible,
        "evidence_reason": safe_text(str(reason), 120),
        "files": safe_file_names(files),
        "existing_files_ignored": safe_file_names(existing_files_ignored),
        "receipt": receipt if status == "ok" else None,
        "attempts": safe_attempts(attempts),
        "metadata": safe_metadata(metadata) if status == "metadata-only" else None,
        "transcription": safe_transcription(transcription) if status == "machine-transcription" else None,
        "error_class": safe_text(str(error_class), 80) if error_class else None,
        "error": safe_text(str(error), 1200) if error else None,
    }
    return payload


def canonical_video(value):
    if not isinstance(value, str):
        raise ValueError("YouTube URL must be a string")
    raw = value.strip()
    if VIDEO_ID.fullmatch(raw):
        return raw, f"https://www.youtube.com/watch?v={raw}"

    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("only HTTP(S) YouTube URLs are supported")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in YOUTUBE_HOSTS:
        raise ValueError("only YouTube video URLs or 11-character video IDs are supported")

    segments = [segment for segment in parsed.path.split("/") if segment]
    if host in {"youtu.be", "www.youtu.be"}:
        candidate = segments[0] if segments else ""
    else:
        candidate = parse_qs(parsed.query).get("v", [""])[0]
        if not candidate and len(segments) >= 2 and segments[0] in {"shorts", "embed", "live"}:
            candidate = segments[1]

    if not VIDEO_ID.fullmatch(candidate):
        raise ValueError("YouTube URL does not contain a valid video ID")
    return candidate, f"https://www.youtube.com/watch?v={candidate}"


class QueueError(ValueError):
    """A queue cannot be read or safely mutated."""


def queue_id_for(session_id, turn_id):
    if (
        not isinstance(session_id, str)
        or not session_id.strip()
        or not isinstance(turn_id, str)
        or not turn_id.strip()
    ):
        raise QueueError("queue requires complete session and turn ids")
    owner = f"{session_id.strip()}:{turn_id.strip()}"
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()[:32]


def private_state_directory(wiki, dirname, label):
    wiki = Path(wiki).expanduser().resolve()
    if not valid_wiki(wiki) or wiki.is_symlink():
        raise QueueError(f"{label} requires a valid local LLM Wiki")
    current = wiki
    for name in (".sessions", "wiki-agent-system", dirname):
        current = current / name
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise QueueError(f"{label} state path is not a private local directory")
        try:
            current.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(current, 0o700)
        except OSError as error:
            raise QueueError(f"{label} state directory unavailable: {safe_text(str(error), 160)}")
    return current


def queue_directory(wiki):
    return private_state_directory(wiki, QUEUE_DIRNAME, "queue")


def receipt_directory(wiki):
    return private_state_directory(wiki, RECEIPT_DIRNAME, "caption receipt")


def queue_paths(wiki, queue_id):
    if not QUEUE_ID.fullmatch(queue_id):
        raise QueueError("invalid queue id")
    directory = Path(wiki) / ".sessions" / "wiki-agent-system" / QUEUE_DIRNAME
    return directory / f"{queue_id}.json", directory / f"{queue_id}.lock"


def url_lock_path(wiki, url):
    directory = Path(wiki) / ".sessions" / "wiki-agent-system" / QUEUE_DIRNAME / "locks"
    return directory / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.lock"


def atomic_queue_write(path, data):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise QueueError("queue state path is a symlink")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".queue-", text=True)
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, ensure_ascii=False, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
        raise


def atomic_receipt_write(path, data):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise QueueError("caption receipt path is a symlink")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".receipt-", text=True)
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, ensure_ascii=False, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
        raise


def acquire_file_lock(path, timeout=QUEUE_LOCK_TIMEOUT):
    if fcntl is None:
        return None, "locking-unavailable"
    path = Path(path)
    if path.parent.is_symlink() or path.is_symlink():
        return None, "lock-path-is-symlink"
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        os.fchmod(fd, 0o600)
    except OSError:
        return None, "lock-open-failed"

    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd, None
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None, "lock-timeout"
            time.sleep(0.01)
        except OSError:
            os.close(fd)
            return None, "lock-failed"


def release_file_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def caption_lock(wiki, url, timeout):
    queue_directory(wiki)
    fd, reason = acquire_file_lock(url_lock_path(wiki, url), timeout)
    if fd is None:
        raise QueueError(f"caption lock unavailable: {reason}")
    try:
        yield
    finally:
        release_file_lock(fd)


def queue_timestamp(value, required=True):
    if value is None and not required:
        return True
    return isinstance(value, str) and 1 <= len(value) <= 40 and "\n" not in value and "\r" not in value


def queue_item_contract_defaults(item, wiki=None):
    status = item.get("status")
    receipt = item.get("receipt")
    receipt_shape_valid = status == "ok" and valid_caption_receipt(
        receipt,
        expected_url=item.get("url"),
        expected_video_id=item.get("video_id"),
    )
    receipt_valid = receipt_shape_valid
    if receipt_valid and wiki is not None:
        try:
            validate_caption_receipt(
                receipt,
                wiki=wiki,
                expected_url=item.get("url"),
                expected_video_id=item.get("video_id"),
                verify_files=True,
            )
        except (OSError, TypeError, ValueError):
            receipt_valid = False
    receipt_files = [PurePosixPath(entry["path"]).name for entry in receipt.get("files", [])] if receipt_valid else []
    files = item.get("files") if isinstance(item.get("files"), list) else []
    files_match_receipt = files == receipt_files
    caption_valid = receipt_valid and files_match_receipt
    metadata_state = item.get("metadata_state")
    if metadata_state not in {"absent", "acquired"}:
        metadata_state = "acquired" if isinstance(item.get("metadata"), dict) else "absent"
    provenance_class = "caption" if caption_valid else "metadata" if metadata_state == "acquired" else "none"
    evidence_eligible = caption_valid or metadata_state == "acquired"
    transcript_eligible = caption_valid and not bool(receipt and receipt.get("translated_from"))
    caption_state = item.get("caption_state")
    if caption_state not in {"pending", "running", "retryable", "verified", "no-captions", "exhausted", "blocked"}:
        caption_state = {
            "ok": "verified",
            "no-captions": "no-captions",
            "exhausted": "exhausted",
            "blocked": "blocked",
            "blocked-install": "blocked",
        }.get(status, "pending")
    reason = {
        "ok": "fresh-regular-vtt" if caption_valid else "caption-receipt-invalid" if receipt is not None else "caption-receipt-missing",
        "no-captions": "no-fresh-caption",
        "metadata-only": "metadata-only-not-transcript",
        "retryable": "caption-retry-scheduled",
        "exhausted": "caption-retries-exhausted",
        "error": "caption-route-failed",
        "blocked": "access-boundary",
        "blocked-install": "installer-approval-required",
    }.get(status, "not-attempted")
    defaults = {
        "provenance_class": provenance_class,
        "evidence_eligible": evidence_eligible,
        "transcript_eligible": transcript_eligible,
        "evidence_reason": reason,
        "attempts": item.get("attempts", []),
        "metadata": item.get("metadata"),
        "receipt": receipt if receipt_shape_valid else None,
        "caption_state": caption_state,
        "metadata_state": metadata_state,
        "next_retry_at": item.get("next_retry_at"),
    }
    if status == "ok" and not caption_valid:
        defaults["files"] = []
        defaults["receipt"] = None
    changed = False
    for key, value in defaults.items():
        if key not in item or item.get(key) != value:
            item[key] = value
            changed = True
    return changed


def valid_metadata(value):
    if value is None:
        return True
    if not isinstance(value, dict) or set(value) - {"title", "uploader", "channel", "upload_date", "description", "duration"}:
        return False
    for field in ("title", "uploader", "channel", "upload_date", "description"):
        if field in value and (not isinstance(value[field], str) or len(value[field]) > 2000 or "\n" in value[field] or "\r" in value[field]):
            return False
    duration = value.get("duration")
    return duration is None or (type(duration) is int and 0 <= duration <= 86400)


def valid_attempts(value):
    if not isinstance(value, list) or len(value) > 4:
        return False
    allowed = {"route", "attempt", "returncode", "error_class"}
    for attempt in value:
        if not isinstance(attempt, dict) or set(attempt) - allowed:
            return False
        if attempt.get("route") not in {"caption", "transcript-api", "metadata", "audio", "transcription"}:
            return False
        if type(attempt.get("attempt")) is not int or not 1 <= attempt["attempt"] <= 4:
            return False
        returncode = attempt.get("returncode")
        if returncode is not None and (type(returncode) is not int or not -255 <= returncode <= 255):
            return False
        error_class = attempt.get("error_class")
        if error_class is not None and (not isinstance(error_class, str) or not 1 <= len(error_class) <= 80 or "\n" in error_class or "\r" in error_class):
            return False
    return True


def validate_queue(data, queue_id, schema_version=None):
    if not isinstance(data, dict):
        return "invalid", "queue record is not an object"
    expected_version = QUEUE_SCHEMA_VERSION if schema_version is None else schema_version
    version = data.get("schema_version")
    if version != expected_version:
        if schema_version is None and isinstance(version, int) and version > QUEUE_SCHEMA_VERSION:
            return "future-schema", "queue schema is newer than this runtime"
        return "invalid", "unsupported queue schema"
    if data.get("queue_id") != queue_id or not queue_timestamp(data.get("created_at")) or not queue_timestamp(data.get("updated_at")):
        return "invalid", "queue metadata is invalid"
    items = data.get("items")
    if not isinstance(items, list) or len(items) > 1000:
        return "invalid", "queue items are invalid"
    contract_fields = {"provenance_class", "evidence_eligible", "transcript_eligible", "evidence_reason", "attempts", "metadata"}
    allowed = {
        "url",
        "video_id",
        "status",
        "attempt",
        "retry_count",
        "created_at",
        "updated_at",
        "started_at",
        "terminal_at",
        "lease_until",
        "helper_status",
        "error_class",
        "files",
        "receipt",
        "caption_state",
        "metadata_state",
        "next_retry_at",
    }
    if expected_version in {QUEUE_LEGACY_SCHEMA_VERSION, QUEUE_PREVIOUS_SCHEMA_VERSION, QUEUE_SCHEMA_VERSION}:
        allowed |= contract_fields
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) - allowed:
            return "invalid", "queue item shape is invalid"
        try:
            video_id, canonical = canonical_video(item["url"])
        except (KeyError, TypeError, ValueError):
            return "invalid", "queue item URL is invalid"
        if canonical != item.get("url") or video_id != item.get("video_id") or canonical in seen:
            return "invalid", "queue item URL is not canonical or unique"
        seen.add(canonical)
        if item.get("status") not in QUEUE_STATES and not (expected_version in {1, 2} and item.get("status") == "metadata-only"):
            return "invalid", "queue item status is invalid"
        if type(item.get("attempt")) is not int or not 0 <= item["attempt"] <= 1000:
            return "invalid", "queue item attempt is invalid"
        if type(item.get("retry_count")) is not int or not 0 <= item["retry_count"] <= 1000:
            return "invalid", "queue item retry count is invalid"
        if not queue_timestamp(item.get("created_at")) or not queue_timestamp(item.get("updated_at")):
            return "invalid", "queue item timestamp is invalid"
        if not queue_timestamp(item.get("started_at"), required=False) or not queue_timestamp(item.get("terminal_at"), required=False):
            return "invalid", "queue item lifecycle timestamp is invalid"
        lease_until = item.get("lease_until")
        if lease_until is not None and (type(lease_until) not in (int, float) or not math.isfinite(lease_until) or lease_until < 0):
            return "invalid", "queue item lease is invalid"
        next_retry_at = item.get("next_retry_at")
        if next_retry_at is not None and (type(next_retry_at) not in (int, float) or not math.isfinite(next_retry_at) or next_retry_at < 0):
            return "invalid", "queue item retry schedule is invalid"
        if item.get("caption_state") is not None and item.get("caption_state") not in {"pending", "running", "retryable", "verified", "no-captions", "exhausted", "blocked"}:
            return "invalid", "queue item caption state is invalid"
        if item.get("metadata_state") is not None and item.get("metadata_state") not in {"absent", "acquired"}:
            return "invalid", "queue item metadata state is invalid"
        for field in ("helper_status", "error_class"):
            value = item.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > 120 or "\n" in value or "\r" in value):
                return "invalid", "queue item diagnostic is invalid"
        files = item.get("files")
        if not isinstance(files, list) or len(files) > 20 or any(
            not isinstance(name, str) or not name or len(name) > 255 or Path(name).name != name for name in files
        ):
            return "invalid", "queue item files are invalid"
        receipt = item.get("receipt")
        if receipt is not None and not valid_caption_receipt(
            receipt,
            expected_url=item["url"],
            expected_video_id=item["video_id"],
        ):
            return "invalid", "queue item receipt is invalid"
        receipt_files = [PurePosixPath(entry["path"]).name for entry in receipt["files"]] if receipt else []
        if receipt and files != receipt_files:
            return "invalid", "queue item receipt files do not match"
        present = contract_fields & set(item)
        if expected_version == QUEUE_SCHEMA_VERSION and present != contract_fields:
            return "invalid", "queue item evidence contract is incomplete"
        if present and present != contract_fields:
            return "invalid", "queue item evidence contract is incomplete"
        if present:
            legacy_unreceipted = item["status"] == "ok" and receipt is None
            caption_bound = item["status"] == "ok" and ((receipt is not None) or legacy_unreceipted) and bool(files)
            expected_provenance = "caption" if caption_bound else "metadata" if (item.get("metadata_state") == "acquired" or (expected_version < QUEUE_SCHEMA_VERSION and item["status"] == "metadata-only")) else "none"
            if item["provenance_class"] != expected_provenance or item["provenance_class"] not in PROVENANCE_CLASSES:
                return "invalid", "queue item provenance is invalid"
            if type(item["evidence_eligible"]) is not bool or type(item["transcript_eligible"]) is not bool:
                return "invalid", "queue item evidence eligibility is invalid"
            translated = bool(receipt and receipt.get("translated_from"))
            expected_evidence = caption_bound or item.get("metadata_state") == "acquired" or (expected_version < QUEUE_SCHEMA_VERSION and item["status"] == "metadata-only")
            expected_transcript = caption_bound and not translated
            if item["evidence_eligible"] != expected_evidence or item["transcript_eligible"] != expected_transcript:
                return "invalid", "queue item evidence eligibility is inconsistent"
            if not isinstance(item["evidence_reason"], str) or not 1 <= len(item["evidence_reason"]) <= 120 or "\n" in item["evidence_reason"] or "\r" in item["evidence_reason"]:
                return "invalid", "queue item evidence reason is invalid"
            if not valid_attempts(item["attempts"]) or not valid_metadata(item["metadata"]):
                return "invalid", "queue item evidence metadata is invalid"
        if expected_version == QUEUE_SCHEMA_VERSION:
            if {"caption_state", "metadata_state", "next_retry_at"} - set(item):
                return "invalid", "queue item lifecycle contract is incomplete"
            state_pairs = {
                "retryable": "retryable",
                "exhausted": "exhausted",
                "ok": "verified",
                "no-captions": "no-captions",
                "blocked": "blocked",
            }
            expected_caption_state = state_pairs.get(item["status"])
            if expected_caption_state and item["caption_state"] != expected_caption_state:
                return "invalid", "queue item lifecycle state is inconsistent"
    return "valid", None


def read_queue(path, queue_id):
    path = Path(path)
    if path.is_symlink():
        return "invalid", None, "queue record is a symlink"
    if not path.exists():
        return "missing", None, None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return "invalid", None, "queue record is unreadable"
    if isinstance(data, dict) and data.get("schema_version") in {QUEUE_LEGACY_SCHEMA_VERSION, QUEUE_PREVIOUS_SCHEMA_VERSION}:
        status, error = validate_queue(data, queue_id, data.get("schema_version"))
        return ("legacy-schema", data, None) if status == "valid" else ("invalid", None, error)
    status, error = validate_queue(data, queue_id)
    return status, data if status == "valid" else None, error


def migrate_queue(data, queue_id):
    version = data.get("schema_version")
    status, error = validate_queue(data, queue_id, version)
    if status != "valid":
        raise QueueError(error or "unsupported queue schema")
    migrated = dict(data)
    migrated["schema_version"] = QUEUE_SCHEMA_VERSION
    migrated["items"] = []
    now = time.time()
    for item in data["items"]:
        migrated_item = dict(item)
        caption_attempts = [attempt for attempt in item.get("attempts", []) if attempt.get("route") == "caption"]
        last_caption_error = caption_attempts[-1].get("error_class") if caption_attempts else None
        if item.get("status") == "metadata-only":
            migrated_item["status"] = "no-captions" if last_caption_error == "caption-track-absent" else "retryable"
            migrated_item["caption_state"] = "no-captions" if last_caption_error == "caption-track-absent" else "retryable"
            migrated_item["next_retry_at"] = None if last_caption_error == "caption-track-absent" else now
        elif item.get("status") == "ok":
            migrated_item["caption_state"] = "verified"
            migrated_item["next_retry_at"] = None
        elif item.get("status") == "no-captions":
            migrated_item["caption_state"] = "no-captions"
            migrated_item["next_retry_at"] = None
        else:
            migrated_item["caption_state"] = "exhausted" if item.get("status") == "error" else "pending"
            migrated_item["next_retry_at"] = None
        migrated_item["metadata_state"] = "acquired" if isinstance(item.get("metadata"), dict) else "absent"
        queue_item_contract_defaults(migrated_item)
        migrated["items"].append(migrated_item)
    status, error = validate_queue(migrated, queue_id)
    if status != "valid":
        raise QueueError(error or "queue schema migration failed")
    return migrated


migrate_queue_v1 = migrate_queue


def new_queue_item(url, timestamp):
    video_id, canonical = canonical_video(url)
    return {
        "url": canonical,
        "video_id": video_id,
        "status": "pending",
        "attempt": 0,
        "retry_count": 0,
        "created_at": timestamp,
        "updated_at": timestamp,
        "started_at": None,
        "terminal_at": None,
        "lease_until": None,
        "helper_status": None,
        "error_class": None,
        "files": [],
        "provenance_class": "none",
        "evidence_eligible": False,
        "transcript_eligible": False,
        "evidence_reason": "not-attempted",
        "attempts": [],
        "metadata": None,
        "receipt": None,
        "caption_state": "pending",
        "metadata_state": "absent",
        "next_retry_at": None,
    }


def canonical_urls(values):
    result = []
    seen = set()
    for value in values:
        _, canonical = canonical_video(value)
        if canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result


def ensure_queue(wiki, urls, queue_id):
    wiki = queue_wiki(wiki)
    urls = canonical_urls(urls)
    if not urls:
        raise QueueError("queue requires at least one YouTube URL")
    path, lock_path = queue_paths(wiki, queue_id)
    status, data, error = read_queue(path, queue_id)
    if status in {"future-schema", "invalid"}:
        raise QueueError(error)
    queue_directory(wiki)
    fd, reason = acquire_file_lock(lock_path)
    if fd is None:
        raise QueueError(f"queue lock unavailable: {reason}")
    try:
        status, data, error = read_queue(path, queue_id)
        if status in {"future-schema", "invalid"}:
            raise QueueError(error)
        now = datetime.now(timezone.utc).isoformat()
        if status == "missing":
            data = {
                "schema_version": QUEUE_SCHEMA_VERSION,
                "queue_id": queue_id,
                "created_at": now,
                "updated_at": now,
                "items": [new_queue_item(url, now) for url in urls],
            }
            atomic_queue_write(path, data)
        else:
            migrated = status == "legacy-schema"
            if migrated:
                data = migrate_queue(data, queue_id)
            existing = {item["url"] for item in data["items"]}
            additions = [new_queue_item(url, now) for url in urls if url not in existing]
            changed = migrated
            for item in data["items"]:
                changed = queue_item_contract_defaults(item, wiki) or changed
            if additions or changed:
                data["items"].extend(additions)
                data["updated_at"] = now
                atomic_queue_write(path, data)
        return data
    finally:
        release_file_lock(fd)


def queue_summary(data):
    counts = {status: 0 for status in QUEUE_STATES}
    for item in data["items"]:
        counts[item["status"]] += 1
    return {
        "status": "ok",
        "queue_id": data["queue_id"],
        "items": len(data["items"]),
        "pending": counts["pending"],
        "running": counts["running"],
        "terminal": sum(counts[state] for state in QUEUE_TERMINAL_STATES),
        "counts": counts,
        "records": data["items"],
    }


def queue_snapshot(wiki, queue_id):
    wiki = queue_wiki(wiki)
    path, _ = queue_paths(wiki, queue_id)
    status, data, error = read_queue(path, queue_id)
    if status == "valid":
        for item in data["items"]:
            queue_item_contract_defaults(item, wiki)
        return queue_summary(data)
    return {"status": status, "queue_id": queue_id, "error": error}


def claim_queue_items(wiki, queue_id, limit, lease_seconds):
    path, lock_path = queue_paths(wiki, queue_id)
    status, data, error = read_queue(path, queue_id)
    if status not in {"valid", "legacy-schema"}:
        return {"status": status, "queue_id": queue_id, "error": error, "claims": []}
    queue_directory(wiki)
    fd, reason = acquire_file_lock(lock_path)
    if fd is None:
        return {"status": "unavailable", "queue_id": queue_id, "error": reason, "claims": []}
    try:
        status, data, error = read_queue(path, queue_id)
        migrated = status == "legacy-schema"
        if migrated:
            try:
                data = migrate_queue(data, queue_id)
            except QueueError as migration_error:
                return {"status": "invalid", "queue_id": queue_id, "error": str(migration_error), "claims": []}
            status = "valid"
        if status != "valid":
            return {"status": status, "queue_id": queue_id, "error": error, "claims": []}
        now = time.time()
        stamp = datetime.now(timezone.utc).isoformat()
        changed = migrated
        for item in data["items"]:
            changed = queue_item_contract_defaults(item, wiki) or changed
        for item in data["items"]:
            if item["status"] == "running" and item["lease_until"] is not None and item["lease_until"] <= now:
                item["status"] = "pending"
                item["lease_until"] = None
                item["error_class"] = "lease-expired"
                item["updated_at"] = stamp
                changed = True
        claims = []
        for item in data["items"]:
            due = item.get("status") == "pending" or (
                item.get("status") == "retryable"
                and (item.get("next_retry_at") is None or item["next_retry_at"] <= now)
            )
            if not due or len(claims) >= limit:
                continue
            item["status"] = "running"
            item["attempt"] += 1
            item["started_at"] = stamp
            item["updated_at"] = stamp
            item["lease_until"] = now + lease_seconds
            claims.append({
                "url": item["url"],
                "video_id": item["video_id"],
                "attempt": item["attempt"],
                "resume": item.get("error_class") == "interrupted",
            })
            changed = True
        if changed:
            data["updated_at"] = stamp
            atomic_queue_write(path, data)
        return {"status": "ok", "queue_id": queue_id, "claims": claims}
    finally:
        release_file_lock(fd)


def queue_file_names(data, output_dir):
    names = []
    for value in data.get("files", []):
        if not isinstance(value, str):
            continue
        path = Path(value)
        if path.is_absolute():
            if path.parent != output_dir or path != output_dir / path.name:
                continue
        elif path.name != value or path.name in {".", ".."}:
            continue
        candidate = output_dir / path.name
        try:
            mode = candidate.lstat().st_mode
        except OSError:
            continue
        if stat.S_ISREG(mode):
            names.append(path.name)
    return names[:20]


def parse_helper_result(output):
    for line in reversed((output or "").splitlines()):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def fetch_metadata(command, url, timeout):
    if timeout <= 0:
        return None, {
            "route": "metadata",
            "attempt": 1,
            "error_class": "deadline-exhausted",
        }
    process = run_command(command + [
        "--ignore-config",
        "--no-playlist",
        "--no-progress",
        "--skip-download",
        "--dump-single-json",
        url,
    ], timeout)
    if not isinstance(process, subprocess.CompletedProcess):
        return None, {
            "route": "metadata",
            "attempt": 1,
            "error_class": "timeout" if isinstance(process, subprocess.TimeoutExpired) else "process-failed",
        }
    output = f"{process.stdout}\n{process.stderr}"
    attempt = {
        "route": "metadata",
        "attempt": 1,
        "returncode": process.returncode,
        "error_class": error_class(output) if process.returncode else None,
    }
    if process.returncode != 0:
        return None, attempt
    metadata = safe_metadata(parse_helper_result(process.stdout))
    if not metadata:
        attempt["error_class"] = "metadata-unavailable"
        return None, attempt
    return metadata, attempt


def existing_caption_receipt(wiki, claim):
    """Reuse a receipt left behind if interruption happened after acquisition."""
    if claim.get("resume") is not True:
        return None
    try:
        candidates = sorted(receipt_directory(wiki).glob("*.json"))[-100:]
    except OSError:
        return None
    for path in candidates:
        if path.is_symlink() or not RECEIPT_ID.fullmatch(path.stem):
            continue
        try:
            return read_caption_receipt(
                wiki,
                path.stem,
                expected_url=claim["url"],
                expected_video_id=claim["video_id"],
                verify_files=True,
            )
        except (OSError, TypeError, ValueError):
            continue
    return None


def run_queue_item(claim, workspace, wiki, deadline):
    existing = existing_caption_receipt(wiki, claim)
    if existing:
        return {
            "state": "ok",
            "helper_status": "receipt-reused",
            "error_class": None,
            "files": [PurePosixPath(entry["path"]).name for entry in existing["files"]],
            "provenance_class": "caption",
            "evidence_eligible": True,
            "transcript_eligible": True,
            "evidence_reason": "fresh-regular-vtt",
            "attempts": [],
            "metadata": None,
            "receipt": existing,
            "retryable": False,
            "caption_state": "verified",
        }
    remaining = deadline - time.monotonic()
    if remaining <= TIMEOUT_MIN:
        return {"defer": True, "error_class": "drain-deadline"}
    timeout = min(DRAIN_PER_URL_TIMEOUT, max(TIMEOUT_MIN, math.floor(remaining - 0.05)))
    output_dir = (wiki / "inbox" / "youtube").resolve()
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "captions",
        claim["url"],
        "--attempts",
        "1",
        "--backoff",
        "0",
        "--timeout",
        str(timeout),
        "--output-dir",
        str(output_dir),
    ]
    process = run_command(command, timeout, env=os.environ.copy(), cwd=str(workspace))
    if not isinstance(process, subprocess.CompletedProcess):
        error = "timeout" if isinstance(process, subprocess.TimeoutExpired) else "process-failed"
        return {"state": "retryable", "helper_status": "error", "error_class": error, "files": [], "retryable": True, "caption_state": "retryable"}
    data = parse_helper_result(process.stdout)
    if not data or data.get("url") != claim["url"] or data.get("video_id") != claim["video_id"]:
        return {"state": "error", "helper_status": "error", "error_class": "invalid-helper-output", "files": []}
    helper_status = safe_text(str(data.get("status") or "unknown"), 80)
    receipt = None
    receipt_error = None
    if helper_status == "ok":
        candidate = data.get("receipt")
        receipt_id = candidate.get("receipt_id") if isinstance(candidate, dict) else None
        try:
            receipt = read_caption_receipt(
                wiki,
                receipt_id,
                expected_url=claim["url"],
                expected_video_id=claim["video_id"],
                verify_files=True,
            )
        except (OSError, TypeError, ValueError) as error:
            receipt_error = safe_text(str(error), 120)
    files = [PurePosixPath(entry["path"]).name for entry in receipt["files"]] if receipt else queue_file_names(data, output_dir)
    state = {
        "ok": "ok",
        "no-captions": "no-captions",
        "stale-captions-ignored": "no-captions",
        "metadata-only": "metadata-only",
        "blocked": "blocked",
        "install-approval-required": "blocked-install",
    }.get(helper_status, "error")
    error_class = data.get("error_class")
    if not error_class and isinstance(data.get("attempts"), list) and data["attempts"]:
        error_class = data["attempts"][-1].get("error_class")
    error_class = safe_text(str(error_class), 80) if error_class else None
    caption_attempts = [attempt for attempt in data.get("attempts", []) if attempt.get("route") == "caption"]
    caption_error = caption_attempts[-1].get("error_class") if caption_attempts else error_class
    if helper_status == "metadata-only" and caption_error:
        error_class = safe_text(str(caption_error), 80)
    if helper_status == "metadata-only":
        state = "no-captions" if caption_error == "caption-track-absent" else "retryable"
    contract_status = helper_status if helper_status in RESULT_STATUSES else "error"
    if contract_status == "ok" and (not receipt or not files):
        contract_status = "error"
        state = "error"
        error_class = "invalid-caption-receipt"
        files = []
        if receipt_error:
            data["error"] = receipt_error
    contract = result_contract(
        contract_status,
        claim["video_id"],
        claim["url"],
        files=files,
        attempts=data.get("attempts"),
        existing_files_ignored=data.get("existing_files_ignored"),
        error_class=error_class,
        error=data.get("error"),
        metadata=data.get("metadata"),
        receipt=receipt,
    )
    return {
        "state": state,
        "helper_status": helper_status,
        "error_class": contract["error_class"],
        "files": files,
        "provenance_class": contract["provenance_class"],
        "evidence_eligible": contract["evidence_eligible"],
        "transcript_eligible": contract["transcript_eligible"],
        "evidence_reason": contract["evidence_reason"],
        "attempts": contract["attempts"],
        "metadata": contract["metadata"],
        "receipt": contract["receipt"],
        "retryable": state == "retryable" or (state == "error" and error_class in RETRYABLE_CAPTION_ERRORS),
        "caption_state": "verified" if state == "ok" else "no-captions" if state == "no-captions" else "blocked" if state == "blocked" else "retryable" if state == "retryable" else "exhausted",
    }


def finish_queue_claim(wiki, queue_id, claim, outcome):
    path, lock_path = queue_paths(wiki, queue_id)
    fd, reason = acquire_file_lock(lock_path)
    if fd is None:
        return {"status": "unavailable", "error": reason}
    try:
        status, data, error = read_queue(path, queue_id)
        if status != "valid":
            return {"status": status, "error": error}
        item = next((item for item in data["items"] if item["url"] == claim["url"]), None)
        if not item or item["status"] != "running" or item["attempt"] != claim["attempt"]:
            return {"status": "stale-claim"}
        stamp = datetime.now(timezone.utc).isoformat()
        if outcome.get("defer"):
            item["status"] = "pending"
            item["error_class"] = safe_text(str(outcome.get("error_class") or "deferred"), 80)
            item["next_retry_at"] = None
        else:
            state = outcome["state"] if outcome["state"] in {"ok", "no-captions", "retryable", "exhausted", "error", "blocked", "blocked-install"} else "error"
            if outcome.get("retryable"):
                if item["retry_count"] < QUEUE_AUTOMATIC_RETRY_CAP:
                    item["retry_count"] += 1
                    item["status"] = "retryable"
                    item["next_retry_at"] = time.time() + min(QUEUE_RETRY_BASE_SECONDS * (2 ** (item["retry_count"] - 1)), QUEUE_RETRY_MAX_SECONDS)
                    item["terminal_at"] = None
                else:
                    item["status"] = "exhausted"
                    item["next_retry_at"] = None
                    item["terminal_at"] = stamp
            else:
                item["status"] = state if state in QUEUE_TERMINAL_STATES else "error"
                item["next_retry_at"] = None
                item["terminal_at"] = stamp
            item["helper_status"] = outcome.get("helper_status")
            item["error_class"] = outcome.get("error_class")
            item["files"] = outcome.get("files", [])
            item["provenance_class"] = outcome.get("provenance_class", "none")
            item["evidence_eligible"] = outcome.get("evidence_eligible", False)
            item["transcript_eligible"] = outcome.get("transcript_eligible", False)
            item["evidence_reason"] = outcome.get("evidence_reason", "not-available")
            item["attempts"] = outcome.get("attempts", [])
            new_metadata = outcome.get("metadata")
            if new_metadata is not None and valid_metadata(new_metadata):
                item["metadata"] = new_metadata
                item["metadata_state"] = "acquired"
            elif item.get("metadata_state") == "acquired" and valid_metadata(item.get("metadata")):
                pass
            else:
                item["metadata"] = None
                item["metadata_state"] = "absent"
            item["receipt"] = outcome.get("receipt")
            item["caption_state"] = "verified" if item["status"] == "ok" else "no-captions" if item["status"] == "no-captions" else "exhausted" if item["status"] == "exhausted" else "retryable" if item["status"] == "retryable" else "blocked" if item["status"] == "blocked" else outcome.get("caption_state", "blocked")
            item["lease_until"] = None
        item["updated_at"] = stamp
        if outcome.get("defer"):
            item["lease_until"] = None
        data["updated_at"] = stamp
        atomic_queue_write(path, data)
        return {"status": "updated", "state": item["status"]}
    finally:
        release_file_lock(fd)


def drain_queue(
    workspace,
    queue_id,
    deadline_seconds=DRAIN_DEFAULT_DEADLINE,
    concurrency=DRAIN_DEFAULT_CONCURRENCY,
    limit=DRAIN_DEFAULT_LIMIT,
):
    if type(deadline_seconds) not in (int, float) or not math.isfinite(deadline_seconds) or not 1 <= deadline_seconds <= TIMEOUT_MAX:
        raise QueueError("--deadline must be between 1 and 600 seconds")
    if type(concurrency) is not int or not 1 <= concurrency <= 4:
        raise QueueError("--concurrency must be between 1 and 4")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 1000):
        raise QueueError("--limit must be between 1 and 1000")
    wiki = queue_wiki(workspace)
    started = time.monotonic()
    deadline = started + deadline_seconds
    processed = 0
    while limit is None or processed < limit:
        remaining = deadline - time.monotonic()
        if remaining < TIMEOUT_MIN:
            break
        batch_limit = 1 if limit is None else min(1, limit - processed)
        claimed = claim_queue_items(
            wiki,
            queue_id,
            batch_limit,
            max(DRAIN_PER_URL_TIMEOUT + 5, deadline_seconds + 1),
        )
        if claimed["status"] != "ok" or not claimed["claims"]:
            break
        deferred = False
        for claim in claimed["claims"]:
            try:
                outcome = run_queue_item(claim, workspace, wiki, deadline)
            except Exception:
                outcome = {"state": "retryable", "helper_status": "error", "error_class": "worker-failed", "files": [], "retryable": True, "caption_state": "retryable"}
            finish_queue_claim(wiki, queue_id, claim, outcome)
            deferred = deferred or bool(outcome.get("defer"))
            processed += 1
        if deferred:
            break
    result = queue_snapshot(wiki, queue_id)
    result["processed"] = processed
    result["deadline_seconds"] = deadline_seconds
    return result


def recover_queue_leases(workspace, queue_id):
    """Return interrupted claims to pending without changing evidence state."""
    wiki = queue_wiki(workspace)
    path, lock_path = queue_paths(wiki, queue_id)
    status, data, error = read_queue(path, queue_id)
    if status not in {"valid", "legacy-schema"}:
        return {"status": status, "queue_id": queue_id, "error": error, "recovered": 0}
    fd, reason = acquire_file_lock(lock_path)
    if fd is None:
        return {"status": "unavailable", "queue_id": queue_id, "error": reason, "recovered": 0}
    try:
        status, data, error = read_queue(path, queue_id)
        if status == "legacy-schema":
            data = migrate_queue(data, queue_id)
            status = "valid"
        if status != "valid":
            return {"status": status, "queue_id": queue_id, "error": error, "recovered": 0}
        stamp = datetime.now(timezone.utc).isoformat()
        recovered = 0
        for item in data["items"]:
            if item["status"] != "running":
                continue
            item["status"] = "pending"
            item["caption_state"] = "pending"
            item["lease_until"] = None
            item["error_class"] = "interrupted"
            item["updated_at"] = stamp
            recovered += 1
        if recovered:
            data["updated_at"] = stamp
            atomic_queue_write(path, data)
        return {"status": "ok", "queue_id": queue_id, "recovered": recovered}
    finally:
        release_file_lock(fd)


def drain_due_queues(workspace, deadline_seconds=8, limit=2):
    """Bounded SessionStart recovery for all due local YouTube queues."""
    wiki = queue_wiki(workspace)
    directory = wiki / ".sessions" / "wiki-agent-system" / QUEUE_DIRNAME
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError:
        return {"status": "unavailable", "processed": 0}
    started = time.monotonic()
    processed = 0
    for path in paths:
        if processed >= limit or time.monotonic() - started >= deadline_seconds:
            break
        queue_id = path.stem
        if not QUEUE_ID.fullmatch(queue_id):
            continue
        result = drain_queue(workspace, queue_id, max(1, deadline_seconds - (time.monotonic() - started)), concurrency=1, limit=1)
        processed += result.get("processed", 0)
    return {"status": "ok", "processed": processed}


def retry_queue_item(workspace, queue_id, url):
    canonical = canonical_video(url)[1]
    wiki = queue_wiki(workspace)
    path, lock_path = queue_paths(wiki, queue_id)
    status, data, error = read_queue(path, queue_id)
    if status not in {"valid", "legacy-schema"}:
        raise QueueError(error or "queue record unavailable")
    queue_directory(wiki)
    fd, reason = acquire_file_lock(lock_path)
    if fd is None:
        raise QueueError(f"queue lock unavailable: {reason}")
    try:
        status, data, error = read_queue(path, queue_id)
        if status == "legacy-schema":
            data = migrate_queue(data, queue_id)
            status = "valid"
        if status != "valid":
            raise QueueError(error or "queue record unavailable")
        for item in data["items"]:
            queue_item_contract_defaults(item, wiki)
        item = next((item for item in data["items"] if item["url"] == canonical), None)
        if not item:
            raise QueueError("URL is not present in queue")
        if item["status"] not in QUEUE_TERMINAL_STATES:
            raise QueueError("only a terminal URL can receive an explicit retry")
        stamp = datetime.now(timezone.utc).isoformat()
        item["status"] = "pending"
        item["retry_count"] += 1
        item["updated_at"] = stamp
        item["started_at"] = None
        item["terminal_at"] = None
        item["lease_until"] = None
        item["helper_status"] = None
        item["error_class"] = "explicit-retry"
        item["caption_state"] = "pending"
        item["metadata_state"] = "absent"
        item["next_retry_at"] = None
        item["files"] = []
        item["provenance_class"] = "none"
        item["evidence_eligible"] = False
        item["transcript_eligible"] = False
        item["evidence_reason"] = "not-attempted"
        item["attempts"] = []
        item["metadata"] = None
        item["receipt"] = None
        data["updated_at"] = stamp
        atomic_queue_write(path, data)
        return queue_summary(data)
    finally:
        release_file_lock(fd)


def queue_wiki(workspace):
    wiki = nearest_wiki(Path(workspace))
    if not wiki or not valid_wiki(wiki):
        raise QueueError("queue requires a valid local LLM Wiki")
    return wiki


def run_command(command, timeout, env=None, cwd=None):
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
            cwd=cwd,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return error


def module_command(interpreter):
    command = [interpreter, "-m", "yt_dlp", "--ignore-config", "--version"]
    result = run_command(command, 8)
    if isinstance(result, subprocess.CompletedProcess) and result.returncode == 0:
        version = (result.stdout or "").strip().splitlines()[-1:]
        return [interpreter, "-m", "yt_dlp"], version[0] if version else "unknown"
    return None


def locate_ytdlp():
    executable = shutil.which("yt-dlp")
    if executable:
        result = run_command([executable, "--ignore-config", "--version"], 8)
        if isinstance(result, subprocess.CompletedProcess) and result.returncode == 0:
            version = (result.stdout or "").strip().splitlines()[-1:]
            return [executable], version[0] if version else "unknown"

    interpreters = []
    for candidate in (sys.executable, shutil.which("python3"), shutil.which("python")):
        if candidate and candidate not in interpreters:
            interpreters.append(candidate)
    for interpreter in interpreters:
        resolved = module_command(interpreter)
        if resolved:
            return resolved
    return None


def installer():
    interpreter = sys.executable or shutil.which("python3") or shutil.which("python")
    if interpreter:
        return "pip", [
            interpreter,
            "-m",
            "pip",
            "install",
            "--user",
            "--upgrade",
            "--disable-pip-version-check",
            f"yt-dlp=={YTDLP_VERSION}",
        ], None
    return None


def ensure_ytdlp(approve_install):
    resolved = locate_ytdlp()
    if resolved:
        return resolved[0], None
    if not approve_install:
        return None, f"yt-dlp is not installed; explicit approval required for pinned install yt-dlp=={YTDLP_VERSION}"

    selected = installer()
    if not selected:
        return None, "no Python interpreter is available for the pinned yt-dlp installer"

    name, command, environment = selected
    result = run_command(command, 180, environment)
    if isinstance(result, subprocess.CompletedProcess) and result.returncode == 0:
        resolved = locate_ytdlp()
        if resolved:
            return resolved[0], None
        return None, f"{name} completed, but yt-dlp is not visible on PATH or as a Python module"

    if isinstance(result, subprocess.CompletedProcess):
        detail = safe_text(result.stderr or result.stdout)
        return None, f"{name} failed{(': ' + detail) if detail else ''}"
    return None, f"installer failed: {safe_text(str(result))}"


def default_output_dir():
    workspace_wiki = nearest_wiki(Path.cwd())
    if workspace_wiki and valid_wiki(workspace_wiki):
        return workspace_wiki / "inbox" / "youtube"
    return Path.cwd() / ".wiki" / "inbox" / "youtube"


def validate_output_scope(output_dir):
    workspace_wiki = nearest_wiki(Path.cwd())
    if not workspace_wiki or not valid_wiki(workspace_wiki):
        raise ValueError("refusing to write inside a foreign or incomplete .wiki")
    target_wiki = nearest_wiki(output_dir)
    if target_wiki and target_wiki != workspace_wiki:
        raise ValueError("refusing to write inside a foreign or incomplete .wiki")
    allowed_root = (workspace_wiki / "inbox" / "youtube").resolve()
    try:
        allowed_root.relative_to(workspace_wiki)
    except ValueError:
        raise ValueError("active .wiki/inbox/youtube directory must stay inside the active Wiki")
    if output_dir != allowed_root and allowed_root not in output_dir.parents:
        raise ValueError("output directory must be inside the active .wiki/inbox/youtube directory")


def nearest_wiki(path):
    try:
        resolved = path.expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    for candidate in (resolved, *resolved.parents):
        wiki = candidate / ".wiki"
        if wiki.is_dir():
            return wiki.resolve()
    return None


def valid_wiki(wiki):
    return (wiki / "config.md").is_file() and (wiki / "_index.md").is_file() and all(
        (wiki / item).is_dir() for item in ("raw", "wiki")
    )


def validate_options(args):
    if not LANGUAGES.fullmatch(args.languages):
        raise ValueError("--languages contains unsupported characters")
    if not FORMATS.fullmatch(args.sub_format):
        raise ValueError("--sub-format contains unsupported characters")
    if args.sub_format != "vtt":
        raise ValueError("--sub-format must be vtt for evidence")
    if args.attempts < 1 or args.attempts > 3:
        raise ValueError("--attempts must be between 1 and 3")
    if not math.isfinite(args.backoff) or args.backoff < 0 or args.backoff > 30:
        raise ValueError("--backoff must be between 0 and 30 seconds")
    if args.timeout < TIMEOUT_MIN or args.timeout > TIMEOUT_MAX:
        raise ValueError(f"--timeout must be between {TIMEOUT_MIN} and {TIMEOUT_MAX} seconds")


def error_class(text):
    lowered = (text or "").lower()
    if any(marker in lowered for marker in ("no subtitles", "there are no subtitles", "subtitles are not available")):
        return "caption-track-absent"
    if any(marker in lowered for marker in ("sign in", "not a bot", "age-restricted", "private video", "unavailable")):
        return "access-or-availability"
    if any(marker in lowered for marker in ("timed out", "timeout", "connection", "http error", "network")):
        return "network"
    return "yt-dlp-error"


def caption_files(output_dir, video_id, extension):
    pattern = re.compile(rf"^{re.escape(video_id)}.*\.{re.escape(extension)}$")
    try:
        entries = sorted(output_dir.iterdir(), key=lambda path: path.name)
    except OSError:
        return []
    files = []
    for path in entries:
        if not pattern.fullmatch(path.name):
            continue
        try:
            mode = path.lstat().st_mode
        except OSError:
            continue
        if stat.S_ISREG(mode) or stat.S_ISLNK(mode):
            files.append(path)
    return files


def caption_fingerprint(path):
    try:
        metadata = path.lstat()
    except OSError:
        return None
    return metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, stat.S_IFMT(metadata.st_mode)


def caption_snapshot(output_dir, video_id, extension):
    return {
        path: fingerprint
        for path in caption_files(output_dir, video_id, extension)
        for fingerprint in (caption_fingerprint(path),)
        if fingerprint is not None
    }


def changed_caption_files(output_dir, video_id, extension, baseline):
    return [
        path
        for path in caption_files(output_dir, video_id, extension)
        if caption_fingerprint(path) != baseline.get(path)
    ]


def remove_caption_files(paths, output_dir):
    failures = []
    for path in paths:
        if path.parent != output_dir or path != output_dir / path.name:
            failures.append(f"{path}: caption path escaped output directory")
            continue
        try:
            metadata = path.lstat()
            if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)):
                failures.append(f"{path}: caption path is not a regular file or symlink")
                continue
            # `unlink` on the lexical path removes only a symlink, never its target.
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as error:
            failures.append(f"{path}: {safe_text(str(error), 300)}")
    return failures


def _transcript_api_module():
    try:
        return importlib.import_module("youtube_transcript_api")
    except (ImportError, ModuleNotFoundError):
        return None


def _transcript_api_version(module):
    value = getattr(module, "__version__", None)
    if value:
        return str(value)
    try:
        return importlib_metadata.version("youtube-transcript-api")
    except importlib_metadata.PackageNotFoundError:
        return ""


def _track_value(track, name, default=None):
    if isinstance(track, dict):
        return track.get(name, default)
    return getattr(track, name, default)


def _language_rank(code, languages):
    for index, pattern in enumerate(languages.split(",")):
        if pattern.endswith(".*") and code.startswith(pattern[:-2]):
            return index
        if pattern == code:
            return index
    return None


def select_transcript_track(transcript_list, languages, allow_translation=False):
    tracks = list(transcript_list)
    candidates = []
    for track in tracks:
        code = str(_track_value(track, "language_code", ""))
        rank = _language_rank(code, languages)
        if rank is not None:
            candidates.append((rank, bool(_track_value(track, "is_generated", False)), track, None))
    if candidates:
        _, _, track, translated_from = min(candidates, key=lambda value: (value[0], value[1], str(_track_value(value[2], "language_code", ""))))
        return track, translated_from
    if not allow_translation:
        return None, None
    targets = [pattern[:-2] if pattern.endswith(".*") else pattern for pattern in languages.split(",")]
    for target in targets:
        for track in tracks:
            if not _track_value(track, "is_translatable", False):
                continue
            available = _track_value(track, "translation_languages", []) or []
            codes = {str(_track_value(language, "language_code", "")) for language in available}
            if target in codes:
                try:
                    return track.translate(target), str(_track_value(track, "language_code", ""))
                except Exception:
                    return None, None
    return None, None


def _snippet_value(snippet, name, default=None):
    if isinstance(snippet, dict):
        return snippet.get(name, default)
    return getattr(snippet, name, default)


def _vtt_timestamp(seconds):
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0 or seconds > 86400:
        raise ValueError("transcript-api timestamp is invalid")
    milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def normalize_transcript_snippets(fetched):
    if hasattr(fetched, "to_raw_data"):
        snippets = fetched.to_raw_data()
    else:
        snippets = list(fetched)
    if not isinstance(snippets, list) or not 1 <= len(snippets) <= 20000:
        raise ValueError("transcript-api returned no bounded snippets")
    blocks = []
    for index, snippet in enumerate(snippets, 1):
        text = _snippet_value(snippet, "text")
        start = _snippet_value(snippet, "start")
        duration = _snippet_value(snippet, "duration", 0.001)
        if not isinstance(text, str) or not text.strip():
            continue
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
            raise ValueError("transcript-api snippet duration is invalid")
        start_timestamp = _vtt_timestamp(start)
        end_timestamp = _vtt_timestamp(float(start) + max(float(duration), 0.001))
        clean_text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        blocks.append(f"{index}\n{start_timestamp} --> {end_timestamp}\n{clean_text}")
    if not blocks:
        raise ValueError("transcript-api returned empty snippets")
    return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"


def atomic_caption_write(path, content):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("transcript-api output path is a symlink")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".transcript-api-", text=True)
    try:
        with os.fdopen(fd, "w") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def transcript_api_error_class(error):
    name = type(error).__name__
    if name in {"NoTranscriptFound", "TranscriptsDisabled"}:
        return "caption-track-absent"
    if name in {"RequestBlocked", "IpBlocked", "PoTokenRequired", "VideoUnavailable", "VideoUnplayable", "AgeRestricted"}:
        return "access-boundary"
    if name in {"TimeoutError", "ConnectTimeout", "ReadTimeout", "ConnectionError", "YouTubeRequestFailed"}:
        return "network"
    return "transcript-api-parser"


def transcript_api_exit_code(result):
    return 0 if result.get("status") in {"ok", "no-captions"} else 1


def fetch_transcript_api(args):
    video_id, url = canonical_video(args.url)
    validate_options(args)
    output_dir = Path(args.output_dir or default_output_dir()).expanduser().resolve()
    validate_output_scope(output_dir)
    module = _transcript_api_module()
    if module is None:
        return result_contract(
            "error",
            video_id,
            url,
            attempts=[{"route": "transcript-api", "attempt": 1, "error_class": "dependency-unavailable"}],
            error_class="dependency-unavailable",
            error=f"youtube-transcript-api=={TRANSCRIPT_API_VERSION} is not provisioned",
        )
    adapter_version = _transcript_api_version(module)
    if adapter_version != TRANSCRIPT_API_VERSION:
        return result_contract(
            "error",
            video_id,
            url,
            attempts=[{"route": "transcript-api", "attempt": 1, "error_class": "dependency-version"}],
            error_class="dependency-version",
            error=f"youtube-transcript-api=={TRANSCRIPT_API_VERSION} is required",
        )
    try:
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        workspace_wiki = nearest_wiki(Path.cwd())
        if not workspace_wiki or not valid_wiki(workspace_wiki):
            raise ValueError("caption receipts require a valid local LLM Wiki")
        api = module.YouTubeTranscriptApi()
        transcript_list = api.list(video_id)
        track, translated_from = select_transcript_track(transcript_list, args.languages, args.allow_translation)
        attempt = {"route": "transcript-api", "attempt": 1}
        if track is None:
            attempt["error_class"] = "caption-track-absent"
            return result_contract("no-captions", video_id, url, attempts=[attempt])
        fetched = track.fetch()
        content = normalize_transcript_snippets(fetched)
        language_code = str(_track_value(track, "language_code", ""))
        language = str(_track_value(track, "language", language_code))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", language_code):
            raise ValueError("transcript-api track language is invalid")
        output = output_dir / f"{video_id}.{language_code}.transcript-api.vtt"
        if output.exists() or output.is_symlink():
            return result_contract(
                "stale-captions-ignored",
                video_id,
                url,
                attempts=[{"route": "transcript-api", "attempt": 1, "error_class": "existing-output"}],
                existing_files_ignored=[output],
            )
        with caption_lock(workspace_wiki, url, TIMEOUT_MIN):
            if output.exists() or output.is_symlink():
                return result_contract(
                    "stale-captions-ignored",
                    video_id,
                    url,
                    attempts=[{"route": "transcript-api", "attempt": 1, "error_class": "existing-output"}],
                    existing_files_ignored=[output],
                )
            atomic_caption_write(output, content)
            normalized_hash = sha256_file(output)
            metadata = {
                "adapter": "youtube-transcript-api",
                "adapter_version": adapter_version,
                "track_language_code": language_code,
                "track_language": language,
                "track_is_generated": bool(_track_value(track, "is_generated", False)),
                "track_is_translatable": bool(_track_value(track, "is_translatable", False)),
                "translated_from": translated_from,
                "normalized_sha256": normalized_hash,
            }
            try:
                receipt = write_caption_receipt(workspace_wiki, url, video_id, 1, [output], metadata)
            except (OSError, TypeError, ValueError):
                output.unlink(missing_ok=True)
                raise
        return result_contract(
            "ok",
            video_id,
            url,
            files=[output],
            attempts=[attempt],
            receipt=receipt,
            evidence_eligible=True,
            transcript_eligible=translated_from is None,
        )
    except Exception as error:
        error_class_value = transcript_api_error_class(error)
        if error_class_value == "caption-track-absent":
            return result_contract(
                "no-captions",
                video_id,
                url,
                attempts=[{"route": "transcript-api", "attempt": 1, "error_class": error_class_value}],
            )
        if error_class_value == "access-boundary":
            return result_contract(
                "blocked",
                video_id,
                url,
                attempts=[{"route": "transcript-api", "attempt": 1, "error_class": error_class_value}],
                error_class=error_class_value,
                error=safe_text(str(error), 160),
            )
        return result_contract(
            "error",
            video_id,
            url,
            attempts=[{"route": "transcript-api", "attempt": 1, "error_class": error_class_value}],
            error_class=error_class_value,
            error=safe_text(str(error), 160),
        )


def fetch_captions(args):
    video_id, url = canonical_video(args.url)
    validate_options(args)
    adapter_attempts = []
    if args.adapter in {"auto", "transcript-api"}:
        adapter_result = fetch_transcript_api(args)
        if args.adapter == "transcript-api" or adapter_result.get("status") == "ok":
            emit(adapter_result, transcript_api_exit_code(adapter_result))
        if adapter_result.get("error_class") not in {"dependency-unavailable", "dependency-version"}:
            adapter_attempts = adapter_result.get("attempts", [])
    output_dir = Path(args.output_dir or default_output_dir()).expanduser().resolve()
    validate_output_scope(output_dir)
    deadline = time.monotonic() + args.timeout

    command, install_error = ensure_ytdlp(args.approve_install)
    if not command:
        status = "install-approval-required" if not args.approve_install else "installer-failed"
        result = result_contract(status, video_id, url, error_class="installer", error=install_error)
        result["install_version"] = YTDLP_VERSION
        emit(result, 2)

    try:
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as error:
        emit(result_contract("error", video_id, url, error_class="invalid-output-dir", error=str(error)), 2)
    if not output_dir.is_dir():
        emit(result_contract("error", video_id, url, error_class="invalid-output-dir"), 2)

    workspace_wiki = nearest_wiki(Path.cwd())
    if not workspace_wiki or not valid_wiki(workspace_wiki):
        emit(result_contract("error", video_id, url, error_class="invalid-wiki", error="caption receipts require a valid local LLM Wiki"), 2)
    ignored_files = set(caption_snapshot(output_dir, video_id, args.sub_format))
    attempts = adapter_attempts
    caption_status = None
    for attempt in range(1, args.attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            attempts.append({"route": "caption", "attempt": attempt, "error_class": "deadline-exhausted"})
            break
        try:
            with caption_lock(workspace_wiki, url, max(QUEUE_LOCK_TIMEOUT, deadline - time.monotonic())):
                attempt_baseline = caption_snapshot(output_dir, video_id, args.sub_format)
                ignored_files.update(attempt_baseline)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    attempts.append({"route": "caption", "attempt": attempt, "error_class": "deadline-exhausted"})
                    break
                process = run_command(command + [
                    "--ignore-config",
                    "--no-playlist",
                    "--no-progress",
                    "--no-overwrites",
                    "--skip-download",
                    "--write-subs",
                    "--write-auto-subs",
                    "--sub-langs",
                    args.languages,
                    "--sub-format",
                    args.sub_format,
                    "--output",
                    str(output_dir / "%(id)s.%(ext)s"),
                    url,
                ], remaining)
                attempt_files = changed_caption_files(output_dir, video_id, args.sub_format, attempt_baseline)
                if isinstance(process, subprocess.CompletedProcess):
                    output = f"{process.stdout}\n{process.stderr}"
                    attempts.append({
                        "route": "caption",
                        "attempt": attempt,
                        "returncode": process.returncode,
                        "error_class": error_class(output) if process.returncode else None,
                    })
                    if process.returncode == 0:
                        symlink_files = [path for path in attempt_files if path.is_symlink()]
                        if symlink_files:
                            cleanup_failures = remove_caption_files(attempt_files, output_dir)
                            if cleanup_failures:
                                emit(result_contract(
                                    "error",
                                    video_id,
                                    url,
                                    attempts=attempts,
                                    error_class="caption-cleanup",
                                    error=f"failed to reject symlink caption output: {'; '.join(cleanup_failures)}",
                                ), 1)
                            emit(result_contract(
                                "error",
                                video_id,
                                url,
                                attempts=attempts,
                                error_class="caption-symlink",
                                error="caption output contains a symlink",
                            ), 1)
                        files = []
                        for path in attempt_files:
                            try:
                                if stat.S_ISREG(path.lstat().st_mode):
                                    files.append(path)
                            except OSError:
                                continue
                        if files:
                            try:
                                receipt = write_caption_receipt(workspace_wiki, url, video_id, attempt, files)
                            except (OSError, TypeError, ValueError) as error:
                                cleanup_failures = remove_caption_files(attempt_files, output_dir)
                                detail = safe_text(str(error), 160)
                                if cleanup_failures:
                                    detail = f"{detail}; cleanup failed: {'; '.join(cleanup_failures)}"
                                emit(result_contract(
                                    "error",
                                    video_id,
                                    url,
                                    attempts=attempts,
                                    error_class="caption-receipt",
                                    error=detail,
                                ), 1)
                            emit(result_contract("ok", video_id, url, files=files, attempts=attempts, receipt=receipt))
                        caption_status = "stale-captions-ignored" if ignored_files else "no-captions"
                        break
                else:
                    attempts.append({
                        "route": "caption",
                        "attempt": attempt,
                        "error_class": "timeout" if isinstance(process, subprocess.TimeoutExpired) else "process-failed",
                    })
                if attempt_files:
                    cleanup_failures = remove_caption_files(attempt_files, output_dir)
                    ignored_files.difference_update(attempt_files)
                    if cleanup_failures:
                        emit(result_contract(
                            "error",
                            video_id,
                            url,
                            attempts=attempts,
                            error_class="caption-cleanup",
                            error=f"failed to clean captions written by a failed attempt: {'; '.join(cleanup_failures)}",
                        ), 1)
        except QueueError as error:
            attempts.append({"route": "caption", "attempt": attempt, "error_class": "lock-unavailable"})
            emit(result_contract("error", video_id, url, attempts=attempts, error_class="caption-lock", error=str(error)), 1)
        if attempt < args.attempts:
            remaining = deadline - time.monotonic()
            delay = min(args.backoff * attempt, max(0, remaining))
            if delay <= 0:
                break
            time.sleep(delay)

    metadata, metadata_attempt = fetch_metadata(command, url, deadline - time.monotonic())
    if caption_status:
        caption_attempts = [attempt for attempt in attempts if attempt.get("route") == "caption"]
        if caption_attempts and not caption_attempts[-1].get("error_class"):
            caption_attempts[-1]["error_class"] = "caption-track-absent"
    attempts.append(metadata_attempt)
    if metadata:
        emit(result_contract(
            "metadata-only",
            video_id,
            url,
            attempts=attempts,
            existing_files_ignored=sorted(ignored_files) if caption_status else None,
            metadata=metadata,
        ))
    if caption_status:
        emit(result_contract(
            caption_status,
            video_id,
            url,
            attempts=attempts,
            existing_files_ignored=sorted(ignored_files),
        ))
    emit(result_contract(
        "error",
        video_id,
        url,
        attempts=attempts,
        error_class="caption-fetch-failed",
        error="yt-dlp failed after bounded retries",
    ), 1)


def default_transcription_model():
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_home / "llm-wiki" / "models" / "ggml-base.bin"


def regular_file(path, maximum_bytes=None):
    try:
        metadata = Path(path).lstat()
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode) and (
        maximum_bytes is None or 0 < metadata.st_size <= maximum_bytes
    )


def transcription_output(output_dir, video_id):
    return output_dir / f"{video_id}.machine-transcription.txt"


def transcription_provenance_output(transcript):
    return transcript.with_suffix(".json")


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def caption_receipt_path(wiki, receipt_id):
    if not isinstance(receipt_id, str) or not RECEIPT_ID.fullmatch(receipt_id):
        raise ValueError("caption receipt id is invalid")
    return receipt_directory(wiki) / f"{receipt_id}.json"


def write_caption_receipt(wiki, url, video_id, attempt, files, adapter_metadata=None):
    entries = []
    for path in sorted(files or [], key=lambda value: Path(value).name):
        path = Path(path).expanduser()
        if path.is_symlink():
            raise ValueError("caption receipt input is a symlink")
        path = path.resolve()
        try:
            relative = path.relative_to(Path(wiki).expanduser().resolve()).as_posix()
            parts = caption_receipt_parts(relative)
            metadata = path.lstat()
            if receipt_file_path(wiki, relative) != path:
                raise ValueError("caption receipt input is not a lexical Wiki path")
        except (OSError, ValueError):
            raise ValueError("caption receipt input is outside the active Wiki")
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise ValueError("caption receipt input is not a regular VTT")
        before = caption_fingerprint(path)
        digest = sha256_file(path)
        if before is None or before != caption_fingerprint(path):
            raise ValueError("caption changed while creating caption receipt")
        entries.append({"path": PurePosixPath(*parts).as_posix(), "sha256": digest})
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "receipt_id": uuid.uuid4().hex,
        "canonical_url": url,
        "video_id": video_id,
        "status": "ok",
        "provenance_class": "caption",
        "attempt": attempt,
        "attempted_at": datetime.now(timezone.utc).isoformat(),
        "files": entries,
    }
    if adapter_metadata is not None:
        if not isinstance(adapter_metadata, dict):
            raise ValueError("caption adapter metadata is invalid")
        required = {
            "adapter",
            "adapter_version",
            "track_language_code",
            "track_language",
            "track_is_generated",
            "track_is_translatable",
            "translated_from",
            "normalized_sha256",
        }
        if set(adapter_metadata) != required:
            raise ValueError("caption adapter metadata is incomplete")
        receipt["schema_version"] = ADAPTER_RECEIPT_SCHEMA_VERSION
        receipt.update(adapter_metadata)
    if not entries:
        raise ValueError("caption receipt requires at least one VTT")
    path = caption_receipt_path(wiki, receipt["receipt_id"])
    atomic_receipt_write(path, receipt)
    try:
        return read_caption_receipt(
            wiki,
            receipt["receipt_id"],
            expected_url=url,
            expected_video_id=video_id,
            verify_files=True,
        )
    except (OSError, TypeError, ValueError):
        try:
            path.unlink()
        except OSError:
            pass
        raise


def read_caption_receipt(wiki, receipt_id, *, expected_url=None, expected_video_id=None, verify_files=True):
    path = caption_receipt_path(wiki, receipt_id)
    if path.is_symlink() or not path.is_file():
        raise ValueError("caption receipt is missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("caption receipt is malformed") from error
    return validate_caption_receipt(
        data,
        wiki=wiki,
        expected_url=expected_url,
        expected_video_id=expected_video_id,
        verify_files=verify_files,
    )


def atomic_json(path, payload):
    if path.parent.is_symlink() or path.is_symlink():
        raise OSError("provenance path is a symlink")
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".transcription-", text=True)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(payload, file, ensure_ascii=False, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def cleanup_transcript(path, output_dir):
    if path.parent != output_dir or path != output_dir / path.name:
        return "transcript path escaped output directory"
    try:
        metadata = path.lstat()
        if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)):
            return "transcript path is not a regular file or symlink"
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        return safe_text(str(error), 160)
    return None


def cleanup_transcription_artifacts(transcript, output_dir):
    errors = [cleanup_transcript(path, output_dir) for path in (transcript, transcription_provenance_output(transcript))]
    return next((error for error in errors if error), None)


def valid_existing_transcription(transcript, provenance):
    if not regular_file(transcript, MAX_TRANSCRIPT_BYTES) or not regular_file(provenance, MAX_TRANSCRIPT_BYTES):
        return False
    try:
        data = safe_transcription(json.loads(provenance.read_text(encoding="utf-8")))
        return bool(data and data["transcript_sha256"] == sha256_file(transcript))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


def media_file(directory):
    try:
        entries = list(directory.iterdir())
    except OSError:
        return None
    candidates = [path for path in entries if regular_file(path, MAX_TRANSCRIPTION_AUDIO_BYTES)]
    return candidates[0] if len(candidates) == 1 else None


def transcription_binaries():
    ffmpeg = shutil.which("ffmpeg")
    whisper = shutil.which("whisper-cli")
    if not ffmpeg or not whisper:
        missing = ", ".join(name for name, value in (("ffmpeg", ffmpeg), ("whisper-cli", whisper)) if not value)
        return None, f"local STT unavailable: {missing} is not installed"
    return (ffmpeg, whisper), None


def validate_transcription_options(args):
    if args.timeout < TIMEOUT_MIN or args.timeout > TIMEOUT_MAX:
        raise ValueError(f"--timeout must be between {TIMEOUT_MIN} and {TIMEOUT_MAX} seconds")
    if not TRANSCRIPTION_LANGUAGE.fullmatch(args.language):
        raise ValueError("--language must be auto or a short language code")
    if args.max_duration < 1 or args.max_duration > MAX_TRANSCRIPTION_DURATION:
        raise ValueError(f"--max-duration must be between 1 and {MAX_TRANSCRIPTION_DURATION} seconds")


def transcription_error(video_id, url, attempts, error_class, error=None):
    return result_contract(
        "error", video_id, url, attempts=attempts, error_class=error_class,
        error=error, evidence_reason="local-stt-failed",
    )


def transcription_gate(workspace, queue_id, url):
    try:
        wiki = queue_wiki(workspace)
        snapshot = queue_snapshot(workspace, queue_id)
    except (OSError, QueueError) as error:
        raise QueueError(f"local STT requires a readable caption queue: {safe_text(str(error), 120)}") from error
    if snapshot.get("status") != "ok":
        raise QueueError("local STT requires a readable caption queue")
    item = next((candidate for candidate in snapshot["records"] if candidate["url"] == url), None)
    if not item or item["status"] not in {"no-captions", "metadata-only"} or item["transcript_eligible"]:
        raise QueueError("local STT requires a terminal caption miss for this video")
    return wiki


def transcribe(args):
    """Download public audio into a private temporary directory and run local STT.

    This command is intentionally not called from the prompt hook: an agent owns
    the longer bounded workflow after a terminal caption result.
    """
    video_id, url = canonical_video(args.url)
    validate_transcription_options(args)
    wiki = transcription_gate(args.workspace, args.queue_id, url)
    fd, reason = acquire_file_lock(url_lock_path(wiki, url))
    if fd is None:
        emit(transcription_error(video_id, url, [], "stt-lock-unavailable", reason), 2)
    try:
        transcribe_locked(args, video_id, url, wiki)
    finally:
        release_file_lock(fd)


def transcribe_locked(args, video_id, url, wiki):
    output_dir = Path(args.output_dir or default_output_dir()).expanduser().resolve()
    validate_output_scope(output_dir)
    if nearest_wiki(output_dir) != wiki:
        raise QueueError("local STT output must stay in the caption queue Wiki")
    model = Path(args.model or default_transcription_model()).expanduser()
    if not regular_file(model, 2 * 1024 * 1024 * 1024):
        emit(transcription_error(video_id, url, [], "stt-model-unavailable", "local Whisper model is missing or unsafe"), 2)
    binaries, binary_error = transcription_binaries()
    if not binaries:
        emit(transcription_error(video_id, url, [], "stt-unavailable", binary_error), 2)
    command, install_error = ensure_ytdlp(False)
    if not command:
        emit(transcription_error(video_id, url, [], "audio-downloader-unavailable", install_error), 2)
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    transcript = transcription_output(output_dir, video_id)
    provenance = transcription_provenance_output(transcript)
    existing = [path for path in (transcript, provenance) if path.exists() or path.is_symlink()]
    if existing:
        if len(existing) != 2 or not valid_existing_transcription(transcript, provenance):
            emit(transcription_error(video_id, url, [], "stt-existing-output-unsafe"), 2)
        emit(result_contract("machine-transcription-existing", video_id, url, existing_files_ignored=existing), 0)

    deadline = time.monotonic() + args.timeout
    metadata, metadata_attempt = fetch_metadata(command, url, deadline - time.monotonic())
    attempts = [metadata_attempt]
    duration = (metadata or {}).get("duration")
    if isinstance(duration, int) and duration > args.max_duration:
        emit(transcription_error(video_id, url, attempts, "media-too-long", "video exceeds local transcription duration limit"), 2)
    if time.monotonic() >= deadline:
        emit(transcription_error(video_id, url, attempts, "deadline-exhausted"), 1)

    ffmpeg, whisper = binaries
    with tempfile.TemporaryDirectory(prefix="wiki-youtube-stt-") as temporary:
        temporary_path = Path(temporary)
        downloaded = temporary_path / "download.%(ext)s"
        audio = temporary_path / "audio.wav"
        remaining = deadline - time.monotonic()
        process = run_command(command + [
            "--ignore-config", "--no-playlist", "--no-progress", "--no-overwrites",
            "--format", "bestaudio/best", "--output", str(downloaded), url,
        ], remaining)
        attempts.append({"route": "audio", "attempt": 1, "returncode": process.returncode if isinstance(process, subprocess.CompletedProcess) else None, "error_class": None if isinstance(process, subprocess.CompletedProcess) and process.returncode == 0 else "audio-download-failed"})
        source = media_file(temporary_path)
        if not isinstance(process, subprocess.CompletedProcess) or process.returncode or not source:
            emit(transcription_error(video_id, url, attempts, "audio-download-failed", "public audio was unavailable"), 1)
        remaining = deadline - time.monotonic()
        process = run_command([ffmpeg, "-nostdin", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(audio)], remaining)
        attempts.append({"route": "audio", "attempt": 2, "returncode": process.returncode if isinstance(process, subprocess.CompletedProcess) else None, "error_class": None if isinstance(process, subprocess.CompletedProcess) and process.returncode == 0 else "audio-conversion-failed"})
        if not isinstance(process, subprocess.CompletedProcess) or process.returncode or not regular_file(audio, MAX_TRANSCRIPTION_AUDIO_BYTES):
            emit(transcription_error(video_id, url, attempts, "audio-conversion-failed"), 1)
        remaining = deadline - time.monotonic()
        process = run_command([whisper, "-m", str(model), "-f", str(audio), "-l", args.language, "-otxt", "-of", str(transcript.with_suffix(""))], remaining)
        attempts.append({"route": "transcription", "attempt": 1, "returncode": process.returncode if isinstance(process, subprocess.CompletedProcess) else None, "error_class": None if isinstance(process, subprocess.CompletedProcess) and process.returncode == 0 else "stt-failed"})
        if not isinstance(process, subprocess.CompletedProcess) or process.returncode or not regular_file(transcript, MAX_TRANSCRIPT_BYTES):
            cleanup_error = cleanup_transcription_artifacts(transcript, output_dir)
            emit(transcription_error(video_id, url, attempts, "stt-cleanup" if cleanup_error else "stt-failed", cleanup_error), 1)
        try:
            if not transcript.read_text(encoding="utf-8").strip():
                raise ValueError("empty transcript")
        except (OSError, UnicodeError, ValueError) as error:
            cleanup_error = cleanup_transcription_artifacts(transcript, output_dir)
            emit(transcription_error(video_id, url, attempts, "stt-cleanup" if cleanup_error else "stt-invalid-output", str(error)), 1)
        try:
            provenance_data = {
                "schema_version": 1,
                "engine": "whisper-cli",
                "language": args.language,
                "model": model.name,
                "model_sha256": sha256_file(model),
                "transcript_sha256": sha256_file(transcript),
                "duration_seconds": int(duration or 0),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            atomic_json(provenance, provenance_data)
        except (OSError, ValueError) as error:
            cleanup_error = cleanup_transcription_artifacts(transcript, output_dir)
            emit(transcription_error(video_id, url, attempts, "stt-cleanup" if cleanup_error else "stt-provenance-failed", str(error)), 1)
    emit(result_contract("machine-transcription", video_id, url, files=[transcript, provenance], attempts=attempts, transcription=provenance_data))


def self_test():
    assert canonical_video("https://youtu.be/dQw4w9WgXcQ?si=ignored")[0] == "dQw4w9WgXcQ"
    assert canonical_video("https://www.youtube.com/shorts/dQw4w9WgXcQ")[1].endswith("v=dQw4w9WgXcQ")
    assert len(queue_id_for("session", "turn")) == 32
    for incomplete in ((None, None), ("", ""), (None, "turn"), ("session", None)):
        try:
            queue_id_for(*incomplete)
        except QueueError:
            pass
        else:
            raise AssertionError("incomplete queue identity accepted")
    for status in ("no-captions", "metadata-only", "error", "blocked-install"):
        result = result_contract(
            status,
            "dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            metadata={"title": "metadata"} if status == "metadata-only" else None,
        )
        assert {"url", "provenance_class", "evidence_eligible", "transcript_eligible"} <= set(result)
        assert result["transcript_eligible"] is False
    assert canonical_urls(["dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ?si=ignored"]) == [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    ]
    try:
        canonical_video("https://example.com/watch?v=dQw4w9WgXcQ")
    except ValueError:
        pass
    else:
        raise AssertionError("non-YouTube host accepted")
    print("youtube fallback self-test passed")


def build_parser():
    parser = argparse.ArgumentParser(description="Fetch YouTube captions and explicitly agent-owned local transcriptions.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ensure = subparsers.add_parser("ensure", help="check or install yt-dlp")
    ensure.add_argument("--approve-install", action="store_true")

    captions = subparsers.add_parser("captions", help="download manual/automatic captions")
    captions.add_argument("url")
    captions.add_argument("--adapter", choices=("auto", "yt-dlp", "transcript-api"), default="auto")
    captions.add_argument("--allow-translation", action="store_true")
    captions.add_argument("--approve-install", action="store_true")
    captions.add_argument("--output-dir")
    captions.add_argument("--languages", default="id.*,en.*")
    captions.add_argument("--sub-format", default="vtt")
    captions.add_argument("--attempts", type=int, default=3)
    captions.add_argument("--backoff", type=float, default=2)
    captions.add_argument("--timeout", type=int, default=180)

    transcribe_parser = subparsers.add_parser("transcribe", help="agent-owned local Whisper transcription after caption failure")
    transcribe_parser.add_argument("url")
    transcribe_parser.add_argument("--workspace", required=True)
    transcribe_parser.add_argument("--queue-id", required=True)
    transcribe_parser.add_argument("--output-dir")
    transcribe_parser.add_argument("--model")
    transcribe_parser.add_argument("--language", default="id")
    transcribe_parser.add_argument("--timeout", type=int, default=600)
    transcribe_parser.add_argument("--max-duration", type=int, default=MAX_TRANSCRIPTION_DURATION)

    queue = subparsers.add_parser("queue", help="create or extend a per-turn YouTube queue")
    queue.add_argument("--workspace", required=True)
    queue.add_argument("--session-id", required=True)
    queue.add_argument("--turn-id", required=True)
    queue.add_argument("urls", nargs="+")

    drain = subparsers.add_parser("drain", help="bounded automatic drain of a per-turn YouTube queue")
    drain.add_argument("--workspace", required=True)
    drain.add_argument("--queue-id")
    drain.add_argument("--session-id")
    drain.add_argument("--turn-id")
    drain.add_argument("--deadline", "--deadline-seconds", dest="deadline", type=float, default=DRAIN_DEFAULT_DEADLINE)
    drain.add_argument("--concurrency", type=int, default=DRAIN_DEFAULT_CONCURRENCY)
    drain.add_argument("--limit", type=int, default=DRAIN_DEFAULT_LIMIT)

    retry = subparsers.add_parser("retry", help="create an explicit retry attempt for one terminal URL")
    retry.add_argument("--workspace", required=True)
    retry.add_argument("--queue-id")
    retry.add_argument("--session-id")
    retry.add_argument("--turn-id")
    retry.add_argument("url")

    subparsers.add_parser("self-test", help="run local invariants without network access")
    return parser


def cli_queue_id(args):
    if args.queue_id:
        if not QUEUE_ID.fullmatch(args.queue_id):
            raise QueueError("invalid queue id")
        return args.queue_id
    if args.session_id is None or args.turn_id is None:
        raise QueueError("queue id or both session and turn ids are required")
    return queue_id_for(args.session_id, args.turn_id)


def main():
    args = build_parser().parse_args()
    if args.command == "self-test":
        self_test()
        return
    try:
        if args.command == "queue":
            queue_id = queue_id_for(args.session_id, args.turn_id)
            data = ensure_queue(queue_wiki(args.workspace), args.urls, queue_id)
            emit(queue_summary(data))
        if args.command == "drain":
            result = drain_queue(args.workspace, cli_queue_id(args), args.deadline, args.concurrency, args.limit)
            emit(result, 2 if result.get("status") in {"future-schema", "invalid", "unavailable"} else 0)
        if args.command == "retry":
            emit(retry_queue_item(args.workspace, cli_queue_id(args), args.url))
        if args.command == "ensure":
            command, error = ensure_ytdlp(args.approve_install)
            if command:
                emit({"status": "available", "command": command[0]})
            emit({
                "status": "install-approval-required" if not args.approve_install else "installer-failed",
                "install_version": YTDLP_VERSION,
                "error": error,
            }, 2)
        if args.command == "transcribe":
            transcribe(args)
        fetch_captions(args)
    except (ValueError, QueueError) as error:
        emit({"status": "invalid-input", "error": str(error)}, 2)


if __name__ == "__main__":
    main()
