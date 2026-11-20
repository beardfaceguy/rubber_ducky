# rubber_ducky context

Date: 2026-10-06

## Vocabulary

- **Author / worker** — the repository-capable agent that investigates a task,
  supplies the reviewed artifact, and responds to concerns. It does not accept
  its own ADR.
- **Reviewer** — an independent agent context that challenges the artifact.
  Reviewer protocol approval is not human ADR acceptance.
- **Review Protocol** — the domain-agnostic, three-round negotiation lifecycle
  implemented by the shared core.
- **Review mode** — the typed request, rebuttal, state, CLI, MCP,
  and skill binding for one artifact kind, currently code or plan.
- **Evidence handoff** — the immutable spec, repository revision, sources,
  findings, alternatives, artifacts, and blockers supplied to an independent
  reviewer.
- **Human acceptance** — the explicit `Accepted`, `Rejected`, or `Superseded`
  status recorded in an ADR file through GitHub review. Automated approval
  cannot create it.
- **rubber_stamp** — the separate human-authored ADR filing application under
  the accepted desktop pivot. The old agent-authoring workflow is Superseded;
  existing code/journals are preserved. rubber_ducky's ADR reviewer mode remains
  independent and unchanged. See the replacement decision in
  `meetalix/rubber_stamp/docs/adr/2026-10-06-human-authored-adr-filing.md`.

## Where

- Protocol values and messages:
  `src/rubber_ducky/core/models.py`
- Legal transitions and terminal states:
  `src/rubber_ducky/core/lifecycle.py`
- Durable journal, replay, and audit coordination:
  `src/rubber_ducky/core/persistence.py` and
  `src/rubber_ducky/core/service.py`
- Reviewer generation boundary:
  `src/rubber_ducky/core/adapters.py`
- Current review-mode bindings:
  `src/rubber_ducky/code/` and `src/rubber_ducky/plan/`
- Protocol guidance:
  `skill/references/review-protocol.md`

_Anchors: `src/rubber_ducky/core/models.py::ReviewResponse`,
`src/rubber_ducky/core/lifecycle.py::apply_review_response`,
`src/rubber_ducky/core/service.py::ReviewService`,
`src/rubber_ducky/core/adapters.py::ReviewerAdapter`_
