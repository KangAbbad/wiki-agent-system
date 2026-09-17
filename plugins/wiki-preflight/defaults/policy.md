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

## Stop-owned capture

The Stop hook owns the default semantic capture for meaningful workspace work.
At `UserPromptSubmit`, it stores only bounded intent signals and task state.
At `Stop`, a durable prompt or a workspace change causes one atomic,
redacted, pending-curation record from the final assistant message. Missing
final messages, casual prompts without workspace changes, repeated Stop events,
and capture failures abstain without interrupting the user-facing response.

Meaningful work includes an implementation, investigation, research, synthesis,
plan, decision, verification, or changed artifact. A final response should
summarize the outcome and include applicable decisions, artifacts,
verification, sources, confidence, and open questions so optional structured
enrichment can preserve them in the same task record. The hook extracts only
bounded Markdown sections with those names; an unstructured final remains a
bounded outcome capture. No command is required for preservation. Do not
capture trivial replies, raw transcripts, tool output, or secrets.

Hook-owned and optional structured captures share the task identity and merge
into one record without canonicalizing final text. The Stop hook never
interrupts the user-facing response.

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
the end of extraction. A caption is transcript evidence in that context only
when it is shown as `caption_evidence=verified` with its `receipt_id`,
`caption_sha256`, and receipt-bound file list. A path without that verified
receipt is reported as stale/unreceipted and must not be used for transcript
claims.

Every helper and queue result carries the canonical URL, a `provenance_class`
(`caption`, `web-extraction`, `metadata`, `machine-transcription`, or `none`), `evidence_eligible`, and
`transcript_eligible`. Only a fresh regular VTT bound to a valid receipt has
caption and transcript eligibility. `metadata-only` may support metadata
claims only; `no-captions`, stale/unreceipted captions, failures, and blocked
installation are not transcript evidence and must not be used to synthesize
transcript facts. Local STT is always labeled `machine-transcription` with
both eligibility flags false.

Evidence lifecycle is separate from capture lifecycle:
`acquired → unverified → verifying → verified | exhausted | blocked`.
`web-extraction` preserves public content with its retrieval method, timestamp,
URL, and hash, but never becomes an official caption. Report the provenance
class and lifecycle status exactly as stored.

Public-source verification has two independent gates. A source host is never an
authority merely because it appeared in the prompt or contains words such as
`docs`, `vendor`, or `official`; an authority binding must be explicit
(`authority_host=<public-host>`) or supplied by a trusted caller. `verified`
also requires bounded claim-to-evidence matching in the acquired page content.
Host matching alone is always `unverified`.

When a direct URL is missing, fails, or does not support the claim, the agent
uses a bounded public discovery ladder: one public search, up to three result
pages, and the same total deadline. Discovered pages are persisted as
`web-extraction` evidence and remain `unverified` unless both gates pass.
Pending and exhausted retryable records are drained by the bounded scheduled
SessionStart worker; a new user prompt is not required.

When more valid URLs are present than the bounded immediate batch, the
remaining URLs enter the agent-owned automatic queue drain during the same
task. No user-side shell action or repeated prompt is required. Hook-owned
processing uses the provisioned stable runtime internally; no terminal command
is part of this workflow.

The hook gives both drain passes one shared 40-second YouTube budget, below the
45-second UserPromptSubmit limit. Each URL has one total helper deadline;
caption attempts, bounded backoff, and the permitted metadata route share it,
so metadata can use only the time remaining after captions and cannot extend
the URL or hook deadline.

Queue records use schema 2 for the evidence contract. A valid schema-1 record
is migrated to schema 2 in memory and replaced atomically under the queue lock.
Older runtimes treat schema 2 as a future schema and leave it untouched.

If the result is `install-approval-required`, pause that URL and report the
bounded blocked status. The automatic hook never installs a dependency or
claims a caption that was not acquired.

The approved installer uses the current Python interpreter's user site and the
exact package pin `yt-dlp==2026.08.19`; it does not run Homebrew or an unpinned
package install. If Python's user-site installation is unavailable, fail loudly
and report the dependency as unavailable without delegating routine setup.

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

Local STT is an agent-owned fallback after a terminal caption miss, never a
`UserPromptSubmit` or Stop-hook action. It uses only public audio, private
temporary media, installed `ffmpeg` and `whisper-cli`, and a user-private GGML
model. It never installs binaries, downloads a model, sends media to a cloud
service, supplies cookies, or bypasses access controls. Its output is a fresh
`.wiki/inbox/youtube/**/VIDEO.machine-transcription.txt` record with
`provenance_class=machine-transcription`, `evidence_eligible=false`, and
`transcript_eligible=false`; it is usable only when explicitly characterized as
machine transcription, never as an official caption or attributable source.
If prerequisites are absent, report `machine-transcription` as unavailable;
never expose a shell command or invent a transcript.

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
access controls. Record the helper's JSON status, receipt ID, source hash, and
receipt-bound caption file paths in the Wiki evidence provenance.
Canonicalization of a caption requires that receipt ID; metadata-only,
stale/unreceipted captions, and machine transcription cannot satisfy that gate.

## Evidence-aware final reports

Final reports must state acquired sources, provenance class, evidence lifecycle,
and confidence as facts. Public vendor, database, documentation, and
provenance gaps remain agent-owned: continue bounded public lookup and durable
retry when possible, then report `unverified` or `exhausted`. Never write
`Skipped`, `verify later`, `please verify`, or an equivalent routine user task.

Ask the user for one precise authority only when the missing boundary is a
required private credential or source, access control, or destructive production verification,
or an explicit authority decision. Name that boundary and keep the rest of the
report bounded; do not expose runtime paths, secrets, raw transcripts, or tool
output.
