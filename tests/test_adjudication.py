import unittest

from mib_pipeline import (
    AdjudicationEngine,
    CandidateEvidence,
    CaseLinker,
    EvidencePrecedenceResolver,
    EvidenceType,
    GeneralizablePolicyExceptionStore,
    PolicyException,
    Rect,
)
from mib_pipeline.models import FIELD_NAMES


def candidate(
    field_name,
    value,
    evidence_type=EvidenceType.INTAKE_FORM,
    *,
    source="visible_ocr",
    cues=(),
):
    return CandidateEvidence(
        field_name=field_name,
        value=value,
        evidence_type=evidence_type,
        page_index=0,
        box=Rect(10, 10, 200, 30),
        legible=value is not None,
        superseded=False,
        ocr_confidence=0.95,
        visual_cues=tuple(cues),
        source=source,
        case_id_hint="MIB-000001",
        applicant_hint="Zed Zarnax",
    )


BASE_VALUES = {
    "applicant_name": "Zed Zarnax",
    "species_code": "ORION_GRAYS",
    "home_world": "Kepler-186f",
    "visa_class": "XW-2",
    "sponsor_id": "SPN-1042",
    "arrival_date": "2026-04-17",
    "declared_purpose": "technical work",
    "risk_flags": "none",
    "fee_status": "paid",
    "stay_duration_days": "90",
    "packet_receipt_date": "2026-04-20",
    "work_permit_requested": "yes",
}


def resolved_case(*, omit=(), evidence_types=None, **overrides):
    values = dict(BASE_VALUES)
    values.update(overrides)
    types = evidence_types or {}
    evidence = [
        candidate(name, value, types.get(name, EvidenceType.INTAKE_FORM))
        for name, value in values.items()
        if name not in omit
    ]
    linked = CaseLinker().link("MIB-000001", evidence)
    return EvidencePrecedenceResolver().resolve(linked)


def resolved_case_with_source(field_name, source, **overrides):
    values = dict(BASE_VALUES)
    values.update(overrides)
    evidence = [
        candidate(name, value, source=source if name == field_name else "visible_ocr")
        for name, value in values.items()
    ]
    return EvidencePrecedenceResolver().resolve(
        CaseLinker().link("MIB-000001", evidence)
    )


class AdjudicationPolicyTests(unittest.TestCase):
    def decision(self, case, *, engine=None):
        return (engine or AdjudicationEngine()).adjudicate_case(case)

    def test_clear_case_passes_strict_approval_bar_and_builds_exact_row(self):
        outcome = self.decision(resolved_case())

        self.assertEqual(outcome.row.adjudication, "APPROVED")
        self.assertEqual(tuple(outcome.row.to_dict()), FIELD_NAMES)
        self.assertIn("strict_approval_bar_cleared", outcome.trace.approval_facts)
        self.assertEqual(outcome.row.confidence, 0.0)

    def test_each_disqualifying_flag_denies(self):
        for flag in (
            "memory_tampering",
            "planetary_embargo",
            "active_warrant",
            "biohazard_red",
        ):
            with self.subTest(flag=flag):
                outcome = self.decision(resolved_case(risk_flags=flag))
                self.assertEqual(outcome.row.adjudication, "DENIED")
                self.assertIn(f"disqualifying_flag:{flag}", outcome.trace.denial_reasons)

    def test_transit_work_authorization_denies(self):
        outcome = self.decision(resolved_case(visa_class="TRANSIT-7"))

        self.assertEqual(outcome.row.adjudication, "DENIED")
        self.assertIn("transit_work_authorization", outcome.trace.denial_reasons)

    def test_unpaid_denies_but_visible_hardship_waiver_satisfies_fee(self):
        denied = self.decision(resolved_case(fee_status="unpaid"))
        waived = self.decision(
            resolved_case(fee_status="unpaid", hardship_waiver="valid")
        )

        self.assertEqual(denied.row.adjudication, "DENIED")
        self.assertNotEqual(waived.row.adjudication, "DENIED")

    def test_unsupported_waiver_reviews_and_diplomatic_waiver_is_valid(self):
        unsupported = self.decision(resolved_case(fee_status="waived"))
        diplomatic = self.decision(
            resolved_case(
                visa_class="DIP-1",
                fee_status="waived",
                omit=("stay_duration_days",),
            )
        )

        self.assertEqual(unsupported.row.adjudication, "NEEDS_REVIEW")
        self.assertEqual(diplomatic.row.adjudication, "APPROVED")

    def test_stale_application_denies_except_valid_diplomatic_note(self):
        stale = self.decision(
            resolved_case(arrival_date="2025-01-01", packet_receipt_date="2026-04-20")
        )
        exempt = self.decision(
            resolved_case(
                visa_class="DIP-1",
                fee_status="waived",
                arrival_date="2025-01-01",
                packet_receipt_date="2026-04-20",
                diplomatic_note="valid",
                omit=("stay_duration_days",),
            )
        )

        self.assertEqual(stale.row.adjudication, "DENIED")
        self.assertIn("stale_application", stale.trace.denial_reasons)
        self.assertEqual(exempt.row.adjudication, "APPROVED")

    def test_missing_or_barred_required_sponsor_denies(self):
        missing = self.decision(resolved_case(omit=("sponsor_id",)))
        barred = self.decision(resolved_case(sponsor_id="SPN-0139"))

        self.assertEqual(missing.row.adjudication, "DENIED")
        self.assertEqual(barred.row.adjudication, "DENIED")

    def test_med3_requires_visible_clean_check_and_red_denies(self):
        missing = self.decision(resolved_case(visa_class="MED-3"))
        clean = self.decision(
            resolved_case(visa_class="MED-3", biohazard_check="clean")
        )
        red = self.decision(
            resolved_case(visa_class="MED-3", biohazard_check="red")
        )

        self.assertEqual(missing.row.adjudication, "NEEDS_REVIEW")
        self.assertEqual(clean.row.adjudication, "APPROVED")
        self.assertEqual(red.row.adjudication, "DENIED")

    def test_xw_duration_over_limit_denies_and_unknown_reviews(self):
        over = self.decision(resolved_case(visa_class="XW-1", stay_duration_days="31"))
        unknown = self.decision(
            resolved_case(visa_class="XW-1", omit=("stay_duration_days",))
        )

        self.assertEqual(over.row.adjudication, "DENIED")
        self.assertEqual(unknown.row.adjudication, "NEEDS_REVIEW")

    def test_review_flags_and_missing_evidence_never_approve(self):
        flagged = self.decision(resolved_case(risk_flags="identity_conflict"))
        missing = self.decision(resolved_case(omit=("arrival_date",)))

        self.assertEqual(flagged.row.adjudication, "NEEDS_REVIEW")
        self.assertEqual(missing.row.adjudication, "NEEDS_REVIEW")

    def test_two_review_flags_stay_review_without_validated_exception(self):
        outcome = self.decision(
            resolved_case(risk_flags="identity_conflict|sponsor_mismatch")
        )

        self.assertEqual(outcome.row.adjudication, "NEEDS_REVIEW")

    def test_exact_validated_generalizable_exception_can_deny_flag_combo(self):
        store = GeneralizablePolicyExceptionStore(
            [
                PolicyException(
                    rule_id="review-combo-1",
                    conditions={
                        "risk_flags": "identity_conflict|sponsor_mismatch"
                    },
                    decision="DENIED",
                    rationale="held-out evidence supports this exact combination",
                )
            ]
        )
        outcome = self.decision(
            resolved_case(risk_flags="sponsor_mismatch|identity_conflict"),
            engine=AdjudicationEngine(exceptions=store),
        )

        self.assertEqual(outcome.row.adjudication, "DENIED")
        self.assertEqual(outcome.trace.exception_ids, ("review-combo-1",))

    def test_only_visible_rank_one_decision_can_override_policy(self):
        lower = resolved_case(
            risk_flags="active_warrant",
            adjudication="APPROVED",
        )
        higher = resolved_case(
            risk_flags="active_warrant",
            adjudication="APPROVED",
            evidence_types={"adjudication": EvidenceType.ADJUDICATOR_STAMP},
        )

        self.assertEqual(self.decision(lower).row.adjudication, "DENIED")
        high_outcome = self.decision(higher)
        self.assertEqual(high_outcome.row.adjudication, "APPROVED")
        self.assertTrue(high_outcome.trace.authoritative_source)

    def test_text_layer_only_critical_value_routes_to_review(self):
        outcome = self.decision(
            resolved_case(
                evidence_types={"arrival_date": EvidenceType.TEXT_LAYER}
            )
        )

        self.assertEqual(outcome.row.adjudication, "NEEDS_REVIEW")
        self.assertIn("arrival_date_not_visible", outcome.trace.review_reasons)

    def test_untrusted_disqualifying_facts_route_to_review_not_denial(self):
        risk = self.decision(
            resolved_case_with_source("risk_flags", "text_layer", risk_flags="active_warrant")
        )
        fee = self.decision(
            resolved_case_with_source("fee_status", "text_layer", fee_status="unpaid")
        )

        self.assertEqual(risk.row.adjudication, "NEEDS_REVIEW")
        self.assertFalse(risk.trace.denial_reasons)
        self.assertEqual(fee.row.adjudication, "NEEDS_REVIEW")
        self.assertFalse(fee.trace.denial_reasons)


class GeneralizablePolicyExceptionTests(unittest.TestCase):
    def test_rejects_unvalidated_and_case_identity_rules(self):
        with self.assertRaises(ValueError):
            GeneralizablePolicyExceptionStore(
                [
                    PolicyException(
                        "bad",
                        {"risk_flags": "none"},
                        "APPROVED",
                        "not held out",
                        validated=False,
                    )
                ]
            )
        for conditions in (
            {"case_id": "MIB-000001"},
            {"visible_note": "MIB-000001"},
            {"filename": "case.pdf"},
            {"risk_flags": "a" * 64},
            {"applicant_name": "Zed Zarnax"},
        ):
            with self.subTest(conditions=conditions), self.assertRaises(ValueError):
                GeneralizablePolicyExceptionStore(
                    [PolicyException("bad", conditions, "DENIED", "identity leak")]
                )

    def test_learned_exception_cannot_create_an_approval(self):
        with self.assertRaises(ValueError):
            GeneralizablePolicyExceptionStore(
                [
                    PolicyException(
                        "unsafe-approval",
                        {"risk_flags": "none"},
                        "APPROVED",
                        "approvals must clear the deterministic safety bar",
                    )
                ]
            )


if __name__ == "__main__":
    unittest.main()
