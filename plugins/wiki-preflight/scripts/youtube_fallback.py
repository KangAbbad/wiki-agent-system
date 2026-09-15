#!/usr/bin/env python3
"""Fetch YouTube captions through yt-dlp with an approval-gated installer."""

import argparse
import json
import math
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse


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


def canonical_video(value):
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


def run_command(command, timeout, env=None):
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
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


def fetch_captions(args):
    video_id, url = canonical_video(args.url)
    validate_options(args)
    output_dir = Path(args.output_dir or default_output_dir()).expanduser().resolve()
    validate_output_scope(output_dir)

    command, install_error = ensure_ytdlp(args.approve_install)
    if not command:
        emit({
            "status": "install-approval-required" if not args.approve_install else "installer-failed",
            "video_id": video_id,
            "url": url,
            "install_version": YTDLP_VERSION,
            "error": install_error,
        }, 2)

    try:
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as error:
        emit({"status": "error", "error_class": "invalid-output-dir", "error": safe_text(str(error))}, 2)
    if not output_dir.is_dir():
        emit({"status": "error", "error_class": "invalid-output-dir"}, 2)

    ignored_files = set(caption_snapshot(output_dir, video_id, args.sub_format))
    attempts = []
    files = []
    for attempt in range(1, args.attempts + 1):
        attempt_baseline = caption_snapshot(output_dir, video_id, args.sub_format)
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
        ], args.timeout)
        attempt_files = changed_caption_files(output_dir, video_id, args.sub_format, attempt_baseline)
        if isinstance(process, subprocess.CompletedProcess):
            output = f"{process.stdout}\n{process.stderr}"
            attempts.append({
                "attempt": attempt,
                "returncode": process.returncode,
                "error_class": error_class(output) if process.returncode else None,
            })
            if process.returncode == 0:
                symlink_files = [path for path in attempt_files if path.is_symlink()]
                if symlink_files:
                    cleanup_failures = remove_caption_files(symlink_files, output_dir)
                    if cleanup_failures:
                        emit({
                            "status": "error",
                            "video_id": video_id,
                            "url": url,
                            "files": [],
                            "attempts": attempts,
                            "cleanup_failures": cleanup_failures,
                            "error": "failed to reject symlink caption output",
                        }, 1)
                    attempt_files = [path for path in attempt_files if path not in symlink_files]
                files = attempt_files
                if files:
                    emit({
                        "status": "ok",
                        "video_id": video_id,
                        "url": url,
                        "files": [str(path) for path in files],
                        "attempts": attempts,
                    })
                emit({
                    "status": "stale-captions-ignored" if ignored_files else "no-captions",
                    "video_id": video_id,
                    "url": url,
                    "files": [],
                    "existing_files_ignored": [str(path) for path in sorted(ignored_files)],
                    "attempts": attempts,
                })
        else:
            attempts.append({
                "attempt": attempt,
                "error_class": "timeout" if isinstance(process, subprocess.TimeoutExpired) else "process-failed",
            })
        if attempt_files:
            cleanup_failures = remove_caption_files(attempt_files, output_dir)
            ignored_files.difference_update(attempt_files)
            if cleanup_failures:
                emit({
                    "status": "error",
                    "video_id": video_id,
                    "url": url,
                    "files": [],
                    "attempts": attempts,
                    "cleanup_failures": cleanup_failures,
                    "error": "failed to clean captions written by a failed attempt",
                }, 1)
        files = []
        if attempt < args.attempts:
            time.sleep(args.backoff * attempt)

    emit({
        "status": "error",
        "video_id": video_id,
        "url": url,
        "files": [],
        "attempts": attempts,
        "error": "yt-dlp failed after bounded retries",
    }, 1)


def self_test():
    assert canonical_video("https://youtu.be/dQw4w9WgXcQ?si=ignored")[0] == "dQw4w9WgXcQ"
    assert canonical_video("https://www.youtube.com/shorts/dQw4w9WgXcQ")[1].endswith("v=dQw4w9WgXcQ")
    try:
        canonical_video("https://example.com/watch?v=dQw4w9WgXcQ")
    except ValueError:
        pass
    else:
        raise AssertionError("non-YouTube host accepted")
    print("youtube fallback self-test passed")


def build_parser():
    parser = argparse.ArgumentParser(description="Fetch YouTube captions through yt-dlp.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ensure = subparsers.add_parser("ensure", help="check or install yt-dlp")
    ensure.add_argument("--approve-install", action="store_true")

    captions = subparsers.add_parser("captions", help="download manual/automatic captions")
    captions.add_argument("url")
    captions.add_argument("--approve-install", action="store_true")
    captions.add_argument("--output-dir")
    captions.add_argument("--languages", default="id.*,en.*")
    captions.add_argument("--sub-format", default="vtt")
    captions.add_argument("--attempts", type=int, default=3)
    captions.add_argument("--backoff", type=float, default=2)
    captions.add_argument("--timeout", type=int, default=180)

    subparsers.add_parser("self-test", help="run local invariants without network access")
    return parser


def main():
    args = build_parser().parse_args()
    if args.command == "self-test":
        self_test()
        return
    try:
        if args.command == "ensure":
            command, error = ensure_ytdlp(args.approve_install)
            if command:
                emit({"status": "available", "command": command[0]})
            emit({
                "status": "install-approval-required" if not args.approve_install else "installer-failed",
                "install_version": YTDLP_VERSION,
                "error": error,
            }, 2)
        fetch_captions(args)
    except ValueError as error:
        emit({"status": "invalid-input", "error": str(error)}, 2)


if __name__ == "__main__":
    main()
