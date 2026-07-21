"""Offline MIB document-processing pipeline."""

from .batch import BatchRunReport, BatchRunner, discover_case_pdfs
from .ingestion import (
    DocumentRenderer,
    RecoverableRenderError,
    Rect,
    RenderedCase,
    RenderedPage,
    TextLayerReader,
    TextSpan,
)
from .extraction import (
    CandidateEvidence,
    EvidenceType,
    OcrLine,
    OcrToken,
    RecoverableOcrError,
    TesseractOcrEngine,
    UntrustedContentFilter,
    VisibleEvidenceExtractor,
    VisualCueDetector,
    group_ocr_lines,
)
from .models import FIELD_NAMES, PredictionRow, RowValidationError
from .pipeline import (
    ExtractThenFallbackProcessor,
    ProcessingPipeline,
    RenderFirstFallbackProcessor,
    SafeFallbackProcessor,
)
from .writer import CanonicalJsonlWriter, DuplicateCaseIdError

__all__ = [
    "BatchRunReport",
    "BatchRunner",
    "CanonicalJsonlWriter",
    "CandidateEvidence",
    "DuplicateCaseIdError",
    "DocumentRenderer",
    "EvidenceType",
    "ExtractThenFallbackProcessor",
    "FIELD_NAMES",
    "PredictionRow",
    "ProcessingPipeline",
    "RecoverableRenderError",
    "RecoverableOcrError",
    "Rect",
    "RenderedCase",
    "RenderedPage",
    "RenderFirstFallbackProcessor",
    "RowValidationError",
    "SafeFallbackProcessor",
    "TextLayerReader",
    "TextSpan",
    "TesseractOcrEngine",
    "UntrustedContentFilter",
    "VisibleEvidenceExtractor",
    "VisualCueDetector",
    "discover_case_pdfs",
    "group_ocr_lines",
    "OcrLine",
    "OcrToken",
]
