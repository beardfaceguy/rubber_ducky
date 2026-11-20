"""Evidence-aware durable service for ADR reviews."""

import json
import time
from collections.abc import Mapping
from hashlib import sha256
from threading import Lock
from typing import ClassVar

from rubber_ducky.adr.models import (
    ADR_CHECKPOINT_TYPES,
    AdrRebuttal,
    AdrRebuttalInput,
    AdrReviewRequest,
    AdrReviewState,
    AdrStartInput,
)
from rubber_ducky.core.audit import (
    ArtifactConflict,
    ArtifactEvidence,
    AuditIdentityConflict,
    AuditLog,
)
from rubber_ducky.core.lifecycle import ReviewEvent, ReviewState, apply_event
from rubber_ducky.core.models import RebuttalBase, ReviewRequestBase
from rubber_ducky.core.persistence import PersistenceConflict
from rubber_ducky.core.service import ReviewService


class UnsupportedOperation(ValueError):
    """Raised when a caller bypasses evidence-aware ADR operations."""


class AdrReviewService(ReviewService):
    """Durable review service bound to exact ADR and evidence artifacts."""

    request_model: ClassVar[type[ReviewRequestBase]] = AdrReviewRequest
    rebuttal_model: ClassVar[type[RebuttalBase]] = AdrRebuttal
    state_model: ClassVar[type[ReviewState]] = AdrReviewState
    checkpoint_types: ClassVar[tuple[type, ...]] = ADR_CHECKPOINT_TYPES
    _reconcile_lock: ClassVar[Lock] = Lock()

    def start(self, *_args: object, **_kwargs: object) -> AdrReviewState:
        raise UnsupportedOperation("ADR reviews require start_with_evidence")

    def generate_review(self, *_args: object, **_kwargs: object) -> AdrReviewState:
        raise UnsupportedOperation(
            "automatic ADR reviewer generation is not supported"
        )

    def status(self, thread_id: str) -> AdrReviewState:
        with self._reconcile_lock:
            state = super().status(thread_id)
        history = self.store.load_history(thread_id)
        audit = self._open_audit(thread_id, history)
        self._require_artifact(
            audit,
            ArtifactEvidence(
                relative_path=state.request.evidence.relative_path,
                sha256=state.request.evidence.sha256,
            ),
            "evidence",
        )
        self._require_artifact(
            audit,
            ArtifactEvidence(
                relative_path=(
                    f"{audit.key}/artifacts/round-1-request.md"
                ),
                sha256=state.request.adr_sha256,
            ),
            "ADR",
        )
        for rebuttal in state.rebuttals:
            if rebuttal.is_unchanged():
                continue
            assert rebuttal.revised_evidence is not None
            assert rebuttal.revised_adr_sha256 is not None
            self._require_artifact(
                audit,
                ArtifactEvidence(
                    relative_path=rebuttal.revised_evidence.relative_path,
                    sha256=rebuttal.revised_evidence.sha256,
                ),
                "revised evidence",
            )
            self._require_artifact(
                audit,
                ArtifactEvidence(
                    relative_path=(
                        rebuttal.revised_evidence.relative_path.removesuffix(
                            "-evidence.json"
                        )
                        + ".md"
                    ),
                    sha256=rebuttal.revised_adr_sha256,
                ),
                "revised ADR",
            )
        return state

    @staticmethod
    def _require_artifact(
        audit: AuditLog,
        evidence: ArtifactEvidence,
        name: str,
    ) -> None:
        if not audit.verify_artifact(evidence):
            raise ArtifactConflict(f"stored {name} artifact failed verification")

    def submit(
        self,
        thread_id: str,
        event_id: str,
        event: ReviewEvent,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> AdrReviewState:
        if isinstance(event, RebuttalBase):
            raise UnsupportedOperation(
                "ADR rebuttals require submit_adr_rebuttal"
            )
        return super().submit(
            thread_id,
            event_id,
            event,
            metadata=metadata,
        )

    def start_with_evidence(
        self,
        thread_id: str,
        slug: str,
        start_input: AdrStartInput,
    ) -> AdrReviewState:
        request = start_input.request
        evidence_bytes = start_input.evidence_json.encode("utf-8")
        if sha256(evidence_bytes).hexdigest() != request.evidence.sha256:
            raise ArtifactConflict("evidence SHA-256 does not match exact bytes")
        evidence = self._validate_evidence(
            start_input.evidence_json,
            schema_version=request.evidence.schema_version,
            repository_revision=request.repository_revision,
            adr_sha256=request.adr_sha256,
        )
        expected_evidence_path = (
            f"{request.task_id}-{slug}/artifacts/round-1-request-evidence.json"
        )
        if request.evidence.relative_path != expected_evidence_path:
            raise ValueError(
                "evidence reference must use the service-generated artifact path"
            )

        audit = self._open_or_create_audit(
            request.task_id,
            slug,
            thread_id,
        )

        evidence_artifact = audit.store_artifact(
            "round-1-request-evidence.json",
            evidence_bytes,
        )
        if (
            evidence_artifact.relative_path != request.evidence.relative_path
            or evidence_artifact.sha256 != request.evidence.sha256
        ):
            raise PersistenceConflict("stored evidence identity does not match request")
        adr_artifact = audit.store_artifact(
            "round-1-request.md",
            request.adr_markdown.encode("utf-8"),
        )
        if adr_artifact.sha256 != request.adr_sha256:
            raise PersistenceConflict("stored ADR identity does not match request")

        self.store.create_review(thread_id, request, audit_slug=slug)
        if not self.store.is_event_audited(thread_id, "request"):
            audit.append_prepared(
                request,
                event_id="request",
                evidence=adr_artifact,
                audit_metadata={
                    "evidence.path": evidence_artifact.relative_path,
                    "evidence.sha256": evidence_artifact.sha256,
                    "evidence.schema_version": evidence["schema_version"],
                },
            )
            self.store.mark_event_audited(thread_id, "request")
        return self.status(thread_id)

    def _open_or_create_audit(
        self,
        task_id: str,
        slug: str,
        thread_id: str,
    ) -> AuditLog:
        last_incomplete: FileNotFoundError | None = None
        for attempt in range(50):
            try:
                return AuditLog.open(
                    self.workspace_root,
                    task_id,
                    slug,
                    thread_id=thread_id,
                )
            except FileNotFoundError as error:
                last_incomplete = error
                try:
                    return AuditLog.create(
                        self.workspace_root,
                        task_id,
                        slug,
                        thread_id=thread_id,
                    )
                except FileExistsError:
                    if attempt == 49:
                        break
                    time.sleep(0.001)
            except AuditIdentityConflict as error:
                raise PersistenceConflict(str(error)) from error
        assert last_incomplete is not None
        raise last_incomplete

    def submit_adr_rebuttal(
        self,
        thread_id: str,
        event_id: str,
        rebuttal_input: AdrRebuttalInput,
    ) -> AdrReviewState:
        rebuttal = rebuttal_input.rebuttal
        history = self.store.load_history(thread_id)
        for stored in history.events:
            if stored.event_id != event_id:
                continue
            if stored.event != rebuttal:
                raise PersistenceConflict(
                    f"event ID {event_id!r} was reused with different data"
                )
            if rebuttal_input.evidence_json is not None:
                assert rebuttal.revised_evidence is not None
                supplied = rebuttal_input.evidence_json.encode("utf-8")
                if sha256(supplied).hexdigest() != rebuttal.revised_evidence.sha256:
                    raise ArtifactConflict(
                        "evidence SHA-256 does not match exact bytes"
                    )
            state = self.status(thread_id)
            if not self.store.is_event_audited(thread_id, event_id):
                audit = self._open_audit(thread_id, history)
                self._append_rebuttal_audit(audit, event_id, rebuttal)
                self.store.mark_event_audited(thread_id, event_id)
            return state
        state = self.status(thread_id)
        if rebuttal.repository_revision != state.request.repository_revision:
            raise ValueError("rebuttal repository revision does not match request")
        apply_event(state, rebuttal)
        if rebuttal.is_unchanged():
            return super().submit(thread_id, event_id, rebuttal)

        assert rebuttal_input.evidence_json is not None
        assert rebuttal.revised_adr_markdown is not None
        assert rebuttal.revised_adr_sha256 is not None
        assert rebuttal.revised_evidence is not None
        revised_evidence_bytes = rebuttal_input.evidence_json.encode("utf-8")
        if (
            sha256(revised_evidence_bytes).hexdigest()
            != rebuttal.revised_evidence.sha256
        ):
            raise ArtifactConflict("evidence SHA-256 does not match exact bytes")
        evidence = self._validate_evidence(
            rebuttal_input.evidence_json,
            schema_version=rebuttal.revised_evidence.schema_version,
            repository_revision=rebuttal.repository_revision,
            adr_sha256=rebuttal.revised_adr_sha256,
        )
        history = self.store.load_history(thread_id)
        audit = self._open_audit(thread_id, history)
        expected_evidence_path = (
            f"{audit.key}/artifacts/"
            f"round-{rebuttal.round}-{event_id}-evidence.json"
        )
        if rebuttal.revised_evidence.relative_path != expected_evidence_path:
            raise ValueError(
                "evidence reference must use the service-generated artifact path"
            )
        evidence_artifact = audit.store_artifact(
            f"round-{rebuttal.round}-{event_id}-evidence.json",
            revised_evidence_bytes,
        )
        if (
            evidence_artifact.relative_path
            != rebuttal.revised_evidence.relative_path
            or evidence_artifact.sha256 != rebuttal.revised_evidence.sha256
        ):
            raise PersistenceConflict("stored evidence identity does not match rebuttal")
        adr_artifact = audit.store_artifact(
            f"round-{rebuttal.round}-{event_id}.md",
            rebuttal.revised_adr_markdown.encode("utf-8"),
        )
        if adr_artifact.sha256 != rebuttal.revised_adr_sha256:
            raise PersistenceConflict("stored ADR identity does not match rebuttal")

        append_result = self.store.append_event_once(thread_id, event_id, rebuttal)
        if append_result.appended or not self.store.is_event_audited(
            thread_id, event_id
        ):
            self._append_rebuttal_audit(
                audit,
                event_id,
                rebuttal,
                adr_artifact=adr_artifact,
                evidence_artifact=evidence_artifact,
                schema_version=str(evidence["schema_version"]),
            )
            self.store.mark_event_audited(thread_id, event_id)
        updated_history = self.store.load_history(thread_id)
        self._reconcile_graph(thread_id, updated_history, append_result.state)
        return append_result.state

    @staticmethod
    def _append_rebuttal_audit(
        audit: AuditLog,
        event_id: str,
        rebuttal: AdrRebuttal,
        *,
        adr_artifact: ArtifactEvidence | None = None,
        evidence_artifact: ArtifactEvidence | None = None,
        schema_version: str | None = None,
    ) -> None:
        if not rebuttal.is_unchanged() and adr_artifact is None:
            assert rebuttal.revised_evidence is not None
            assert rebuttal.revised_adr_sha256 is not None
            evidence_artifact = ArtifactEvidence(
                relative_path=rebuttal.revised_evidence.relative_path,
                sha256=rebuttal.revised_evidence.sha256,
            )
            adr_artifact = ArtifactEvidence(
                relative_path=(
                    rebuttal.revised_evidence.relative_path.removesuffix(
                        "-evidence.json"
                    )
                    + ".md"
                ),
                sha256=rebuttal.revised_adr_sha256,
            )
            schema_version = rebuttal.revised_evidence.schema_version
        metadata = None
        if evidence_artifact is not None:
            assert schema_version is not None
            metadata = {
                "evidence.path": evidence_artifact.relative_path,
                "evidence.sha256": evidence_artifact.sha256,
                "evidence.schema_version": schema_version,
            }
        audit.append_prepared(
            rebuttal,
            event_id=event_id,
            evidence=adr_artifact,
            audit_metadata=metadata,
        )

    @staticmethod
    def _validate_evidence(
        evidence_json: str,
        *,
        schema_version: str,
        repository_revision: str,
        adr_sha256: str,
    ) -> dict[str, object]:
        try:
            evidence = json.loads(evidence_json)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid evidence JSON: {error}") from error
        if not isinstance(evidence, dict):
            raise ValueError("evidence JSON must contain an object")
        required = {
            "schema_version",
            "spec",
            "repository",
            "sources_read",
            "findings",
            "alternatives",
            "artifacts",
            "blockers",
        }
        missing = required - set(evidence)
        if missing:
            raise ValueError(f"evidence JSON missing sections: {sorted(missing)}")
        unknown = set(evidence) - required
        if unknown:
            raise ValueError(f"evidence JSON has unknown sections: {sorted(unknown)}")
        if evidence["schema_version"] != schema_version:
            raise ValueError("evidence schema version does not match reference")
        spec = evidence["spec"]
        if not isinstance(spec, dict):
            raise ValueError("evidence spec must be an object")
        spec_fields = {
            "project_url",
            "project_identifier",
            "problem",
            "in_scope",
            "deferred",
            "success_measure",
            "open_questions",
        }
        if set(spec) != spec_fields:
            raise ValueError("evidence spec fields do not match schema")
        for field in (
            "project_url",
            "project_identifier",
            "problem",
            "success_measure",
        ):
            if not isinstance(spec.get(field), str) or not spec[field].strip():
                raise ValueError(f"evidence spec requires non-empty {field}")
        for field in ("in_scope", "deferred", "open_questions"):
            if not isinstance(spec.get(field), list) or any(
                not isinstance(item, str) for item in spec[field]
            ):
                raise ValueError(f"evidence spec requires list {field}")
        repository = evidence["repository"]
        if not isinstance(repository, dict) or (
            repository.get("revision") != repository_revision
        ):
            raise ValueError("evidence repository revision does not match request")
        if set(repository) != {"url", "revision", "working_tree_state"}:
            raise ValueError("evidence repository fields do not match schema")
        if not isinstance(repository.get("url"), str) or not repository["url"].strip():
            raise ValueError("evidence repository URL is required")
        if repository.get("working_tree_state") not in {"clean", "dirty"}:
            raise ValueError("evidence working tree state must be clean or dirty")
        sources = evidence["sources_read"]
        if not isinstance(sources, list) or not sources:
            raise ValueError("evidence must declare sources read")
        if any(
            not isinstance(item, dict)
            or set(item) != {"path", "symbols", "purpose"}
            or not isinstance(item.get("path"), str)
            or not item["path"].strip()
            or not isinstance(item.get("symbols"), list)
            or any(
                not isinstance(symbol, str) or not symbol.strip()
                for symbol in item["symbols"]
            )
            or not isinstance(item.get("purpose"), str)
            or not item["purpose"].strip()
            for item in sources
        ):
            raise ValueError("evidence sources read are malformed")
        findings = evidence["findings"]
        if not isinstance(findings, list) or not findings:
            raise ValueError("evidence must contain findings")
        if any(
            not isinstance(item, dict)
            or set(item)
            != {"claim", "anchors", "verifications", "uncertainty"}
            or not isinstance(item.get("claim"), str)
            or not item["claim"].strip()
            or not isinstance(item.get("anchors"), list)
            or not item["anchors"]
            or any(
                not isinstance(anchor, str) or "::" not in anchor
                for anchor in item["anchors"]
            )
            or not isinstance(item.get("verifications"), list)
            or not (
                item.get("uncertainty") is None
                or isinstance(item.get("uncertainty"), str)
            )
            for item in findings
        ):
            raise ValueError("evidence findings are malformed")
        for finding in findings:
            for verification in finding["verifications"]:
                if (
                    not isinstance(verification, dict)
                    or set(verification) != {"command", "exit_code", "summary"}
                    or not isinstance(verification["command"], list)
                    or any(
                        not isinstance(argument, str)
                        for argument in verification["command"]
                    )
                    or not isinstance(verification["exit_code"], int)
                    or not isinstance(verification["summary"], str)
                ):
                    raise ValueError("evidence verifications are malformed")
        alternatives = evidence["alternatives"]
        if not isinstance(alternatives, list) or len(alternatives) < 2:
            raise ValueError("evidence must contain serious alternatives")
        if any(
            not isinstance(item, dict)
            or set(item)
            != {
                "decision_key",
                "name",
                "is_obvious_shortcut",
                "is_recommended",
                "drivers_satisfied",
                "drivers_failed",
                "cost",
            }
            or not isinstance(item.get("decision_key"), str)
            or not item["decision_key"].strip()
            or not isinstance(item.get("name"), str)
            or not item["name"].strip()
            or not isinstance(item.get("is_obvious_shortcut"), bool)
            or not isinstance(item.get("is_recommended"), bool)
            or not isinstance(item.get("drivers_satisfied"), list)
            or any(
                not isinstance(driver, str)
                for driver in item["drivers_satisfied"]
            )
            or not isinstance(item.get("drivers_failed"), list)
            or any(
                not isinstance(driver, str) for driver in item["drivers_failed"]
            )
            or not isinstance(item.get("cost"), str)
            or not item["cost"].strip()
            for item in alternatives
        ):
            raise ValueError("evidence alternatives are malformed")
        if not any(
            isinstance(item, dict) and item.get("is_obvious_shortcut") is True
            for item in alternatives
        ):
            raise ValueError("evidence alternatives require the obvious shortcut")
        if (
            sum(
                1
                for item in alternatives
                if isinstance(item, dict) and item.get("is_recommended") is True
            )
            != 1
        ):
            raise ValueError("evidence alternatives require one recommendation")
        blockers = evidence["blockers"]
        if not isinstance(blockers, list) or any(
            not isinstance(item, dict)
            or set(item) != {"code", "message", "required_input"}
            or not isinstance(item.get("code"), str)
            or not isinstance(item.get("message"), str)
            or not isinstance(item.get("required_input"), str)
            for item in blockers
        ):
            raise ValueError("evidence blockers are malformed")
        artifacts = evidence["artifacts"]
        if not isinstance(artifacts, list) or any(
            not isinstance(item, dict)
            or set(item) != {"path", "sha256"}
            or not isinstance(item.get("path"), str)
            or not item["path"].startswith("docs/adr/")
            or not item["path"].endswith(".md")
            or not isinstance(item.get("sha256"), str)
            for item in artifacts
        ) or not any(
            isinstance(item, dict)
            and item.get("sha256") == adr_sha256
            for item in artifacts
        ):
            raise ValueError("evidence does not identify the reviewed ADR")
        return evidence
