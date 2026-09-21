# Knowledge Readiness Goal

## Product rule

The default user outcome is simple: provide a source, then receive durable,
agent-ready knowledge automatically. An agent may use that knowledge for
research, planning, implementation, review, and checklists immediately.

Acquisition quality is metadata, not a default permission gate. A source that
was acquired from public web extraction is usable when its provenance,
retrieval time, and confidence are recorded, even if it has not passed the
stronger verification workflow.

## Separate concepts

| Concept | Meaning | Effect on normal agent work |
| --- | --- | --- |
| `knowledge_readiness=ready` | The source was acquired and compiled into usable knowledge. | Use it immediately. |
| Provenance | How the source was acquired, such as `web-extraction` or `caption`. | Preserve and state it accurately. |
| Evidence lifecycle | Strength of an attributable claim, such as `unverified` or `verified`. | Calibrate language; do not block ordinary synthesis. |
| Verification | Optional stronger validation for official, high-impact, or explicitly verified claims. | Required only for that stronger assertion, never for routine knowledge use. |

`verified` must never mean merely "knowledge exists". Conversely,
`unverified` must never mean "knowledge is unusable".

## Claim calibration contract

Internal provenance, evidence, receipt, queue, lifecycle, and confidence data
remain durable and machine-readable. They calibrate the agent's wording and
automatic gates; they are not ordinary completion content.

Every normal completion uses one of three claim modes:

1. **Source fact** — attribute what the acquired source says.
2. **Agent inference** — label the deduction and keep it proportional to the
   source support.
3. **Actionable recommendation** — state the action and its conditions as a
   recommendation, not as a source fact.

Do not upgrade a limited source into `official`, `verified`, `guaranteed`,
`safe`, or certain wording unless the existing stronger-evidence condition
passes. A ready source remains usable with attributed, conditional wording.

When a real application boundary matters, add at most one short note in this
form: `Usage note: <boundary-specific constraint>.` Use the target version,
deployment environment, destructive operation, or access boundary that
actually applies. The note is context, not a refusal, routine verification
request, or manual recovery workflow.

Agent-owned bounded checks remain mandatory when they can run locally or
against public sources. If no source material was acquired, say so plainly and
do not fabricate source-backed knowledge.

## User experience

- No slash command, repeated prompt, manual retry, receipt request, authority
  selection, or human verification is required for ordinary source-to-knowledge
  work.
- The agent completes bounded acquisition, writes source and compiled knowledge,
  then reports that it is ready to use.
- Provenance and confidence remain internal metadata for claim calibration and
  explicit audit/citation requests; ordinary completions lead with readiness,
  source links, and the applicable claim mode.
- If a source cannot be acquired, the agent records the truthful bounded result;
  it does not fabricate content or ask the user to perform routine recovery.

## Safety boundary

The non-blocking rule does not permit false attribution. A web extraction may
support a paraphrased design pattern, but it must not be presented as an
official vendor statement, an official caption, or proof of a high-impact
external fact unless the relevant stronger evidence exists. These restrictions
apply to claim wording, not to whether the compiled knowledge may be used.
