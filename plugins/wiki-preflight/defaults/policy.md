Repository Wiki policy: read `.wiki/_index.md` before work. Store research,
decisions, plans, reports, and knowledge artifacts in `.wiki/`; reserve `docs/`
for explicit product/developer documentation. Capture session completion in
`.wiki/inbox/autosave/`. Never modify a foreign/incomplete `.wiki/`.
Mutable Wiki Agent System configuration lives in
`~/.config/llm-wiki/wiki-agent-system.json`, never in the versioned plugin cache.
Measure quota against the local `.wiki/`; quarantine expired operational files
only. Never auto-delete or quarantine canonical `raw/`, `wiki/`, or `output/`.
Workspace marker and user configuration migrate only forward. A marker or config
with a newer schema is read-only until a compatible plugin version is installed.

## Semantic finalizer

Before sending the final response for meaningful workspace work, run
`python3 "$PLUGIN_ROOT/scripts/wiki_ambient.py" capture --cwd "$PWD" --outcome
"..."`. Meaningful work includes an implementation, investigation, research,
plan, decision, verification, or changed artifact. Supply every applicable
`--decision`, `--artifact`, `--verification`, `--source`, `--confidence`, and
`--open-question`; use `--kind` to classify the capture. Do not ask the user to save it.
Do not capture trivial replies, raw transcripts, tool output, or secrets.
Repeated semantic captures in one Codex task merge into one pending record;
preserve decisions, artifacts, verification, sources, and open questions.

The Stop hook never interrupts the user-facing response. When a workspace file
changed during the turn and the agent omitted capture, it persists a redacted
structured fallback from the final message. A direct agent capture remains the
richer record. Ordinary replies without workspace changes are not captured.

Captures remain `pending-curation`. Promote material to `.wiki/raw/` only with
supplied source content, an absolute HTTP(S) provenance URL, a title, and a
content hash. Never canonicalize secrets, environment files, or unsupported
claims.
