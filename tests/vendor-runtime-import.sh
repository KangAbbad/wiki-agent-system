#!/bin/sh
set -eu

runtime_root=${1:?runtime root is required}
manifest="$runtime_root/vendor/MANIFEST.sha256"
test -f "$manifest"
test -f "$runtime_root/vendor/SOURCES.sha256"
(cd "$runtime_root" && shasum -a 256 -c vendor/MANIFEST.sha256 >/dev/null)
grep -Fq 'youtube_transcript_api-1.2.4-py3-none-any.whl' "$runtime_root/vendor/SOURCES.sha256"
for license_file in \
  youtube-transcript-api.LICENSE defusedxml.LICENSE requests.LICENSE \
  charset-normalizer.LICENSE idna.LICENSE.md urllib3.LICENSE.txt certifi.LICENSE; do
  test -f "$runtime_root/vendor/LICENSES/$license_file"
done
"$runtime_root/hooks/launcher.sh" - "$runtime_root/vendor" "$runtime_root" <<'PY'
import importlib
import importlib.metadata
import importlib.util
from pathlib import Path
import sys

runtime = Path(sys.argv[1]).resolve()
plugin_root = Path(sys.argv[2]).resolve()
modules = {
    "youtube_transcript_api": "youtube-transcript-api",
    "defusedxml": "defusedxml",
    "requests": "requests",
    "charset_normalizer": "charset-normalizer",
    "idna": "idna",
    "urllib3": "urllib3",
    "certifi": "certifi",
}
versions = {
    "youtube-transcript-api": "1.2.4",
    "defusedxml": "0.7.1",
    "requests": "2.32.5",
    "charset-normalizer": "3.5.1",
    "idna": "3.20",
    "urllib3": "1.26.20",
    "certifi": "2026.7.22",
}
for module_name, distribution_name in modules.items():
    module = importlib.import_module(module_name)
    module_path = Path(module.__file__).resolve()
    assert runtime in module_path.parents, (module_name, module_path, runtime)
    assert importlib.metadata.version(distribution_name) == versions[distribution_name]
spec = importlib.util.spec_from_file_location("youtube_fallback", plugin_root / "scripts" / "youtube_fallback.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
loaded = adapter._transcript_api_module()
assert loaded is not None
assert adapter._transcript_api_version(loaded) == "1.2.4"
print("vendored runtime import passed")
PY
