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
Plugin hooks execute from stable `PLUGIN_DATA` after provisioning; an active
task survives removal of the versioned plugin cache during a later upgrade.

## Semantic finalizer

Before sending the final response for meaningful workspace work, run
the bundled semantic finalizer through the installed plugin's stable launcher
contract. Meaningful work includes an implementation, investigation, research,
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

## Git hygiene

For a Git workspace, the hook ensures the local wiki runtime directory
`.wiki/.sessions/` is ignored. When an authorized commit includes related
repository work, include the relevant durable `.wiki/` knowledge with it; never
stage or commit `.wiki/.sessions/`. Do not create commits without user
authorization.

## YouTube evidence fallback

For a prompt that asks to research, ingest, summarize, cite, or transcribe a
YouTube video, `UserPromptSubmit` owns ordinary YouTube ingestion. It
automatically invokes the bundled helper for at most two canonical video URLs.
It uses one short attempt per URL, does not install anything, and writes only
fresh manual or automatic VTT captions to the current valid Wiki's
`inbox/youtube/` directory. The hook injects the helper's JSON status and new
file paths into the agent context, so a missing transcript is not treated as
the end of extraction.

Every helper and queue result carries the canonical URL, a `provenance_class`
(`caption`, `metadata`, or `none`), `evidence_eligible`, and
`transcript_eligible`. Only a fresh regular VTT has caption and transcript
eligibility. `metadata-only` may support metadata claims only;
`no-captions`, stale captions, failures, and blocked installation are not
transcript evidence and must not be used to synthesize transcript facts.

When more valid URLs are present than the bounded immediate batch, the
remaining URLs enter the agent-owned automatic queue drain during the same
task. No user-side shell action or repeated prompt is required. Any internal
invocation uses only the bundled stable launcher contract; do not construct a
separate runtime command in a normal terminal.

The hook gives both drain passes one shared 40-second YouTube budget, below the
45-second UserPromptSubmit limit. Each URL has one total helper deadline;
caption attempts, bounded backoff, and the permitted metadata route share it,
so metadata can use only the time remaining after captions and cannot extend
the URL or hook deadline.

Queue records use schema 2 for the evidence contract. A valid schema-1 record
is migrated to schema 2 in memory and replaced atomically under the queue lock.
Older runtimes treat schema 2 as a future schema and leave it untouched.

If the result is `install-approval-required`, pause that URL and request
explicit approval in the current execution before starting an approved retry.
The automatic hook never grants installation approval.

The approved installer uses the current Python interpreter's user site and the
exact package pin `yt-dlp==2026.08.19`; it does not run Homebrew or an unpinned
package install. If Python's user-site installation is unavailable, fail loudly
and let the user install the pinned package through their device policy.

The helper accepts only one YouTube video per invocation, canonicalizes the URL,
validates a 5–600 second total per-URL budget, retries up to three times with bounded
backoff, and never downloads the video. `no-captions` means no new caption file
was produced. `stale-captions-ignored` means matching files already existed and
were deliberately excluded; neither status is a transcript. If no captions are
available, run the permitted metadata route only after the caption outcome is
known; a successful route is `metadata-only` and never a transcript. Continue
with audio-to-STT only when media extraction is separately allowed and label
that evidence `machine-transcription`. If all routes fail, preserve `error` and
never invent transcript content.

When `yt-dlp` exits non-zero, any new or changed VTT from that attempt is removed
before retry or return. Unchanged pre-existing VTT files are not evidence and are
reported only as ignored stale files. Caption entries are inspected with lexical
metadata; symlinks are rejected as evidence and only the link directly under
the active output directory may be removed.

`--output-dir` is restricted to the active valid Wiki's `.wiki/inbox/youtube/`
directory or a child directory. Arbitrary paths and other Wiki roots return
`invalid-input` before any directory is created.

Do not pass browser cookies, credentials, or arbitrary non-YouTube URLs to the
helper. Do not use it to bypass login, paywall, anti-bot, region, quota, or other
access controls. Record the helper's JSON status and caption file paths in the
Wiki evidence provenance.
