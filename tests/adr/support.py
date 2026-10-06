import json
from hashlib import sha256


def adr_markdown(*, newline: str = "\n", title: str = "Decide") -> str:
    lines = [
        f"# {title}",
        "",
        "Status: Proposed",
        "Date: 2026-10-05",
        "Deciders: Human reviewer (pending)",
        "Consulted: Infrastructure",
        "Informed: Infrastructure",
        "Spec: https://linear.app/example",
        "Supersedes:",
        "Superseded by:",
        "",
        "## Context",
        "",
        "Facts and forces. What should we do?",
        "",
        "## Decision drivers",
        "",
        "- Preserve behavior.",
        "",
        "## Decision",
        "",
        "We will use the proposed boundary.",
        "",
        "## Alternatives",
        "",
        "### A. Shortcut — rejected",
        "",
        "Rejected because it fails the boundary.",
        "",
        "## Consequences",
        "",
        "- Adds a review mode.",
        "",
        "## Confirmation",
        "",
        "- Run tests.",
        "",
        "## Anchors",
        "",
        "- `src/example.py::symbol` — constrains the decision.",
        "",
        "## Revisit when",
        "",
        "The boundary changes.",
        "",
        "## Open questions",
        "",
        "None.",
        "",
    ]
    return newline.join(lines)


def evidence_json(markdown: str, *, revision: str = "abc123") -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "spec": {
                "project_url": "https://linear.app/example/project/example",
                "project_identifier": "P-EXAMPLE-1",
                "problem": "A consequential decision needs review.",
                "in_scope": ["Review one Proposed ADR."],
                "deferred": [],
                "success_measure": "All anchors are independently verified.",
                "open_questions": [],
            },
            "repository": {
                "url": "https://github.com/example/repository",
                "revision": revision,
                "working_tree_state": "clean",
            },
            "sources_read": [
                {
                    "path": "src/example.py",
                    "symbols": ["symbol"],
                    "purpose": "Constrains the decision.",
                }
            ],
            "findings": [
                {
                    "claim": "The existing boundary is explicit.",
                    "anchors": ["src/example.py::symbol"],
                    "verifications": [],
                    "uncertainty": None,
                }
            ],
            "alternatives": [
                {
                    "decision_key": "decision",
                    "name": "Shortcut",
                    "is_obvious_shortcut": True,
                    "is_recommended": False,
                    "drivers_satisfied": [],
                    "drivers_failed": ["independence"],
                    "cost": "Weak verification.",
                },
                {
                    "decision_key": "decision",
                    "name": "Selected boundary",
                    "is_obvious_shortcut": False,
                    "is_recommended": True,
                    "drivers_satisfied": ["independence"],
                    "drivers_failed": [],
                    "cost": "Additional integration.",
                },
            ],
            "artifacts": [
                {
                    "path": "docs/adr/2026-10-05-decision.md",
                    "sha256": sha256(markdown.encode()).hexdigest(),
                }
            ],
            "blockers": [],
        },
        indent=2,
    )
