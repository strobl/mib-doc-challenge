"""Decision-correctness signals and pinned isotonic confidence calibration."""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .adjudication import DecisionTrace


PINNED_ARTIFACT_PATH = (
    Path(__file__).resolve().parent / "artifacts" / "confidence_calibration.json"
)


class CalibrationArtifactError(ValueError):
    """The pinned calibration artifact is absent, malformed, or unsafe."""


@dataclass(frozen=True)
class PinnedIsotonicMap:
    """A validated monotonic step map fitted outside the submitted runtime."""

    artifact_id: str
    breakpoints: tuple[float, ...]
    probabilities: tuple[float, ...]
    fit_metadata: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PinnedIsotonicMap":
        allowed_keys = {
            "schema_version",
            "artifact_id",
            "breakpoints",
            "probabilities",
            "fit_metadata",
        }
        if set(value) != allowed_keys or value.get("schema_version") != 1:
            raise CalibrationArtifactError("unsupported calibration artifact schema")
        artifact_id = value.get("artifact_id")
        breakpoints = value.get("breakpoints")
        probabilities = value.get("probabilities")
        metadata = value.get("fit_metadata")
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            raise CalibrationArtifactError("artifact_id must be a non-empty string")
        if not isinstance(breakpoints, list) or not isinstance(probabilities, list):
            raise CalibrationArtifactError("isotonic arrays must be JSON lists")
        if len(breakpoints) != len(probabilities) or len(breakpoints) < 2:
            raise CalibrationArtifactError("isotonic arrays must have equal useful length")
        if not isinstance(metadata, dict):
            raise CalibrationArtifactError("fit_metadata must be an object")
        try:
            xs = tuple(float(item) for item in breakpoints)
            ys = tuple(float(item) for item in probabilities)
        except (TypeError, ValueError) as exc:
            raise CalibrationArtifactError("isotonic values must be numeric") from exc
        if not all(math.isfinite(item) and 0.0 <= item <= 1.0 for item in xs + ys):
            raise CalibrationArtifactError("isotonic values must be finite in [0,1]")
        if any(right <= left for left, right in zip(xs, xs[1:])):
            raise CalibrationArtifactError("isotonic breakpoints must strictly increase")
        if any(right < left for left, right in zip(ys, ys[1:])):
            raise CalibrationArtifactError("isotonic probabilities must not decrease")
        if xs[0] != 0.0 or xs[-1] != 1.0:
            raise CalibrationArtifactError("isotonic breakpoints must span [0,1]")
        return cls(
            artifact_id=artifact_id,
            breakpoints=xs,
            probabilities=ys,
            fit_metadata=dict(metadata),
        )

    @classmethod
    def from_path(cls, path: Path) -> "PinnedIsotonicMap":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CalibrationArtifactError(
                f"cannot load calibration artifact: {path}"
            ) from exc
        if not isinstance(value, dict):
            raise CalibrationArtifactError("calibration artifact must be an object")
        return cls.from_mapping(value)

    def predict(self, raw_signal: float) -> float:
        if not math.isfinite(raw_signal):
            raise ValueError("raw decision signal must be finite")
        bounded = max(0.0, min(1.0, float(raw_signal)))
        index = max(0, bisect_right(self.breakpoints, bounded) - 1)
        return self.probabilities[index]


class DecisionSignalModel:
    """Estimate decision strength from policy semantics, never OCR confidence."""

    _HARD_DENIAL_PREFIXES = (
        "barred_sponsor:",
        "disqualifying_flag:",
        "stay_limit_exceeded:",
    )
    _HARD_DENIAL_REASONS = frozenset(
        {
            "biohazard_red",
            "required_sponsor_absent",
            "stale_application",
            "transit_work_authorization",
            "unpaid_without_valid_waiver",
        }
    )

    @staticmethod
    def _has_prefix(reasons: tuple[str, ...], prefixes: tuple[str, ...]) -> bool:
        return any(reason.startswith(prefixes) for reason in reasons)

    def raw_signal(self, trace: DecisionTrace) -> float:
        """Return a deterministic pre-calibration P(correct-decision) signal."""

        if trace.authoritative_source:
            return 0.98

        if trace.decision == "DENIED":
            hard_reason = bool(set(trace.denial_reasons) & self._HARD_DENIAL_REASONS)
            hard_reason = hard_reason or self._has_prefix(
                trace.denial_reasons, self._HARD_DENIAL_PREFIXES
            )
            base = 0.93 if hard_reason else 0.84
            corroboration = min(0.05, max(0, len(trace.denial_reasons) - 1) * 0.015)
            return min(0.99, base + corroboration)

        if trace.decision == "NEEDS_REVIEW":
            reasons = trace.review_reasons
            if self._has_prefix(reasons, ("contested_field:",)):
                base = 0.94
            elif self._has_prefix(reasons, ("unresolved_linkage:",)):
                base = 0.92
            elif self._has_prefix(reasons, ("review_flag:",)):
                base = 0.88
            elif any("not_visible" in reason for reason in reasons):
                base = 0.83
            else:
                base = 0.74
            corroboration = min(0.06, max(0, len(reasons) - 1) * 0.01)
            return min(0.98, base + corroboration)

        if trace.decision == "APPROVED":
            supporting_facts = len(
                [
                    fact
                    for fact in trace.approval_facts
                    if fact != "strict_approval_bar_cleared"
                ]
            )
            return min(0.93, 0.78 + min(6, supporting_facts) * 0.025)

        raise ValueError(f"unsupported adjudication in decision trace: {trace.decision}")


class ConfidenceCalibrator:
    """Map a policy decision trace to P(the emitted adjudication is correct)."""

    def __init__(
        self,
        isotonic_map: PinnedIsotonicMap,
        *,
        signal_model: DecisionSignalModel | None = None,
    ) -> None:
        self._map = isotonic_map
        self._signal_model = signal_model or DecisionSignalModel()

    @classmethod
    def from_pinned_artifact(
        cls,
        path: Path = PINNED_ARTIFACT_PATH,
    ) -> "ConfidenceCalibrator":
        return cls(PinnedIsotonicMap.from_path(path))

    @property
    def artifact_id(self) -> str:
        return self._map.artifact_id

    def raw_signal(self, trace: DecisionTrace) -> float:
        return self._signal_model.raw_signal(trace)

    def calibrate(self, trace: DecisionTrace) -> float:
        """Return bounded calibrated confidence for the chosen decision."""

        return self._map.predict(self.raw_signal(trace))
