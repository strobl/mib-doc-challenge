"""Deterministic, evidence-aware MIB policy adjudication."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping, Protocol

from .extraction import EvidenceType
from .models import PredictionRow
from .resolution import FieldState, ResolvedCase, ResolvedField


VISA_CLASSES = frozenset({"XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7"})
PINNED_POLICY_EXCEPTIONS_PATH = (
    Path(__file__).resolve().parent / "artifacts" / "policy_exceptions.json"
)
OUTPUT_VALUE_FIELDS = (
    "applicant_name",
    "species_code",
    "home_world",
    "visa_class",
    "sponsor_id",
    "arrival_date",
    "declared_purpose",
    "risk_flags",
    "fee_status",
)


class PolicyArtifactError(ValueError):
    """The pinned policy-exception artifact is absent, malformed, or unsafe."""


@dataclass(frozen=True)
class PolicyException:
    """One held-out-validated exception expressed only through visible features."""

    rule_id: str
    conditions: Mapping[str, str]
    decision: str
    rationale: str
    validated: bool = True


class GeneralizablePolicyExceptionStore:
    """Reject identity lookups and expose only exact, inspectable feature rules."""

    _ALLOWED_KEYS = frozenset(
        {
            "species_code",
            "home_world",
            "visa_class",
            "sponsor_id",
            "declared_purpose",
            "risk_flags",
            "fee_status",
            "stay_duration_days",
            "biohazard_check",
            "hardship_waiver",
            "diplomatic_note",
            "work_permit_requested",
        }
    )
    _CASE_ID = re.compile(r"\bMIB-[0-9]{6}\b", re.I)
    _PDF_NAME = re.compile(r"\.pdf\b", re.I)
    _HASH = re.compile(r"\b[0-9a-f]{32,64}\b", re.I)

    def __init__(
        self,
        exceptions: Iterable[PolicyException] = (),
        *,
        artifact_id: str = "inline-policy-exceptions",
    ) -> None:
        if not artifact_id:
            raise ValueError("policy exception artifact_id must be non-empty")
        checked: list[PolicyException] = []
        seen_ids: set[str] = set()
        for exception in exceptions:
            if not exception.validated:
                raise ValueError(f"policy exception is not validated: {exception.rule_id}")
            if exception.decision not in {"DENIED", "NEEDS_REVIEW"}:
                raise ValueError(
                    "learned exceptions may make policy stricter but may not create approvals"
                )
            if not exception.rule_id or exception.rule_id in seen_ids:
                raise ValueError("policy exception IDs must be non-empty and unique")
            if not exception.conditions:
                raise ValueError("policy exceptions require visible feature conditions")
            for key, value in exception.conditions.items():
                normalized_key = key.strip().casefold()
                if key != normalized_key or not isinstance(value, str):
                    raise ValueError("policy exception conditions must use normalized strings")
                rendered_value = str(value)
                if normalized_key not in self._ALLOWED_KEYS:
                    raise ValueError(
                        f"non-generalizable policy exception key is forbidden: {key}"
                    )
                if (
                    self._CASE_ID.search(rendered_value)
                    or self._PDF_NAME.search(rendered_value)
                    or self._HASH.search(rendered_value)
                ):
                    raise ValueError(
                        f"identity-valued policy exception is forbidden: {exception.rule_id}"
                    )
            seen_ids.add(exception.rule_id)
            checked.append(
                PolicyException(
                    rule_id=exception.rule_id,
                    conditions=MappingProxyType(dict(exception.conditions)),
                    decision=exception.decision,
                    rationale=exception.rationale,
                    validated=True,
                )
            )
        self._exceptions = tuple(checked)
        self._artifact_id = artifact_id

    @classmethod
    def from_pinned_artifact(
        cls,
        path: Path = PINNED_POLICY_EXCEPTIONS_PATH,
    ) -> "GeneralizablePolicyExceptionStore":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PolicyArtifactError(
                f"cannot load policy exception artifact: {path}"
            ) from exc
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "artifact_id",
            "exceptions",
        }:
            raise PolicyArtifactError("unsupported policy exception artifact schema")
        if value.get("schema_version") != 1 or not isinstance(
            value.get("exceptions"), list
        ):
            raise PolicyArtifactError("unsupported policy exception artifact schema")
        exceptions: list[PolicyException] = []
        for raw_rule in value["exceptions"]:
            if not isinstance(raw_rule, dict) or set(raw_rule) != {
                "rule_id",
                "conditions",
                "decision",
                "rationale",
            }:
                raise PolicyArtifactError("malformed policy exception rule")
            exceptions.append(
                PolicyException(
                    rule_id=raw_rule["rule_id"],
                    conditions=raw_rule["conditions"],
                    decision=raw_rule["decision"],
                    rationale=raw_rule["rationale"],
                    validated=True,
                )
            )
        try:
            return cls(exceptions, artifact_id=value.get("artifact_id", ""))
        except (AttributeError, TypeError, ValueError) as exc:
            raise PolicyArtifactError("unsafe policy exception artifact") from exc

    @property
    def artifact_id(self) -> str:
        return self._artifact_id

    def matching(self, features: Mapping[str, str]) -> tuple[PolicyException, ...]:
        """Return exact feature matches in stable rule-ID order."""

        matches = [
            exception
            for exception in self._exceptions
            if all(features.get(key) == value for key, value in exception.conditions.items())
        ]
        return tuple(sorted(matches, key=lambda exception: exception.rule_id))


@dataclass(frozen=True)
class PolicyRuleSet:
    """Published policy constants and deterministic predicates."""

    barred_sponsors: frozenset[str] = frozenset(
        {"SPN-0007", "SPN-0139", "SPN-4040"}
    )
    disqualifying_flags: frozenset[str] = frozenset(
        {"memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red"}
    )
    review_only_flags: frozenset[str] = frozenset(
        {
            "identity_conflict",
            "sponsor_mismatch",
            "illegible_biometrics",
            "rescinded_denial",
        }
    )
    stay_limits: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({"XW-1": 30, "XW-2": 180})
    )
    stale_after_days: int = 180


@dataclass(frozen=True)
class DecisionTrace:
    """Auditable policy result consumed by confidence calibration and evaluation."""

    decision: str
    authoritative_source: bool
    denial_reasons: tuple[str, ...]
    review_reasons: tuple[str, ...]
    approval_facts: tuple[str, ...]
    exception_ids: tuple[str, ...]


@dataclass(frozen=True)
class AdjudicationOutcome:
    row: PredictionRow
    trace: DecisionTrace


class ConfidenceProvider(Protocol):
    def calibrate(self, trace: DecisionTrace) -> float:
        """Return P(the chosen adjudication is correct)."""


def _field(resolved_case: ResolvedCase, field_name: str) -> ResolvedField | None:
    return resolved_case.fields.get(field_name)


def _value(resolved_case: ResolvedCase, field_name: str) -> str | None:
    field = _field(resolved_case, field_name)
    if field is None or field.state is not FieldState.RESOLVED:
        return None
    return field.value


def _is_visible(field: ResolvedField | None) -> bool:
    evidence = field.winning_evidence if field is not None else None
    return bool(
        field is not None
        and field.state is FieldState.RESOLVED
        and evidence is not None
        and evidence.legible
        and evidence.source == "visible_ocr"
        and evidence.evidence_type is not EvidenceType.TEXT_LAYER
    )


def _parse_flags(value: str | None) -> frozenset[str]:
    if not value or value.casefold() == "none":
        return frozenset()
    return frozenset(
        item.strip().casefold().replace(" ", "_")
        for item in value.split("|")
        if item.strip()
    )


def _parse_positive_int(value: str | None) -> int | None:
    try:
        parsed = int(value) if value is not None else 0
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value is not None else None
    except ValueError:
        return None


class AdjudicationEngine:
    """Apply policy to resolved visible evidence with a strict approval bar."""

    def __init__(
        self,
        *,
        rules: PolicyRuleSet | None = None,
        exceptions: GeneralizablePolicyExceptionStore | None = None,
        calibrator: ConfidenceProvider | None = None,
        default_confidence: float = 0.0,
    ) -> None:
        self._rules = rules or PolicyRuleSet()
        self._exceptions = exceptions or GeneralizablePolicyExceptionStore()
        self._calibrator = calibrator
        self._default_confidence = default_confidence

    @staticmethod
    def _authoritative_decision(resolved_case: ResolvedCase) -> str | None:
        field = _field(resolved_case, "adjudication")
        evidence = field.winning_evidence if field is not None else None
        if (
            _is_visible(field)
            and evidence is not None
            and evidence.evidence_type
            in {EvidenceType.ADJUDICATOR_STAMP, EvidenceType.SIGNED_MANUAL_NOTE}
        ):
            return field.value
        return None

    @staticmethod
    def _feature_map(resolved_case: ResolvedCase, flags: frozenset[str]) -> dict[str, str]:
        features = {
            name: value
            for name in resolved_case.fields
            if _is_visible(_field(resolved_case, name))
            and (value := _value(resolved_case, name)) is not None
        }
        if _is_visible(_field(resolved_case, "risk_flags")):
            features["risk_flags"] = "|".join(sorted(flags)) if flags else "none"
        return features

    @staticmethod
    def _work_permit_requested(resolved_case: ResolvedCase) -> bool | None:
        explicit = _value(resolved_case, "work_permit_requested")
        if explicit is not None and _is_visible(
            _field(resolved_case, "work_permit_requested")
        ):
            return explicit == "yes"
        purpose = (_value(resolved_case, "declared_purpose") or "").casefold()
        if not _is_visible(_field(resolved_case, "declared_purpose")):
            return None
        if any(token in purpose for token in ("work", "technical", "employment")):
            return True
        return None

    @staticmethod
    def _valid_visible_marker(resolved_case: ResolvedCase, field_name: str) -> bool:
        return _value(resolved_case, field_name) == "valid" and _is_visible(
            _field(resolved_case, field_name)
        )

    def _assemble_row(
        self,
        resolved_case: ResolvedCase,
        decision: str,
        confidence: float,
    ) -> PredictionRow:
        values = {
            field_name: _value(resolved_case, field_name)
            for field_name in OUTPUT_VALUE_FIELDS
        }
        values.update(
            {
                "case_id": resolved_case.case_id,
                "adjudication": decision,
                "confidence": confidence,
            }
        )
        return PredictionRow.from_mapping(values, fallback_case_id=resolved_case.case_id)

    def adjudicate_case(self, resolved_case: ResolvedCase) -> AdjudicationOutcome:
        authoritative = self._authoritative_decision(resolved_case)
        if authoritative is not None:
            trace = DecisionTrace(
                decision=authoritative,
                authoritative_source=True,
                denial_reasons=("authoritative_visible_decision",)
                if authoritative == "DENIED"
                else (),
                review_reasons=("authoritative_visible_decision",)
                if authoritative == "NEEDS_REVIEW"
                else (),
                approval_facts=("authoritative_visible_decision",)
                if authoritative == "APPROVED"
                else (),
                exception_ids=(),
            )
            confidence = self._confidence(trace)
            return AdjudicationOutcome(
                row=self._assemble_row(resolved_case, authoritative, confidence),
                trace=trace,
            )

        denial_reasons: list[str] = []
        review_reasons: list[str] = []
        approval_facts: list[str] = []
        visa_class = _value(resolved_case, "visa_class")
        visa_visible = _is_visible(_field(resolved_case, "visa_class"))
        sponsor_id = _value(resolved_case, "sponsor_id")
        fee_status = _value(resolved_case, "fee_status")
        fee_visible = _is_visible(_field(resolved_case, "fee_status"))
        flags_field = _field(resolved_case, "risk_flags")
        flags = _parse_flags(_value(resolved_case, "risk_flags"))
        flags_visible = _is_visible(flags_field)
        hardship_waiver = self._valid_visible_marker(resolved_case, "hardship_waiver")
        diplomatic_note = self._valid_visible_marker(resolved_case, "diplomatic_note")

        disqualifying = sorted(flags & self._rules.disqualifying_flags) if flags_visible else []
        denial_reasons.extend(f"disqualifying_flag:{flag}" for flag in disqualifying)

        if visa_visible and visa_class == "TRANSIT-7":
            work_permit_requested = self._work_permit_requested(resolved_case)
            if work_permit_requested:
                denial_reasons.append("transit_work_authorization")
            else:
                review_reasons.append("transit_work_request_not_trustworthily_clear")

        if fee_visible and fee_status == "unpaid" and not hardship_waiver:
            denial_reasons.append("unpaid_without_valid_waiver")
        elif fee_visible and fee_status == "paid":
            approval_facts.append("fee_paid")
        elif fee_visible and fee_status == "waived":
            if (visa_visible and visa_class == "DIP-1") or hardship_waiver:
                approval_facts.append("valid_fee_waiver")
            else:
                review_reasons.append("unsupported_fee_waiver")
        elif fee_status is None or fee_status == "unknown":
            review_reasons.append("fee_status_unknown")

        if not visa_visible:
            review_reasons.append("visa_class_not_visible")
        elif visa_class != "DIP-1":
            if sponsor_id is None:
                denial_reasons.append("required_sponsor_absent")
            elif not _is_visible(_field(resolved_case, "sponsor_id")):
                review_reasons.append("required_sponsor_not_visible")
            elif sponsor_id in self._rules.barred_sponsors:
                denial_reasons.append(f"barred_sponsor:{sponsor_id}")
            else:
                approval_facts.append("sponsor_present_and_not_publicly_barred")
        else:
            approval_facts.append("diplomatic_sponsor_exemption")

        arrival = _parse_date(_value(resolved_case, "arrival_date"))
        receipt = _parse_date(_value(resolved_case, "packet_receipt_date"))
        if arrival is None:
            review_reasons.append("arrival_date_unknown")
        elif not _is_visible(_field(resolved_case, "arrival_date")):
            review_reasons.append("arrival_date_not_visible")
        elif receipt is None:
            review_reasons.append("packet_receipt_date_unknown")
        elif not _is_visible(_field(resolved_case, "packet_receipt_date")):
            review_reasons.append("packet_receipt_date_not_visible")
        else:
            age_days = (receipt - arrival).days
            if age_days > self._rules.stale_after_days and not (
                visa_visible and visa_class == "DIP-1" and diplomatic_note
            ):
                denial_reasons.append("stale_application")
            else:
                approval_facts.append("application_date_current_or_exempt")

        if visa_visible and visa_class in self._rules.stay_limits:
            duration = _parse_positive_int(_value(resolved_case, "stay_duration_days"))
            if duration is None or not _is_visible(
                _field(resolved_case, "stay_duration_days")
            ):
                review_reasons.append("stay_duration_unknown")
            elif duration > self._rules.stay_limits[visa_class]:
                denial_reasons.append(f"stay_limit_exceeded:{visa_class}")
            else:
                approval_facts.append("stay_within_visa_limit")

        if visa_visible and visa_class == "MED-3":
            biohazard = _value(resolved_case, "biohazard_check")
            if biohazard == "red" and _is_visible(
                _field(resolved_case, "biohazard_check")
            ):
                denial_reasons.append("biohazard_red")
            elif biohazard != "clean" or not _is_visible(
                _field(resolved_case, "biohazard_check")
            ):
                review_reasons.append("clean_biohazard_check_missing")
            else:
                approval_facts.append("clean_biohazard_check")

        if visa_class not in VISA_CLASSES:
            review_reasons.append("visa_class_unknown")

        review_flags = sorted(flags & self._rules.review_only_flags)
        review_reasons.extend(f"review_flag:{flag}" for flag in review_flags)
        if resolved_case.rescinded_decision and "rescinded_denial" not in flags:
            review_reasons.append("review_flag:rescinded_denial")

        if flags_field is None or flags_field.state is not FieldState.RESOLVED:
            review_reasons.append("risk_flags_unknown")
        elif not _is_visible(flags_field):
            review_reasons.append("risk_flags_not_visible")

        if resolved_case.unresolved_linkage:
            review_reasons.extend(
                f"unresolved_linkage:{reason}" for reason in resolved_case.unresolved_reasons
            )
        for field_name, field in resolved_case.fields.items():
            if field.state is FieldState.CONTESTED:
                review_reasons.append(f"contested_field:{field_name}")

        for field_name in OUTPUT_VALUE_FIELDS:
            field = _field(resolved_case, field_name)
            if field is None or field.state is not FieldState.RESOLVED:
                if field_name != "sponsor_id" or visa_class != "DIP-1":
                    review_reasons.append(f"required_output_unknown:{field_name}")
            elif not _is_visible(field):
                review_reasons.append(f"required_output_not_visible:{field_name}")

        features = self._feature_map(resolved_case, flags)
        matching_exceptions = self._exceptions.matching(features)
        exception_ids = tuple(exception.rule_id for exception in matching_exceptions)
        exception_decisions = {exception.decision for exception in matching_exceptions}
        if len(exception_decisions) > 1:
            review_reasons.append("conflicting_generalizable_exceptions")
        elif exception_decisions == {"DENIED"}:
            denial_reasons.append("validated_generalizable_exception")
        elif exception_decisions == {"NEEDS_REVIEW"}:
            review_reasons.append("validated_generalizable_exception")

        if denial_reasons:
            decision = "DENIED"
        elif review_reasons:
            decision = "NEEDS_REVIEW"
        else:
            decision = "APPROVED"
            approval_facts.append("strict_approval_bar_cleared")

        trace = DecisionTrace(
            decision=decision,
            authoritative_source=False,
            denial_reasons=tuple(sorted(set(denial_reasons))),
            review_reasons=tuple(sorted(set(review_reasons))),
            approval_facts=tuple(sorted(set(approval_facts))),
            exception_ids=exception_ids,
        )
        confidence = self._confidence(trace)
        return AdjudicationOutcome(
            row=self._assemble_row(resolved_case, decision, confidence), trace=trace
        )

    def _confidence(self, trace: DecisionTrace) -> float:
        if self._calibrator is None:
            return self._default_confidence
        return self._calibrator.calibrate(trace)

    def adjudicate(self, resolved_case: ResolvedCase) -> PredictionRow:
        """Return the canonical row expected by the runtime pipeline."""

        return self.adjudicate_case(resolved_case).row
