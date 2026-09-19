#!/bin/sh
set -eu

plugin_root=${1:-plugins/wiki-preflight}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

sh "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/vendor-runtime-import.sh" "$plugin_root"

"$plugin_root/hooks/launcher.sh" - "$plugin_root/scripts/youtube_fallback.py" "$test_root/workspace" <<'PY'
import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

script, workspace = map(Path, sys.argv[1:])
workspace.mkdir(parents=True)
wiki = workspace / ".wiki"
(wiki / "raw").mkdir(parents=True)
(wiki / "wiki").mkdir()
(wiki / "inbox").mkdir()
(wiki / "config.md").write_text("# Workspace Wiki\n")
(wiki / "_index.md").write_text("# Workspace Wiki\n")
spec = importlib.util.spec_from_file_location("youtube_fallback", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
os.chdir(workspace)

class Language:
    def __init__(self, code):
        self.language_code = code

class Track:
    def __init__(self, code, language, generated, translatable, snippets):
        self.language_code = code
        self.language = language
        self.is_generated = generated
        self.is_translatable = translatable
        self.translation_languages = [Language("en")] if translatable else []
        self._snippets = snippets

    def fetch(self):
        return self._snippets

    def translate(self, code):
        return Track(code, "English (translated)", True, False, [{"text": "translated", "start": 0, "duration": 1}])

class TranscriptList:
    def __init__(self, tracks):
        self.tracks = tracks

    def __iter__(self):
        return iter(self.tracks)

class Api:
    def __init__(self):
        self.mode = "normal"

    def list(self, video_id):
        return TranscriptList([
            Track("en", "English", True, False, [{"text": "generated", "start": 0, "duration": 1}]),
            Track("id", "Indonesian generated", True, False, [{"text": "generated id", "start": 0, "duration": 1}]),
            Track("id", "Indonesian", False, True, [{"text": "manual id", "start": 1.25, "duration": 1.5}]),
        ])

fake = types.ModuleType("youtube_transcript_api")
fake.__version__ = "1.2.4"
fake.YouTubeTranscriptApi = Api
sys.modules["youtube_transcript_api"] = fake

selected, translated_from = module.select_transcript_track(list(Api().list("dQw4w9WgXcQ")), "id.*,en.*")
assert selected.language == "Indonesian" and translated_from is None
assert module.normalize_transcript_snippets(selected.fetch()).startswith("WEBVTT\n")

args = SimpleNamespace(
    url="https://youtu.be/dQw4w9WgXcQ?si=fixture",
    languages="id.*,en.*",
    sub_format="vtt",
    attempts=1,
    backoff=0,
    timeout=5,
    output_dir=str(wiki / "inbox" / "youtube"),
    allow_translation=False,
)
result = module.fetch_transcript_api(args)
assert result["status"] == "ok", result
assert result["transcript_eligible"] is True
receipt = result["receipt"]
assert receipt["schema_version"] == 2
assert receipt["adapter"] == "youtube-transcript-api"
assert receipt["track_language_code"] == "id"
assert receipt["track_is_generated"] is False
assert receipt["normalized_sha256"] == receipt["files"][0]["sha256"]
output = wiki / "inbox" / "youtube" / result["files"][0]
assert "manual id" in output.read_text()
module.validate_caption_receipt(receipt, wiki=wiki, expected_url=receipt["canonical_url"], verify_files=True)

class TranscriptsDisabled(Exception):
    pass

class DisabledApi:
    def __init__(self):
        pass

    def list(self, video_id):
        raise TranscriptsDisabled("captions disabled")

fake.YouTubeTranscriptApi = DisabledApi
fake.TranscriptsDisabled = TranscriptsDisabled
result = module.fetch_transcript_api(args)
assert result["status"] == "no-captions" and result["transcript_eligible"] is False, result

class RequestBlocked(Exception):
    pass

class BlockedApi:
    def __init__(self):
        pass

    def list(self, video_id):
        raise RequestBlocked("blocked")

fake.YouTubeTranscriptApi = BlockedApi
fake.RequestBlocked = RequestBlocked
result = module.fetch_transcript_api(args)
assert result["status"] == "blocked" and result["error_class"] == "access-boundary", result

class TranslationApi:
    def __init__(self):
        pass

    def list(self, video_id):
        return TranscriptList([Track("ja", "Japanese", False, True, [{"text": "source", "start": 0, "duration": 1}])])

fake.YouTubeTranscriptApi = TranslationApi
translated_values = vars(args).copy()
translated_values.update({"allow_translation": True, "languages": "en.*"})
translated_args = SimpleNamespace(**translated_values)
translated_result = module.fetch_transcript_api(translated_args)
assert translated_result["status"] == "ok", translated_result
assert translated_result["transcript_eligible"] is False
assert translated_result["receipt"]["translated_from"] == "ja"

print("youtube transcript API adapter contract passed")
PY
