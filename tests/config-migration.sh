#!/bin/sh
set -eu

plugin_root=${1:-plugins/wiki-preflight}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
config_home="$test_root/config"
config="$config_home/llm-wiki/wiki-agent-system.json"
mkdir -p "$(dirname "$config")"
python3 - "$plugin_root/defaults/ambient.json" "$config" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
data["schema_version"] = 2
data["retention"].pop("max_bytes")
json.dump(data, open(sys.argv[2], "w"))
PY
XDG_CONFIG_HOME="$config_home" python3 "$plugin_root/scripts/wiki_ambient.py" validate >/dev/null
python3 - "$config" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
assert data["schema_version"] == 3
assert data["retention"]["max_bytes"] > 0
PY
printf '%s\n' 'config migration passed'
