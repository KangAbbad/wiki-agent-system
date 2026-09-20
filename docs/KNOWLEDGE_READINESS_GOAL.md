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

## User experience

- No slash command, repeated prompt, manual retry, receipt request, authority
  selection, or human verification is required for ordinary source-to-knowledge
  work.
- The agent completes bounded acquisition, writes source and compiled knowledge,
  then reports that it is ready to use.
- Provenance and confidence are concise metadata. They are surfaced when useful,
  not as a blocking workflow or an alarm.
- If a source cannot be acquired, the agent records the truthful bounded result;
  it does not fabricate content or ask the user to perform routine recovery.

## Safety boundary

The non-blocking rule does not permit false attribution. A web extraction may
support a paraphrased design pattern, but it must not be presented as an
official vendor statement, an official caption, or proof of a high-impact
external fact unless the relevant stronger evidence exists. These restrictions
apply to claim wording, not to whether the compiled knowledge may be used.
