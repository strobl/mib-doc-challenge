import unittest

from mib_pipeline import (
    CandidateEvidence,
    CaseLinker,
    EvidencePrecedenceHierarchy,
    EvidencePrecedenceResolver,
    EvidenceType,
    FieldState,
    Rect,
)


def candidate(
    field_name,
    value,
    evidence_type=EvidenceType.INTAKE_FORM,
    *,
    page=0,
    top=20,
    legible=True,
    superseded=False,
    cues=(),
    case_hint="MIB-000001",
    applicant_hint="Zed Zarnax",
    confidence=0.9,
):
    return CandidateEvidence(
        field_name=field_name,
        value=value,
        evidence_type=evidence_type,
        page_index=page,
        box=Rect(10, top - 10, 200, top),
        legible=legible,
        superseded=superseded,
        ocr_confidence=confidence,
        visual_cues=tuple(cues),
        case_id_hint=case_hint,
        applicant_hint=applicant_hint,
    )


def linked(*candidates, expected="MIB-000001"):
    return CaseLinker().link(expected, candidates)


class CaseLinkerTests(unittest.TestCase):
    def test_evidence_for_other_case_and_applicant_is_excluded(self):
        evidence = [
            candidate("applicant_name", "Zed Zarnax"),
            candidate("home_world", "Kepler-186f"),
            candidate(
                "applicant_name",
                "Other Person",
                case_hint="MIB-000999",
                applicant_hint="Other Person",
                page=2,
            ),
            candidate(
                "home_world",
                "Mars",
                case_hint="MIB-000999",
                applicant_hint="Other Person",
                page=2,
            ),
        ]

        result = linked(*evidence)

        self.assertEqual(result.case_id, "MIB-000001")
        self.assertEqual(result.active_applicant, "Zed Zarnax")
        self.assertFalse(result.unresolved)
        self.assertNotIn("Mars", {item.value for item in result.evidence})

    def test_unseparable_multiple_applicants_are_marked_unresolved(self):
        evidence = [
            candidate("applicant_name", "Zed Zarnax", applicant_hint=None),
            candidate("applicant_name", "Other Person", applicant_hint=None),
            candidate("home_world", "Mars", applicant_hint=None),
        ]

        result = linked(*evidence)

        self.assertTrue(result.unresolved)
        self.assertIsNone(result.active_applicant)
        self.assertIn("multiple applicants", result.unresolved_reasons[0])
        self.assertFalse(any(item.field_name == "home_world" for item in result.evidence))

    def test_conflicting_visible_case_id_marks_linkage_unresolved(self):
        result = linked(
            candidate(
                "case_id",
                "MIB-000999",
                case_hint="MIB-000999",
                applicant_hint=None,
            )
        )

        self.assertEqual(result.case_id, "MIB-000001")
        self.assertTrue(result.unresolved)


class PrecedenceResolverTests(unittest.TestCase):
    def test_precedence_ranks_match_field_manual(self):
        self.assertEqual(
            [
                EvidencePrecedenceHierarchy.rank(evidence_type)
                for evidence_type in (
                    EvidenceType.ADJUDICATOR_STAMP,
                    EvidenceType.INTAKE_FORM,
                    EvidenceType.BIOMETRIC_SLIP,
                    EvidenceType.SPONSOR_ATTESTATION,
                    EvidenceType.REGISTRY_EXTRACT,
                    EvidenceType.TEXT_LAYER,
                )
            ],
            [1, 2, 3, 4, 5, 6],
        )

    def test_higher_rank_wins_and_lower_rank_cannot_override(self):
        case = linked(
            candidate("fee_status", "paid", EvidenceType.INTAKE_FORM),
            candidate("fee_status", "unpaid", EvidenceType.REGISTRY_EXTRACT),
        )

        resolved = EvidencePrecedenceResolver().resolve(case)

        field = resolved.fields["fee_status"]
        self.assertEqual(field.state, FieldState.RESOLVED)
        self.assertEqual(field.value, "paid")
        self.assertEqual(field.winning_evidence.evidence_type, EvidenceType.INTAKE_FORM)

    def test_same_rank_conflict_is_contested(self):
        case = linked(
            candidate("home_world", "Mars", EvidenceType.INTAKE_FORM),
            candidate("home_world", "Europa", EvidenceType.INTAKE_FORM, page=1),
        )

        field = EvidencePrecedenceResolver().resolve(case).fields["home_world"]

        self.assertEqual(field.state, FieldState.CONTESTED)
        self.assertIsNone(field.value)

    def test_struck_through_value_is_dropped(self):
        case = linked(
            candidate(
                "fee_status",
                "unpaid",
                EvidenceType.INTAKE_FORM,
                superseded=True,
                cues=("strikethrough",),
            ),
            candidate("fee_status", "paid", EvidenceType.SPONSOR_ATTESTATION),
        )

        field = EvidencePrecedenceResolver().resolve(case).fields["fee_status"]

        self.assertEqual(field.value, "paid")

    def test_visible_correction_wins_within_same_rank(self):
        case = linked(
            candidate("sponsor_id", "SPN-0007", EvidenceType.INTAKE_FORM),
            candidate(
                "sponsor_id",
                "SPN-1234",
                EvidenceType.INTAKE_FORM,
                page=1,
                cues=("correction",),
            ),
        )

        field = EvidencePrecedenceResolver().resolve(case).fields["sponsor_id"]

        self.assertEqual(field.value, "SPN-1234")

    def test_text_layer_is_used_only_when_no_visible_source_exists(self):
        text_only = linked(
            candidate("visa_class", "XW-1", EvidenceType.TEXT_LAYER)
        )
        self.assertEqual(
            EvidencePrecedenceResolver().resolve(text_only).fields["visa_class"].value,
            "XW-1",
        )

        with_visible = linked(
            candidate("visa_class", "XW-1", EvidenceType.TEXT_LAYER),
            candidate("visa_class", "XW-2", EvidenceType.INTAKE_FORM),
        )
        self.assertEqual(
            EvidencePrecedenceResolver().resolve(with_visible).fields["visa_class"].value,
            "XW-2",
        )

    def test_missing_and_illegible_fields_are_unknown(self):
        case = linked(
            candidate("species_code", None, legible=False),
        )

        resolved = EvidencePrecedenceResolver().resolve(case)

        self.assertEqual(resolved.fields["species_code"].state, FieldState.UNKNOWN)
        self.assertEqual(resolved.fields["home_world"].state, FieldState.UNKNOWN)

    def test_later_signed_approval_rescinds_denial_stamp(self):
        case = linked(
            candidate(
                "adjudication",
                "DENIED",
                EvidenceType.ADJUDICATOR_STAMP,
                page=0,
            ),
            candidate(
                "adjudication",
                "APPROVED",
                EvidenceType.SIGNED_MANUAL_NOTE,
                page=1,
                cues=("correction",),
            ),
        )

        resolved = EvidencePrecedenceResolver().resolve(case)

        self.assertTrue(resolved.rescinded_decision)
        self.assertEqual(resolved.fields["adjudication"].value, "APPROVED")

    def test_sample_denial_watermark_never_becomes_live_decision(self):
        case = linked(
            candidate(
                "adjudication",
                "DENIED",
                EvidenceType.ADJUDICATOR_STAMP,
                cues=("sample_denial_watermark",),
            )
        )

        field = EvidencePrecedenceResolver().resolve(case).fields["adjudication"]

        self.assertEqual(field.state, FieldState.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
