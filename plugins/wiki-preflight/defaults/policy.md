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
automatically invokes the bundled helper for a bounded set of canonical video
URLs. It uses bounded attempts per URL, does not install anything, and writes only
fresh manual or automatic VTT captions to the current valid Wiki's
`inbox/youtube/` directory. The hook injects the helper's JSON status and new
file paths into the agent context, so a missing transcript is not treated as
the end of extraction. A caption is transcript evidence in that context only
when it is shown as `caption_evidence=verified` with its `receipt_id`,
`caption_sha256`, and receipt-bound file list. A path without that verified
receipt is reported as stale/unreceipted. It may inform a provisional
synthesis only with its actual provenance and confidence; it must not be
presented as an official caption or verified transcript.

Every helper and queue result carries the canonical URL, a `provenance_class`
(`caption`, `web-extraction`, `metadata`, `machine-transcription`, or `none`), `evidence_eligible`, and
`transcript_eligible`. Only a fresh regular VTT bound to a valid receipt has
caption and transcript eligibility. `metadata-only` may support metadata
claims only; `no-captions`, failures, and blocked installation provide no
source material. Stale or unreceipted caption text may inform a provisional
synthesis when it is labeled as unverified and never as an official caption.
Local STT is always labeled `machine-transcription` with both eligibility flags
false and must not be presented as caption evidence.

Evidence lifecycle is separate from capture lifecycle:
`acquired → unverified → verifying → verified | exhausted | blocked`.
`web-extraction` preserves public content with its retrieval method, timestamp,
URL, and hash, but never becomes an official caption. Report the provenance
class and lifecycle status exactly as stored.

Knowledge readiness is separate from evidence strength. When source material is
safely acquired and compiled, report `knowledge_readiness=ready` and use it for
ordinary agent work even when `evidence_status=unverified`; retain provenance,
retrieval details, and confidence. `evidence_status=verified` is an optional
stronger claim-quality label, never the availability gate. Unavailable, empty,
or blocked acquisition is `knowledge_readiness=unready` and must not produce a
source or fabricated synthesis.

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
Pending and due retryable records are drained by the bounded scheduled
SessionStart worker; a new user prompt is not required.

YouTube caption work uses a durable foreground controller. `UserPromptSubmit`
creates or resolves one job for the measured `session_id` + `turn_id` identity,
then runs one bounded foreground worker through due caption attempts. It honors
`next_retry_at`, advances the controller revision for every executed pass, and
ends at a terminal evidence state or bounded `exhausted` result. No user-side
shell action, repeated prompt, daemon, or scheduler is required. `Interrupt`
returns uncompleted claims to `pending` and preserves the same checkpoint.

The controller gives one job a shared 40-second budget, four foreground
passes, one allowlisted action at a time, and the existing per-queue retry cap.
Each URL has one total helper deadline; caption attempts, bounded backoff, and
the permitted metadata route share it, so metadata can use only the time
remaining after captions and cannot extend the URL or job deadline. Its state is
atomic private operational data under `.wiki/.sessions/` and contains no prompt,
transcript, raw stderr, or secret.

Queue records use schema 3 for independent caption and metadata lifecycles. A
valid schema-1 or schema-2 record is migrated forward and replaced atomically
under the queue lock. Older runtimes treat schema 3 as a future schema and
leave it untouched. Caption transport/process failures are `retryable` with a
bounded `next_retry_at`; explicit subtitle absence is `no-captions`; exhausted
retryable failures are `exhausted`. Metadata remains available as a separate
`metadata_state=acquired` claim and never makes transcript evidence eligible.

If the result is `install-approval-required`, pause that URL and report the
bounded blocked status. The automatic hook never installs a dependency or
claims a caption that was not acquired. The foreground worker always honors
`next_retry_at`; it must not force-claim a retry before its backoff is due.

Stop finalization does not gate ordinary acquired knowledge. Receipt validation
and the bounded `youtube-knowledge` artifact remain required only before
claiming the stronger `verified` caption label. That artifact must bind its
hash, queue/source URLs, receipt IDs, every receipt caption hash, claim-to-
evidence references, `provenance_class=caption`, `evidence_status=verified`,
`grounded=true`, and `quality_status=verified`, with the required synthesis
sections. Missing, stale, tampered, unrelated, or duplicate artifacts leave
the knowledge usable with its declared provenance but do not upgrade it to
`verified`; they do not block ordinary Stop capture. `exhausted`, `blocked`,
and empty acquisition never become ready and never justify fabricated source
content. Nonterminal jobs remain bounded and do not create a false ready signal.

The approved installer uses the current Python interpreter's user site and the
exact package pin `yt-dlp==2026.08.19`; it does not run Homebrew or an unpinned
package install. If Python's user-site installation is unavailable, fail loudly
and report the dependency as unavailable without delegating routine setup.

The plugin package vendors the adapter and its pure-Python runtime dependency
bundle under `vendor/`, including upstream license texts, a hash manifest for
every shipped file, and separate source-wheel hashes; provisioning copies that
bundle into stable `PLUGIN_DATA` with the hook code.
The launcher loads the bundle from that stable runtime, so a clean device does
not need package installation or network access for adapter import.

The helper accepts only one YouTube video per invocation, canonicalizes the URL,
validates a 5–600 second total per-URL budget, retries up to three times with bounded
backoff, and never downloads the video. Its `auto` adapter first uses the pinned,
pre-provisioned `youtube-transcript-api==1.2.4` when available: it lists tracks,
selects the configured language with manual tracks ahead of generated tracks,
fetches timestamped snippets, normalizes them to VTT, and binds adapter/version,
track metadata, translation origin, and normalized hash into a schema-2 receipt.
If that optional runtime is absent or mismatched, the helper falls back to
`yt-dlp`; it never installs the adapter at hook runtime. Translated tracks are
explicitly derived reading aids and are not transcript-eligible. `no-captions` means no new caption file
was produced. `stale-captions-ignored` means matching files already existed and
were deliberately excluded; neither status is a transcript. If no captions are
available, run the permitted metadata route only after the caption outcome is
known; a successful route is `metadata-only` and never a transcript. Continue
with audio-to-STT only when media extraction is separately allowed and label
that evidence `machine-transcription`. machine-transcription cannot support transcript claims. If all routes fail, preserve `error` and never invent transcript content.

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
stale/unreceipted captions, and machine transcription cannot satisfy that
stronger caption gate. A ready provisional synthesis must retain the actual
provenance instead of implying official caption evidence.

## Evidence-aware final reports

Final reports must state acquired sources, provenance class, knowledge
readiness, evidence lifecycle, and confidence as facts. Public vendor, database,
documentation, and provenance gaps remain agent-owned: continue bounded public lookup and durable
retry when possible, then report `unverified` or `exhausted`. Never write
`Skipped`, `verify later`, `please verify`, or an equivalent routine user task.

Ask the user for one precise authority only when the missing boundary is a
required private credential or source, access control, or destructive production verification,
or an explicit authority decision. Name that boundary and keep the rest of the
report bounded; do not expose runtime paths, secrets, raw transcripts, or tool
output.

Wiki lint is read-only in plugin workflows: use `llm-wiki lint` for inspection
only and never run `llm-wiki lint --fix`.
