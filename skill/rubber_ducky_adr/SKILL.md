---
name: rubber_ducky_adr
description: durable review of Proposed ADR Markdown and immutable evidence
disable-model-invocation: false
---

# Rubber Ducky — ADR Review

Use this mode for a Proposed company ADR plus its versioned evidence JSON.
Read the complete [Review Protocol](../references/review-protocol.md) first.

## Boundaries

- The author and reviewer are separate contexts.
- A separate read-only reviewer independently opens material anchors and
  returns a protocol `ReviewResponse` to the author/caller.
- The reviewer never invokes rubber_ducky tools. The author/caller records the
  returned response.
- Protocol `APPROVE` is automated consensus only. Human acceptance remains an
  explicit GitHub action and ADR status change.
- There is no automatic ADR reviewer-generation operation.

## Artifacts

The initial request carries exact `Status: Proposed` ADR Markdown, its SHA-256,
the repository revision, and an evidence reference. The separate evidence JSON
must match that reference and identify the same revision and ADR hash.

An `ACCEPT` rebuttal carries full revised Proposed ADR Markdown and a new
evidence file/reference. A dispute/clarification leaves both artifacts
unchanged.

## CLI

```text
rubber-ducky-adr --workspace PATH start THREAD SLUG \
  --input request.json --evidence evidence.json
rubber-ducky-adr --workspace PATH status THREAD
rubber-ducky-adr --workspace PATH respond THREAD EVENT --input response.json
rubber-ducky-adr --workspace PATH rebut THREAD EVENT \
  --input rebuttal.json [--evidence evidence.json]
rubber-ducky-adr --workspace PATH resume THREAD EVENT --input summary.json
```

## MCP

- `rubber_ducky_adr_start`
- `rubber_ducky_adr_status`
- `rubber_ducky_adr_respond`
- `rubber_ducky_adr_rebut`
- `rubber_ducky_adr_resume`

All five operations are durable writes; status may repair a lagging checkpoint.
There is intentionally no `rubber_ducky_adr_generate` tool.

## Fallback

If the ADR mode is unavailable, do not route the ADR through plan review.
Record the missing review mode as a blocker and retain the ADR/evidence hashes
for retry after a compatible rubber_ducky installation is available.
