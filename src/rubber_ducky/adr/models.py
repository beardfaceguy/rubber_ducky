"""Concrete ADR review payload models."""

from hashlib import sha256
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from rubber_ducky.core.lifecycle import ReviewState
from rubber_ducky.core.models import (
    UNCHANGED,
    NonEmptyText,
    ProtocolModel,
    RebuttalBase,
    ReviewRequestBase,
)

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ExactText = Annotated[str, Field(min_length=1)]
_REQUIRED_SECTIONS = (
    "## Context",
    "## Decision drivers",
    "## Decision",
    "## Alternatives",
    "## Consequences",
    "## Confirmation",
    "## Anchors",
    "## Revisit when",
    "## Open questions",
)
_REQUIRED_METADATA = (
    "Date:",
    "Deciders:",
    "Consulted:",
    "Informed:",
    "Spec:",
    "Supersedes:",
    "Superseded by:",
)


def _validate_adr_markdown(markdown: str) -> None:
    if not markdown.strip():
        raise ValueError("ADR Markdown cannot be whitespace-only")
    lines = markdown.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError("ADR Markdown requires a level-one title")
    status_lines = [line for line in lines if line.startswith("Status:")]
    if status_lines != ["Status: Proposed"]:
        raise ValueError("ADR Markdown requires exactly one Proposed status")
    missing_metadata = tuple(
        prefix
        for prefix in _REQUIRED_METADATA
        if not any(line.startswith(prefix) for line in lines)
    )
    missing_sections = tuple(
        section for section in _REQUIRED_SECTIONS if lines.count(section) != 1
    )
    if missing_metadata:
        raise ValueError(f"missing ADR metadata: {missing_metadata}")
    if missing_sections:
        raise ValueError(f"missing ADR sections: {missing_sections}")


class AdrEvidenceReference(ProtocolModel):
    """Identity of exact evidence bytes stored with the review audit."""

    schema_version: Literal["1.0"]
    relative_path: NonEmptyText
    sha256: Sha256

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("evidence path must be a contained relative artifact path")
        return value


class AdrReviewRequest(ReviewRequestBase):
    """Initial ADR review request carrying exact Proposed ADR Markdown."""

    adr_markdown: ExactText
    adr_sha256: Sha256
    repository_revision: NonEmptyText
    evidence: AdrEvidenceReference

    @model_validator(mode="after")
    def validate_adr_bytes(self) -> "AdrReviewRequest":
        _validate_adr_markdown(self.adr_markdown)
        actual = sha256(self.adr_markdown.encode("utf-8")).hexdigest()
        if actual != self.adr_sha256:
            raise ValueError("ADR Markdown SHA-256 does not match exact bytes")
        return self

    def payload_heading(self) -> str:
        return "Proposed ADR"

    def payload_text(self) -> str:
        return self.adr_markdown

    def artifact_suffix(self) -> str:
        return "md"


class AdrRebuttal(RebuttalBase):
    """Worker response carrying an optional exact revised ADR."""

    revised_adr_markdown: ExactText | None = None
    revised_adr_sha256: Sha256 | None = None
    repository_revision: NonEmptyText
    revised_evidence: AdrEvidenceReference | None = None

    @model_validator(mode="after")
    def validate_revision(self) -> "AdrRebuttal":
        values = (
            self.revised_adr_markdown,
            self.revised_adr_sha256,
            self.revised_evidence,
        )
        if self.accepts_any_concern() and any(value is None for value in values):
            raise ValueError("accepted concerns require revised ADR and evidence")
        if not self.accepts_any_concern() and any(value is not None for value in values):
            raise ValueError("unchanged rebuttals cannot replace ADR or evidence")
        if self.revised_adr_markdown is not None:
            _validate_adr_markdown(self.revised_adr_markdown)
            actual = sha256(self.revised_adr_markdown.encode("utf-8")).hexdigest()
            if actual != self.revised_adr_sha256:
                raise ValueError("revised ADR SHA-256 does not match exact bytes")
        return self

    def revised_heading(self) -> str:
        return "Revised ADR"

    def revised_text(self) -> str:
        return self.revised_adr_markdown or UNCHANGED

    def revised_artifact_suffix(self) -> str:
        return "md"

    def is_unchanged(self) -> bool:
        return self.revised_adr_markdown is None


class AdrReviewState(ReviewState):
    """Durable review state bound to ADR payload types."""

    request: AdrReviewRequest
    rebuttals: tuple[AdrRebuttal, ...] = Field(default_factory=tuple)

    request_model = AdrReviewRequest
    rebuttal_model = AdrRebuttal


ADR_CHECKPOINT_TYPES: tuple[type, ...] = (
    AdrEvidenceReference,
    AdrReviewRequest,
    AdrRebuttal,
    AdrReviewState,
)


class AdrStartInput(ProtocolModel):
    """Transport input pairing protocol state with exact evidence JSON."""

    request: AdrReviewRequest
    evidence_json: ExactText


class AdrRebuttalInput(ProtocolModel):
    """Transport input pairing an ADR rebuttal with revised evidence bytes."""

    rebuttal: AdrRebuttal
    evidence_json: ExactText | None = None

    @model_validator(mode="after")
    def evidence_matches_revision(self) -> "AdrRebuttalInput":
        if self.rebuttal.is_unchanged() and self.evidence_json is not None:
            raise ValueError("unchanged rebuttal cannot replace evidence")
        if not self.rebuttal.is_unchanged() and self.evidence_json is None:
            raise ValueError("revised ADR requires evidence JSON")
        return self
