import math
import unittest

from mib_pipeline import (
    CalibrationArtifactError,
    ConfidenceCalibrator,
    DecisionSignalModel,
    DecisionTrace,
    PinnedIsotonicMap,
)


def trace(
    decision,
    *,
    authoritative=False,
    denial=(),
    review=(),
    approval=(),
):
    return DecisionTrace(
        decision=decision,
        authoritative_source=authoritative,
        denial_reasons=tuple(denial),
        review_reasons=tuple(review),
        approval_facts=tuple(approval),
        exception_ids=(),
    )


def artifact(**overrides):
    value = {
        "schema_version": 1,
        "artifact_id": "test-map-v1",
        "breakpoints": [0.0, 0.5, 0.75, 1.0],
        "probabilities": [0.2, 0.55, 0.8, 0.95],
        "fit_metadata": {
            "method": "isotonic_regression",
            "target": "emitted_adjudication_is_correct",
        },
    }
    value.update(overrides)
    return value


class PinnedIsotonicMapTests(unittest.TestCase):
    def test_pinned_artifact_loads_and_spans_probability_range(self):
        calibrator = ConfidenceCalibrator.from_pinned_artifact()

        self.assertTrue(calibrator.artifact_id)
        values = [
            calibrator.calibrate(
                trace("DENIED", denial=("disqualifying_flag:active_warrant",))
            ),
            calibrator.calibrate(
                trace("NEEDS_REVIEW", review=("fee_status_unknown",))
            ),
            calibrator.calibrate(
                trace(
                    "APPROVED",
                    approval=(
                        "fee_paid",
                        "sponsor_present_and_not_publicly_barred",
                        "strict_approval_bar_cleared",
                    ),
                )
            ),
        ]

        self.assertTrue(all(0.0 <= value <= 1.0 for value in values))
        self.assertGreater(len(set(values)), 1)

    def test_calibrated_review_confidence_is_not_a_fixed_default(self):
        calibrator = ConfidenceCalibrator.from_pinned_artifact()
        missing = calibrator.calibrate(
            trace("NEEDS_REVIEW", review=("fee_status_unknown",))
        )
        contested = calibrator.calibrate(
            trace(
                "NEEDS_REVIEW",
                review=("contested_field:visa_class", "fee_status_unknown"),
            )
        )

        self.assertNotEqual(missing, contested)

    def test_isotonic_prediction_is_monotonic_and_bounded(self):
        mapping = PinnedIsotonicMap.from_mapping(artifact())
        predictions = [mapping.predict(value / 20) for value in range(-5, 26)]

        self.assertEqual(predictions, sorted(predictions))
        self.assertTrue(all(0.0 <= value <= 1.0 for value in predictions))
        self.assertEqual(mapping.predict(-100), 0.2)
        self.assertEqual(mapping.predict(100), 0.95)

    def test_rejects_malformed_or_non_monotonic_artifacts(self):
        malformed = (
            artifact(breakpoints=[0.0, 0.8, 0.7, 1.0]),
            artifact(probabilities=[0.2, 0.8, 0.7, 0.95]),
            artifact(breakpoints=[0.1, 0.5, 0.75, 1.0]),
            artifact(probabilities=[0.2, math.nan, 0.8, 0.95]),
            {**artifact(), "unexpected": True},
        )
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(CalibrationArtifactError):
                PinnedIsotonicMap.from_mapping(value)


class DecisionSignalTests(unittest.TestCase):
    def setUp(self):
        self.model = DecisionSignalModel()

    def test_authoritative_and_hard_denial_signals_are_strong(self):
        authoritative = self.model.raw_signal(
            trace("APPROVED", authoritative=True, approval=("source",))
        )
        hard_denial = self.model.raw_signal(
            trace("DENIED", denial=("disqualifying_flag:active_warrant",))
        )

        self.assertGreaterEqual(authoritative, 0.95)
        self.assertGreaterEqual(hard_denial, 0.9)

    def test_review_confidence_reflects_why_review_is_correct(self):
        missing = self.model.raw_signal(
            trace("NEEDS_REVIEW", review=("fee_status_unknown",))
        )
        untrusted = self.model.raw_signal(
            trace(
                "NEEDS_REVIEW",
                review=("fee_status_not_visible", "risk_flags_not_visible"),
            )
        )
        contested = self.model.raw_signal(
            trace(
                "NEEDS_REVIEW",
                review=("contested_field:visa_class", "fee_status_unknown"),
            )
        )

        self.assertNotEqual(missing, untrusted)
        self.assertGreater(contested, untrusted)

    def test_approval_signal_depends_on_policy_support_not_ocr_score(self):
        lightly_supported = self.model.raw_signal(
            trace(
                "APPROVED",
                approval=("strict_approval_bar_cleared", "fee_paid"),
            )
        )
        strongly_supported = self.model.raw_signal(
            trace(
                "APPROVED",
                approval=(
                    "strict_approval_bar_cleared",
                    "fee_paid",
                    "sponsor_present_and_not_publicly_barred",
                    "application_date_current_or_exempt",
                    "stay_within_visa_limit",
                ),
            )
        )

        self.assertGreater(strongly_supported, lightly_supported)

    def test_unknown_decision_is_rejected(self):
        with self.assertRaises(ValueError):
            self.model.raw_signal(trace("MAYBE"))


if __name__ == "__main__":
    unittest.main()
