import csv
import json
import tempfile
import unittest
from pathlib import Path

from devtools.ocr_ablation import (
    BASELINE_CONFIG,
    AblationConfigurationError,
    AblationVariant,
    build_report,
    config_sha256,
    registered_variants,
    render_markdown,
    run_variant,
)
from mib_pipeline import PredictionRow, build_production_processor


REPO_ROOT = Path(__file__).resolve().parents[1]
FIELDS = (
    "case_id",
    "applicant_name",
    "species_code",
    "home_world",
    "visa_class",
    "sponsor_id",
    "arrival_date",
    "declared_purpose",
    "risk_flags",
    "fee_status",
    "adjudication",
)


def truth(case_id, adjudication):
    return {
        "case_id": case_id,
        "applicant_name": "Zed Zarnax",
        "species_code": "ORION_GRAYS",
        "home_world": "Kepler-186f",
        "visa_class": "XW-2",
        "sponsor_id": "SPN-1042",
        "arrival_date": "2026-04-17",
        "declared_purpose": "research",
        "risk_flags": "none",
        "fee_status": "paid",
        "adjudication": adjudication,
    }


def prediction(row, *, adjudication=None, confidence=0.8, risk_flags=None):
    return PredictionRow.from_mapping(
        {
            **row,
            "risk_flags": risk_flags or row["risk_flags"],
            "adjudication": adjudication or row["adjudication"],
            "confidence": confidence,
        }
    )


class FixedProcessor:
    def __init__(self, rows):
        self._rows = rows

    def process_case(self, path):
        return self._rows[path.stem]


class AblationPlanTests(unittest.TestCase):
    def test_registry_is_stable_and_every_variant_changes_exactly_one_setting(self):
        variants = registered_variants()

        self.assertEqual(len(variants), 8)
        self.assertEqual(
            len({variant.variant_id for variant in variants}),
            len(variants),
        )
        baseline = {
            f"{section}.{name}": value
            for section, values in BASELINE_CONFIG.items()
            for name, value in values.items()
        }
        for variant in variants:
            candidate = {
                f"{section}.{name}": value
                for section, values in variant.config.items()
                for name, value in values.items()
            }
            differences = [
                key for key in baseline if baseline[key] != candidate[key]
            ]
            self.assertEqual(differences, [variant.changed_variable])
            self.assertEqual(variant.technique_enabled_in, "baseline")

    def test_two_variable_or_unregistered_change_is_rejected(self):
        changed = {
            section: dict(values) for section, values in BASELINE_CONFIG.items()
        }
        changed["primary"]["orientation_retry"] = False
        changed["primary"]["risk_geometry_retry"] = False
        with self.assertRaises(AblationConfigurationError):
            AblationVariant(
                variant_id="invalid",
                family="invalid",
                technique="invalid",
                changed_variable="primary.orientation_retry",
                config=changed,
                target_fields=("risk_flags",),
            )

    def test_baseline_factory_is_the_production_composition_root(self):
        baseline = build_production_processor()

        self.assertEqual(
            type(baseline),
            type(build_production_processor()),
        )


class LabelBlindRunTests(unittest.TestCase):
    def test_run_records_only_aggregate_runtime_evidence(self):
        rows = {
            "MIB-000001": prediction(truth("MIB-000001", "APPROVED")),
            "MIB-000002": prediction(truth("MIB-000002", "DENIED")),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            input_dir.mkdir()
            for case_id in rows:
                (input_dir / f"{case_id}.pdf").write_bytes(b"%PDF-fixture")
            observation = run_variant(
                variant_id="baseline",
                benchmark_id="fixture-v1",
                source_revision="a" * 40,
                repeat_index=1,
                input_dir=input_dir,
                predictions_path=root / "predictions.jsonl",
                observation_path=root / "observation.json",
                max_workers=1,
                processor_factory=lambda _variant: FixedProcessor(rows),
            )
            serialized = json.dumps(observation, sort_keys=True)

        self.assertEqual(observation["answered"], 2)
        self.assertEqual(observation["omitted"], 0)
        self.assertGreater(observation["cpu_seconds"], 0.0)
        self.assertNotIn("MIB-000001", serialized)
        self.assertNotIn("truth", serialized.casefold())


class AblationReportTests(unittest.TestCase):
    def _fixture(self):
        stack = tempfile.TemporaryDirectory()
        root = Path(stack.name)
        truth_path = root / "truth.csv"
        truths = [
            truth("MIB-000001", "APPROVED"),
            truth("MIB-000002", "DENIED"),
        ]
        with truth_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(truths)
        return stack, root, truth_path, truths

    @staticmethod
    def _write_predictions(path, rows):
        path.write_text(
            "".join(
                json.dumps(row.to_dict(), separators=(",", ":")) + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _observation(
        path,
        *,
        variant_id,
        repeat,
        predictions_path,
        cpu,
        config,
    ):
        import hashlib

        prediction_hash = hashlib.sha256(predictions_path.read_bytes()).hexdigest()
        value = {
            "schema_version": "mib_ocr_ablation_v1",
            "benchmark_id": "fixture-v1",
            "variant_id": variant_id,
            "repeat_index": repeat,
            "source_revision": "b" * 40,
            "config_sha256": config_sha256(config),
            "input_tree_sha256": "c" * 64,
            "input_pdf_count": 2,
            "max_workers": 1,
            "predictions_path": str(predictions_path),
            "predictions_sha256": prediction_hash,
            "attempted": 2,
            "answered": 2,
            "omitted": 0,
            "cpu_seconds": cpu,
            "wall_seconds": cpu / 2,
            "peak_memory_mib": 64.0,
            "metrics_source": (
                "fresh_process_rusage_self_plus_waited_children_and_monotonic_wall"
            ),
        }
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_report_ranks_positive_deterministic_safe_removal_contribution(self):
        stack, root, truth_path, truths = self._fixture()
        self.addCleanup(stack.cleanup)
        baseline_rows = [
            prediction(truths[0]),
            prediction(truths[1]),
        ]
        ablated_rows = [
            prediction(truths[0], risk_flags="active_warrant"),
            prediction(truths[1]),
        ]
        baseline_path = root / "baseline.jsonl"
        variant_path = root / "variant.jsonl"
        self._write_predictions(baseline_path, baseline_rows)
        self._write_predictions(variant_path, ablated_rows)
        variant = next(
            item
            for item in registered_variants()
            if item.variant_id == "without_risk_geometry_retry"
        )
        paths = []
        for repeat in (1, 2):
            paths.append(
                self._observation(
                    root / f"baseline-{repeat}.json",
                    variant_id="baseline",
                    repeat=repeat,
                    predictions_path=baseline_path,
                    cpu=10.0 + repeat,
                    config=BASELINE_CONFIG,
                )
            )
            paths.append(
                self._observation(
                    root / f"variant-{repeat}.json",
                    variant_id=variant.variant_id,
                    repeat=repeat,
                    predictions_path=variant_path,
                    cpu=8.0 + repeat,
                    config=variant.config,
                )
            )

        report = build_report(
            repo_root=REPO_ROOT,
            truth_path=truth_path,
            observation_paths=paths,
        )
        ranked = report["ranked_recommendations"]
        markdown = render_markdown(report)

        self.assertEqual(ranked[0]["variant_id"], variant.variant_id)
        entry = next(
            item
            for item in report["variants"]
            if item["variant_id"] == variant.variant_id
        )
        self.assertGreater(entry["technique_score_gain"], 0.0)
        self.assertGreater(entry["score_gain_per_cpu_second"], 0.0)
        self.assertEqual(
            entry["target_field_raw_point_deltas"]["risk_flags"],
            8.0,
        )
        self.assertTrue(entry["deterministic"])
        self.assertTrue(entry["safety_pass"])
        self.assertIn("not_measured", {item["evidence_status"] for item in report["variants"]})
        self.assertNotIn("MIB-000001", markdown)

    def test_non_deterministic_repetitions_are_not_recommended(self):
        stack, root, truth_path, truths = self._fixture()
        self.addCleanup(stack.cleanup)
        baseline_path = root / "baseline.jsonl"
        first_path = root / "first.jsonl"
        second_path = root / "second.jsonl"
        self._write_predictions(
            baseline_path,
            [prediction(truths[0]), prediction(truths[1])],
        )
        self._write_predictions(
            first_path,
            [
                prediction(truths[0], risk_flags="active_warrant"),
                prediction(truths[1]),
            ],
        )
        self._write_predictions(
            second_path,
            [
                prediction(truths[0], risk_flags="contraband_match"),
                prediction(truths[1]),
            ],
        )
        variant = next(
            item
            for item in registered_variants()
            if item.variant_id == "without_risk_geometry_retry"
        )
        paths = [
            self._observation(
                root / f"baseline-{repeat}.json",
                variant_id="baseline",
                repeat=repeat,
                predictions_path=baseline_path,
                cpu=10.0,
                config=BASELINE_CONFIG,
            )
            for repeat in (1, 2)
        ]
        paths.extend(
            [
                self._observation(
                    root / "variant-1.json",
                    variant_id=variant.variant_id,
                    repeat=1,
                    predictions_path=first_path,
                    cpu=9.0,
                    config=variant.config,
                ),
                self._observation(
                    root / "variant-2.json",
                    variant_id=variant.variant_id,
                    repeat=2,
                    predictions_path=second_path,
                    cpu=9.0,
                    config=variant.config,
                ),
            ]
        )

        report = build_report(
            repo_root=REPO_ROOT,
            truth_path=truth_path,
            observation_paths=paths,
        )
        entry = next(
            item
            for item in report["variants"]
            if item["variant_id"] == variant.variant_id
        )

        self.assertEqual(entry["evidence_status"], "insufficient_evidence")
        self.assertFalse(entry["recommendation_eligible"])

    def test_catastrophic_false_approval_in_enabled_technique_blocks_recommendation(self):
        stack, root, truth_path, truths = self._fixture()
        self.addCleanup(stack.cleanup)
        baseline_path = root / "baseline.jsonl"
        variant_path = root / "variant.jsonl"
        self._write_predictions(
            baseline_path,
            [
                prediction(truths[0]),
                prediction(truths[1], adjudication="APPROVED"),
            ],
        )
        self._write_predictions(
            variant_path,
            [prediction(truths[0]), prediction(truths[1])],
        )
        variant = next(
            item
            for item in registered_variants()
            if item.variant_id == "without_targeted_rapidocr"
        )
        paths = []
        for repeat in (1, 2):
            paths.extend(
                [
                    self._observation(
                        root / f"baseline-{repeat}.json",
                        variant_id="baseline",
                        repeat=repeat,
                        predictions_path=baseline_path,
                        cpu=11.0,
                        config=BASELINE_CONFIG,
                    ),
                    self._observation(
                        root / f"variant-{repeat}.json",
                        variant_id=variant.variant_id,
                        repeat=repeat,
                        predictions_path=variant_path,
                        cpu=9.0,
                        config=variant.config,
                    ),
                ]
            )

        report = build_report(
            repo_root=REPO_ROOT,
            truth_path=truth_path,
            observation_paths=paths,
        )
        entry = next(
            item
            for item in report["variants"]
            if item["variant_id"] == variant.variant_id
        )

        self.assertFalse(entry["safety_pass"])
        self.assertFalse(entry["recommendation_eligible"])


if __name__ == "__main__":
    unittest.main()
