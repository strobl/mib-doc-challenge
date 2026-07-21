"""Visible-only OCR/CV extraction and untrusted-content vetoes."""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Protocol

from .ingestion import Rect, RenderedCase, RenderedPage
from .models import ADJUDICATION_VALUES, CASE_ID_PATTERN, FEE_VALUES, SPONSOR_ID_PATTERN


class RecoverableOcrError(RuntimeError):
    """OCR failed for one page/case and may be isolated by BatchRunner."""


class EvidenceType(str, Enum):
    ADJUDICATOR_STAMP = "adjudicator_stamp"
    INTAKE_FORM = "intake_form"
    BIOMETRIC_SLIP = "biometric_slip"
    SPONSOR_ATTESTATION = "sponsor_attestation"
    REGISTRY_EXTRACT = "registry_extract"


@dataclass(frozen=True)
class OcrToken:
    page_index: int
    text: str
    confidence: float
    box: Rect
    block_num: int
    paragraph_num: int
    line_num: int
    word_num: int


@dataclass(frozen=True)
class OcrLine:
    page_index: int
    text: str
    confidence: float
    box: Rect
    tokens: tuple[OcrToken, ...]


@dataclass(frozen=True)
class CandidateEvidence:
    field_name: str
    value: str | None
    evidence_type: EvidenceType
    page_index: int
    box: Rect
    legible: bool
    superseded: bool
    ocr_confidence: float
    visual_cues: tuple[str, ...] = ()
    source: str = "visible_ocr"


class RefinementModel(Protocol):
    def refine(
        self,
        page: RenderedPage,
        line: OcrLine,
    ) -> tuple[str, float] | None:
        """Optionally refine a visibly-present uncertain OCR line."""


class TesseractOcrEngine:
    """Bounded offline Tesseract TSV adapter using PNG bytes over stdin."""

    def __init__(
        self,
        *,
        binary: str = "tesseract",
        language: str = "eng",
        page_segmentation_mode: int = 6,
        timeout_seconds: float = 5.0,
    ) -> None:
        if not 1 <= page_segmentation_mode <= 13:
            raise ValueError("page_segmentation_mode must be between 1 and 13")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._binary = binary
        self._language = language
        self._psm = page_segmentation_mode
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def _parse_tsv(tsv_text: str, page_index: int) -> tuple[OcrToken, ...]:
        tokens: list[OcrToken] = []
        reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
        required = {
            "level",
            "left",
            "top",
            "width",
            "height",
            "conf",
            "text",
            "block_num",
            "par_num",
            "line_num",
            "word_num",
        }
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise RecoverableOcrError("Tesseract returned malformed TSV")
        for row in reader:
            text = (row.get("text") or "").strip()
            if not text or row.get("level") != "5":
                continue
            try:
                confidence_percent = float(row["conf"])
                left = int(row["left"])
                top = int(row["top"])
                width = int(row["width"])
                height = int(row["height"])
                block_num = int(row["block_num"])
                paragraph_num = int(row["par_num"])
                line_num = int(row["line_num"])
                word_num = int(row["word_num"])
            except (TypeError, ValueError) as exc:
                raise RecoverableOcrError("Tesseract returned invalid TSV values") from exc
            if confidence_percent < 0 or width <= 0 or height <= 0:
                continue
            tokens.append(
                OcrToken(
                    page_index=page_index,
                    text=text,
                    confidence=max(0.0, min(1.0, confidence_percent / 100.0)),
                    box=Rect(left, top, left + width, top + height),
                    block_num=block_num,
                    paragraph_num=paragraph_num,
                    line_num=line_num,
                    word_num=word_num,
                )
            )
        return tuple(tokens)

    def read_page(self, page: RenderedPage) -> tuple[OcrToken, ...]:
        executable = shutil.which(self._binary)
        if executable is None:
            raise RecoverableOcrError(f"Tesseract executable not found: {self._binary}")
        environment = os.environ.copy()
        environment["OMP_THREAD_LIMIT"] = "1"
        command = [
            executable,
            "stdin",
            "stdout",
            "--dpi",
            str(page.dpi),
            "-l",
            self._language,
            "--oem",
            "1",
            "--psm",
            str(self._psm),
            "tsv",
        ]
        try:
            completed = subprocess.run(
                command,
                input=page.image_png,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self._timeout_seconds,
                check=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise RecoverableOcrError(
                f"OCR timed out on page {page.index + 1}"
            ) from exc
        if completed.returncode != 0:
            message = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RecoverableOcrError(
                f"OCR failed on page {page.index + 1}: {message[:240]}"
            )
        return self._parse_tsv(
            completed.stdout.decode("utf-8", errors="replace"),
            page.index,
        )


def group_ocr_lines(tokens: Iterable[OcrToken]) -> tuple[OcrLine, ...]:
    grouped: dict[tuple[int, int, int, int], list[OcrToken]] = {}
    for token in tokens:
        key = (
            token.page_index,
            token.block_num,
            token.paragraph_num,
            token.line_num,
        )
        grouped.setdefault(key, []).append(token)
    lines: list[OcrLine] = []
    for key in sorted(grouped):
        line_tokens = tuple(sorted(grouped[key], key=lambda token: token.word_num))
        box = line_tokens[0].box
        for token in line_tokens[1:]:
            box = box.union(token.box)
        weight = sum(max(1, len(token.text)) for token in line_tokens)
        confidence = sum(
            token.confidence * max(1, len(token.text)) for token in line_tokens
        ) / weight
        lines.append(
            OcrLine(
                page_index=key[0],
                text=" ".join(token.text for token in line_tokens),
                confidence=confidence,
                box=box,
                tokens=line_tokens,
            )
        )
    return tuple(lines)


class UntrustedContentFilter:
    """Keep injected or non-visible material outside CandidateEvidence."""

    _CONTEXT_PATTERNS = (
        re.compile(r"\b(?:fake\s+)?(?:system\s+prompt|answer\s+key|ground\s+truth)\b", re.I),
        re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?\b", re.I),
        re.compile(r"\b(?:qr|bar\s*code)\b.*\b(?:instruction|policy|answer|decision)\b", re.I),
        re.compile(r"\b(?:assistant|chatgpt)\s*:\s*", re.I),
    )
    _LINE_PATTERNS = (
        re.compile(r"\bsample\s+denial\b", re.I),
        re.compile(r"\bdo\s+not\s+follow\s+(?:the\s+)?(?:form|policy)\b", re.I),
    )

    def context_quarantine_lines(self, text: str) -> int:
        for pattern in self._CONTEXT_PATTERNS:
            if pattern.search(text):
                return 12
        return 0

    def rejection_reason(self, text: str, cues: Iterable[str] = ()) -> str | None:
        cue_set = set(cues)
        if "sample_denial_watermark" in cue_set:
            return "sample denial watermark"
        for pattern in self._CONTEXT_PATTERNS + self._LINE_PATTERNS:
            if pattern.search(text):
                return "injected or decorative content"
        return None

    @staticmethod
    def allows_text_span(*, authoritative: bool, off_crop: bool) -> bool:
        return authoritative and not off_crop


class VisualCueDetector:
    """Detect lightweight visible cues without interpreting them as policy."""

    @staticmethod
    def _dependencies() -> tuple[Any, Any]:
        try:
            import numpy
            from PIL import Image
        except ImportError as exc:
            raise RecoverableOcrError("Pillow and numpy are required for visual cues") from exc
        return Image, numpy

    def prepare_page(self, grayscale: Any) -> Any:
        _, numpy_module = self._dependencies()
        return numpy_module.asarray(grayscale)

    @staticmethod
    def _has_strikethrough(pixels: Any, box: Rect, numpy_module: Any) -> bool:
        height, width = pixels.shape[:2]
        left = max(0, min(width, int(box.left)))
        right = max(0, min(width, int(box.right)))
        top = max(0, min(height, int(box.bottom)))
        bottom = max(0, min(height, int(box.top)))
        if right - left < 8 or bottom - top < 4:
            return False
        region = pixels[top:bottom, left:right]
        center_start = max(0, int(region.shape[0] * 0.35))
        center_end = min(region.shape[0], max(center_start + 1, int(region.shape[0] * 0.7)))
        center = region[center_start:center_end]
        row_coverage = (center < 100).mean(axis=1)
        return bool(row_coverage.size and float(row_coverage.max()) >= 0.72)

    def cues_for_line(self, line: OcrLine, page_pixels: Any) -> tuple[str, ...]:
        text = line.text.casefold()
        cues: set[str] = set()
        if "sample denial" in text:
            cues.add("sample_denial_watermark")
        if re.search(r"\b(?:corrected|amended|override|supersedes?)\b", text):
            cues.add("correction")
        if re.search(r"\b(?:adjudicator\s+stamp|mib\s+official\s+stamp)\b", text):
            cues.add("adjudicator_stamp")
        _, numpy_module = self._dependencies()
        pixels = (
            page_pixels
            if hasattr(page_pixels, "shape")
            else numpy_module.asarray(page_pixels)
        )
        if self._has_strikethrough(pixels, line.box, numpy_module):
            cues.add("strikethrough")
        return tuple(sorted(cues))


FIELD_ALIASES = {
    "case_id": ("case id", "mib case", "application id"),
    "applicant_name": ("applicant name", "full name", "name"),
    "species_code": ("species code", "species"),
    "home_world": ("home world", "homeworld", "origin world"),
    "visa_class": ("visa class", "visa"),
    "sponsor_id": ("sponsor id", "sponsor"),
    "arrival_date": ("arrival date", "date of arrival"),
    "declared_purpose": ("declared purpose", "purpose of visit", "purpose"),
    "risk_flags": ("risk flags", "risk flag", "flags"),
    "fee_status": ("fee status", "fee"),
    "adjudication": ("adjudication", "decision", "final status"),
}


class VisibleEvidenceExtractor:
    """Create field candidates only from OCR-confirmed visible page pixels."""

    def __init__(
        self,
        *,
        ocr_engine: TesseractOcrEngine | None = None,
        cue_detector: VisualCueDetector | None = None,
        content_filter: UntrustedContentFilter | None = None,
        refinement_model: RefinementModel | None = None,
        minimum_legible_confidence: float = 0.45,
        refinement_gate: float = 0.72,
    ) -> None:
        if not 0.0 <= minimum_legible_confidence <= refinement_gate <= 1.0:
            raise ValueError("confidence thresholds must satisfy 0 <= minimum <= gate <= 1")
        self._ocr = ocr_engine or TesseractOcrEngine()
        self._cues = cue_detector or VisualCueDetector()
        self._filter = content_filter or UntrustedContentFilter()
        self._refinement_model = refinement_model
        self._minimum_legible_confidence = minimum_legible_confidence
        self._refinement_gate = refinement_gate

    @staticmethod
    def _page_image(page: RenderedPage) -> Any:
        try:
            from PIL import Image
        except ImportError as exc:
            raise RecoverableOcrError("Pillow is required for visible extraction") from exc
        return Image.open(io.BytesIO(page.image_png)).convert("L")

    @staticmethod
    def _evidence_type(text: str, current: EvidenceType) -> EvidenceType:
        normalized = text.casefold()
        if "adjudicator" in normalized or "official stamp" in normalized:
            return EvidenceType.ADJUDICATOR_STAMP
        if "biometric" in normalized:
            return EvidenceType.BIOMETRIC_SLIP
        if "sponsor attestation" in normalized or "sponsor letter" in normalized:
            return EvidenceType.SPONSOR_ATTESTATION
        if "registry extract" in normalized or "registry record" in normalized:
            return EvidenceType.REGISTRY_EXTRACT
        if "intake form" in normalized or "application form" in normalized:
            return EvidenceType.INTAKE_FORM
        return current

    @staticmethod
    def _match_field(text: str) -> tuple[str, str] | None:
        normalized = " ".join(text.strip().split())
        aliases = sorted(
            (
                (alias, field_name)
                for field_name, field_aliases in FIELD_ALIASES.items()
                for alias in field_aliases
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        for alias, field_name in aliases:
            match = re.match(
                rf"^{re.escape(alias)}\b\s*(?::|#|=|-)?\s*(.*)$",
                normalized,
                flags=re.I,
            )
            if match:
                return field_name, match.group(1).strip()
        return None

    @staticmethod
    def _normalize_value(field_name: str, raw_value: str) -> str | None:
        value = " ".join(raw_value.strip().split())
        if not value:
            return None
        if field_name == "case_id":
            match = re.search(r"MIB\s*[-:]\s*([0-9]{6})", value, re.I)
            candidate = f"MIB-{match.group(1)}" if match else value.upper()
            return candidate if CASE_ID_PATTERN.fullmatch(candidate) else None
        if field_name == "sponsor_id":
            match = re.search(r"SPN\s*[-:]\s*([0-9]{4})", value, re.I)
            candidate = f"SPN-{match.group(1)}" if match else value.upper()
            return candidate if SPONSOR_ID_PATTERN.fullmatch(candidate) else None
        if field_name == "arrival_date":
            from datetime import datetime

            for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
                try:
                    return datetime.strptime(value, pattern).date().isoformat()
                except ValueError:
                    continue
            return None
        if field_name == "fee_status":
            candidate = value.casefold().replace(" ", "_")
            return candidate if candidate in FEE_VALUES else None
        if field_name == "adjudication":
            candidate = value.upper().replace(" ", "_")
            return candidate if candidate in ADJUDICATION_VALUES else None
        if field_name == "risk_flags":
            flags = [
                flag.strip().casefold().replace(" ", "_")
                for flag in re.split(r"[,;|]", value)
                if flag.strip()
            ]
            return "|".join(flags) if flags else None
        return value

    def extract(self, rendered_case: RenderedCase) -> tuple[CandidateEvidence, ...]:
        candidates: list[CandidateEvidence] = []
        evidence_type = EvidenceType.INTAKE_FORM
        for page in rendered_case.pages:
            tokens = self._ocr.read_page(page)
            lines = group_ocr_lines(tokens)
            page_image = self._page_image(page)
            page_visual = (
                self._cues.prepare_page(page_image)
                if hasattr(self._cues, "prepare_page")
                else page_image
            )
            quarantine_remaining = 0
            pending_field: tuple[str, OcrLine, tuple[str, ...], EvidenceType] | None = None
            for line in lines:
                cues = self._cues.cues_for_line(line, page_visual)
                quarantine = self._filter.context_quarantine_lines(line.text)
                if quarantine:
                    quarantine_remaining = max(quarantine_remaining, quarantine)
                if quarantine_remaining > 0:
                    quarantine_remaining -= 1
                    pending_field = None
                    continue
                if self._filter.rejection_reason(line.text, cues) is not None:
                    pending_field = None
                    continue

                evidence_type = self._evidence_type(line.text, evidence_type)
                matched = self._match_field(line.text)
                if pending_field is not None and matched is None:
                    field_name, label_line, label_cues, label_type = pending_field
                    matched = (field_name, line.text)
                    combined_box = label_line.box.union(line.box)
                    combined_cues = tuple(sorted(set(label_cues) | set(cues)))
                    source_line = OcrLine(
                        page_index=line.page_index,
                        text=f"{label_line.text} {line.text}",
                        confidence=min(label_line.confidence, line.confidence),
                        box=combined_box,
                        tokens=label_line.tokens + line.tokens,
                    )
                    candidate_type = label_type
                    pending_field = None
                elif matched is not None:
                    pending_field = None
                    source_line = line
                    combined_cues = cues
                    candidate_type = evidence_type
                else:
                    continue

                field_name, raw_value = matched
                if not raw_value:
                    pending_field = (field_name, line, cues, evidence_type)
                    continue

                confidence = source_line.confidence
                if (
                    self._refinement_model is not None
                    and confidence < self._refinement_gate
                ):
                    refined = self._refinement_model.refine(page, source_line)
                    if refined is not None and refined[1] > confidence:
                        raw_value, confidence = refined

                normalized = self._normalize_value(field_name, raw_value)
                legible = (
                    confidence >= self._minimum_legible_confidence
                    and normalized is not None
                )
                candidates.append(
                    CandidateEvidence(
                        field_name=field_name,
                        value=normalized if legible else None,
                        evidence_type=candidate_type,
                        page_index=page.index,
                        box=source_line.box,
                        legible=legible,
                        superseded="strikethrough" in combined_cues,
                        ocr_confidence=confidence,
                        visual_cues=combined_cues,
                    )
                )
        return tuple(candidates)
