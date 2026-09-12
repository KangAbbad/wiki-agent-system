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

The Stop hook is crash/session fallback only. It cannot read the model's final response
and is not a substitute for this semantic finalizer.

Captures remain `pending-curation`. Promote material to `.wiki/raw/` only with
supplied source content, an absolute HTTP(S) provenance URL, a title, and a
content hash. Never canonicalize secrets, environment files, or unsupported
claims.
