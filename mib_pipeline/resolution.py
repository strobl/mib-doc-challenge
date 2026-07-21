"""Active-case linking and deterministic evidence precedence resolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from .extraction import CandidateEvidence, EvidenceType
from .models import CASE_ID_PATTERN, FIELD_NAMES


POLICY_ONLY_FIELDS = (
    "stay_duration_days",
    "packet_receipt_date",
    "biohazard_check",
    "hardship_waiver",
    "diplomatic_note",
    "work_permit_requested",
)
RESOLVABLE_FIELDS = (
    tuple(field for field in FIELD_NAMES if field != "confidence")
    + POLICY_ONLY_FIELDS
)


class FieldState(str, Enum):
    RESOLVED = "resolved"
    UNKNOWN = "unknown"
    CONTESTED = "contested"


@dataclass(frozen=True)
class LinkedCase:
    case_id: str
    active_applicant: str | None
    evidence: tuple[CandidateEvidence, ...]
    unresolved: bool
    unresolved_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedField:
    field_name: str
    state: FieldState
    value: str | None
    winning_evidence: CandidateEvidence | None
    considered: tuple[CandidateEvidence, ...]
    reason: str


@dataclass(frozen=True)
class ResolvedCase:
    case_id: str
    active_applicant: str | None
    fields: Mapping[str, ResolvedField]
    unresolved_linkage: bool
    unresolved_reasons: tuple[str, ...]
    rescinded_decision: bool = False

    def value(self, field_name: str) -> str | None:
        field = self.fields[field_name]
        return field.value if field.state is FieldState.RESOLVED else None

    @property
    def contested_fields(self) -> tuple[str, ...]:
        return tuple(
            field_name
            for field_name, field in self.fields.items()
            if field.state is FieldState.CONTESTED
        )

    @property
    def unknown_fields(self) -> tuple[str, ...]:
        return tuple(
            field_name
            for field_name, field in self.fields.items()
            if field.state is FieldState.UNKNOWN
        )


class EvidencePrecedenceHierarchy:
    """Binding six-level hierarchy from the MIB field manual."""

    _RANKS = {
        EvidenceType.ADJUDICATOR_STAMP: 1,
        EvidenceType.SIGNED_MANUAL_NOTE: 1,
        EvidenceType.INTAKE_FORM: 2,
        EvidenceType.BIOMETRIC_SLIP: 3,
        EvidenceType.SPONSOR_ATTESTATION: 4,
        EvidenceType.REGISTRY_EXTRACT: 5,
        EvidenceType.TEXT_LAYER: 6,
    }

    @classmethod
    def rank(cls, evidence_type: EvidenceType) -> int:
        return cls._RANKS[evidence_type]


class CaseLinker:
    """Scope evidence to the case filename and its reliably-linked applicant."""

    def link(
        self,
        expected_case_id: str | None,
        candidates: Iterable[CandidateEvidence],
    ) -> LinkedCase:
        candidates = tuple(candidates)
        expected = (
            expected_case_id.strip()
            if isinstance(expected_case_id, str)
            and CASE_ID_PATTERN.fullmatch(expected_case_id.strip())
            else None
        )
        visible_case_ids = {
            candidate.value
            for candidate in candidates
            if candidate.field_name == "case_id"
            and candidate.legible
            and candidate.value is not None
            and CASE_ID_PATTERN.fullmatch(candidate.value)
        }
        reasons: list[str] = []
        if expected is not None:
            case_id = expected
            if visible_case_ids and expected not in visible_case_ids:
                reasons.append("visible case_id conflicts with source filename")
        elif len(visible_case_ids) == 1:
            case_id = next(iter(visible_case_ids))
        else:
            case_id = ""
            reasons.append("active case_id cannot be determined")

        case_scoped = tuple(
            candidate
            for candidate in candidates
            if not candidate.case_id_hint
            or not case_id
            or candidate.case_id_hint == case_id
        )
        applicant_evidence = tuple(
            candidate
            for candidate in case_scoped
            if candidate.field_name == "applicant_name"
            and candidate.legible
            and candidate.value
        )
        applicant_names = {candidate.value for candidate in applicant_evidence}
        if applicant_evidence:
            best_rank = min(
                EvidencePrecedenceHierarchy.rank(candidate.evidence_type)
                for candidate in applicant_evidence
            )
            best_names = {
                candidate.value
                for candidate in applicant_evidence
                if EvidencePrecedenceHierarchy.rank(candidate.evidence_type)
                == best_rank
            }
        else:
            best_names = set()
        if len(best_names) == 1:
            active_applicant = next(iter(best_names))
        elif len(applicant_names) > 1:
            active_applicant = None
            reasons.append("multiple applicants cannot be reliably separated")
        else:
            active_applicant = None

        if active_applicant is None and len(applicant_names) > 1:
            applicant_scoped = tuple(
                candidate
                for candidate in case_scoped
                if candidate.field_name in {"case_id", "applicant_name"}
            )
        else:
            applicant_scoped = tuple(
                candidate
                for candidate in case_scoped
                if not candidate.applicant_hint
                or not active_applicant
                or candidate.applicant_hint == active_applicant
            )

        return LinkedCase(
            case_id=case_id,
            active_applicant=active_applicant,
            evidence=applicant_scoped,
            unresolved=bool(reasons),
            unresolved_reasons=tuple(reasons),
        )


class RescindedDecisionHandler:
    """Neutralize decorative or visibly overturned denial decisions."""

    @staticmethod
    def _sequence(candidate: CandidateEvidence) -> tuple[int, float]:
        return candidate.page_index, candidate.box.top

    def filter(
        self,
        candidates: Iterable[CandidateEvidence],
    ) -> tuple[tuple[CandidateEvidence, ...], bool]:
        candidates = tuple(candidates)
        eligible = [
            candidate
            for candidate in candidates
            if "sample_denial_watermark" not in candidate.visual_cues
        ]
        decisions = [
            candidate
            for candidate in eligible
            if candidate.field_name == "adjudication" and candidate.value
        ]
        later_signed_approvals = [
            candidate
            for candidate in decisions
            if candidate.value == "APPROVED"
            and candidate.evidence_type is EvidenceType.SIGNED_MANUAL_NOTE
            and "correction" in candidate.visual_cues
        ]
        rescinded = False
        if later_signed_approvals:
            latest_approval = max(later_signed_approvals, key=self._sequence)
            filtered: list[CandidateEvidence] = []
            for candidate in eligible:
                is_overturned_denial = (
                    candidate.field_name == "adjudication"
                    and candidate.value == "DENIED"
                    and candidate.evidence_type is EvidenceType.ADJUDICATOR_STAMP
                    and self._sequence(candidate) < self._sequence(latest_approval)
                )
                if is_overturned_denial:
                    rescinded = True
                    continue
                filtered.append(candidate)
            eligible = filtered
        return tuple(eligible), rescinded


class EvidencePrecedenceResolver:
    """Resolve one coherent field set after case/applicant scoping."""

    def __init__(
        self,
        *,
        hierarchy: type[EvidencePrecedenceHierarchy] = EvidencePrecedenceHierarchy,
        rescinded_handler: RescindedDecisionHandler | None = None,
    ) -> None:
        self._hierarchy = hierarchy
        self._rescinded = rescinded_handler or RescindedDecisionHandler()

    def _resolve_field(
        self,
        field_name: str,
        candidates: Iterable[CandidateEvidence],
    ) -> ResolvedField:
        considered = tuple(
            candidate
            for candidate in candidates
            if candidate.field_name == field_name
        )
        eligible = tuple(
            candidate
            for candidate in considered
            if candidate.legible
            and candidate.value is not None
            and not candidate.superseded
            and "strikethrough" not in candidate.visual_cues
        )
        if not eligible:
            return ResolvedField(
                field_name=field_name,
                state=FieldState.UNKNOWN,
                value=None,
                winning_evidence=None,
                considered=considered,
                reason="no eligible evidence",
            )

        winning_rank = min(
            self._hierarchy.rank(candidate.evidence_type) for candidate in eligible
        )
        top_rank = tuple(
            candidate
            for candidate in eligible
            if self._hierarchy.rank(candidate.evidence_type) == winning_rank
        )
        corrections = tuple(
            candidate
            for candidate in top_rank
            if "correction" in candidate.visual_cues
        )
        finalists = corrections or top_rank
        values = {candidate.value for candidate in finalists}
        if len(values) != 1:
            return ResolvedField(
                field_name=field_name,
                state=FieldState.CONTESTED,
                value=None,
                winning_evidence=None,
                considered=considered,
                reason=f"same-rank conflict at precedence rank {winning_rank}",
            )
        value = next(iter(values))
        winning_evidence = max(
            finalists,
            key=lambda candidate: (
                candidate.ocr_confidence,
                candidate.page_index,
                candidate.box.top,
            ),
        )
        return ResolvedField(
            field_name=field_name,
            state=FieldState.RESOLVED,
            value=value,
            winning_evidence=winning_evidence,
            considered=considered,
            reason=f"resolved at precedence rank {winning_rank}",
        )

    def resolve(self, linked_case: LinkedCase) -> ResolvedCase:
        evidence, rescinded = self._rescinded.filter(linked_case.evidence)
        fields = {
            field_name: self._resolve_field(field_name, evidence)
            for field_name in RESOLVABLE_FIELDS
        }
        if linked_case.case_id:
            case_candidates = tuple(
                candidate
                for candidate in evidence
                if candidate.field_name == "case_id"
            )
            fields["case_id"] = ResolvedField(
                field_name="case_id",
                state=FieldState.RESOLVED,
                value=linked_case.case_id,
                winning_evidence=(case_candidates[0] if case_candidates else None),
                considered=case_candidates,
                reason="active case association",
            )
        if linked_case.active_applicant:
            applicant_candidates = tuple(
                candidate
                for candidate in evidence
                if candidate.field_name == "applicant_name"
                and candidate.value == linked_case.active_applicant
            )
            fields["applicant_name"] = ResolvedField(
                field_name="applicant_name",
                state=FieldState.RESOLVED,
                value=linked_case.active_applicant,
                winning_evidence=(
                    applicant_candidates[0] if applicant_candidates else None
                ),
                considered=applicant_candidates,
                reason="active applicant association",
            )
        return ResolvedCase(
            case_id=linked_case.case_id,
            active_applicant=linked_case.active_applicant,
            fields=fields,
            unresolved_linkage=linked_case.unresolved,
            unresolved_reasons=linked_case.unresolved_reasons,
            rescinded_decision=rescinded,
        )
