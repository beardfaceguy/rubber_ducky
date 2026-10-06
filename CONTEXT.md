# rubber_ducky context

Date: 2026-10-05

## Vocabulary

- **Author / worker** — the repository-capable agent that investigates a task,
  supplies the reviewed artifact, and responds to concerns. It does not accept
  its own ADR.
- **Reviewer** — an independent agent context that challenges the artifact.
  Reviewer protocol approval is not human ADR acceptance.
- **Review Protocol** — the domain-agnostic, three-round negotiation lifecycle
  implemented by the shared core.
- **Reviewed-artifact domain** — the typed request, rebuttal, state, CLI, MCP,
  and skill binding for one artifact kind, currently code or plan.
- **Evidence handoff** — the immutable spec, repository revision, sources,
  findings, alternatives, artifacts, and blockers supplied to an independent
  reviewer.
- **Human acceptance** — the explicit `Accepted`, `Rejected`, or `Superseded`
  status recorded in an ADR file through GitHub review. Automated approval
  cannot create it.
- **rubber_stamp** — the proposed ADR-authoring workflow. It is separate from
  rubber_ducky's reviewer role.

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
- Current domain bindings:
  `src/rubber_ducky/code/` and `src/rubber_ducky/plan/`
- Protocol guidance:
  `skill/references/review-protocol.md`

_Anchors: `src/rubber_ducky/core/models.py::ReviewResponse`,
`src/rubber_ducky/core/lifecycle.py::apply_review_response`,
`src/rubber_ducky/core/service.py::ReviewService`,
`src/rubber_ducky/core/adapters.py::ReviewerAdapter`_
