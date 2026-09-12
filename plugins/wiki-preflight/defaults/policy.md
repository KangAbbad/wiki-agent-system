Repository Wiki policy: read `.wiki/_index.md` before work. Store research,
decisions, plans, reports, and knowledge artifacts in `.wiki/`; reserve `docs/`
for explicit product/developer documentation. Capture session completion in
`.wiki/inbox/autosave/`. Never modify a foreign/incomplete `.wiki/`.

## Semantic finalizer

Before sending the final response for meaningful workspace work, run
`python3 "$PLUGIN_ROOT/scripts/wiki_ambient.py" capture --cwd "$PWD" --outcome
"..."`. Meaningful work includes an implementation, investigation, research,
plan, decision, verification, or changed artifact. Supply every applicable
`--decision`, `--artifact`, `--verification`, `--source`, `--confidence`, and
`--open-question`; use `--kind` to classify the capture. Do not ask the user to save it.
Do not capture trivial replies, raw transcripts, tool output, or secrets.

The Stop hook receives the final assistant message. It blocks once to require this
finalizer; if the agent still omits it, the hook persists a redacted structured
fallback from that final message. This guarantees preservation, while a direct
agent capture remains the richer record.

Captures remain `pending-curation`. Promote material to `.wiki/raw/` only with
supplied source content, an absolute HTTP(S) provenance URL, a title, and a
content hash. Never canonicalize secrets, environment files, or unsupported
claims.
