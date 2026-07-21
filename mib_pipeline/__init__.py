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
from .resolution import (
    CaseLinker,
    EvidencePrecedenceHierarchy,
    EvidencePrecedenceResolver,
    FieldState,
    LinkedCase,
    RescindedDecisionHandler,
    ResolvedCase,
    ResolvedField,
)
from .pipeline import (
    ExtractThenFallbackProcessor,
    ProcessingPipeline,
    RenderFirstFallbackProcessor,
    ResolveThenFallbackProcessor,
    SafeFallbackProcessor,
)
from .writer import CanonicalJsonlWriter, DuplicateCaseIdError

__all__ = [
    "BatchRunReport",
    "BatchRunner",
    "CanonicalJsonlWriter",
    "CandidateEvidence",
    "CaseLinker",
    "DuplicateCaseIdError",
    "DocumentRenderer",
    "EvidenceType",
    "EvidencePrecedenceHierarchy",
    "EvidencePrecedenceResolver",
    "ExtractThenFallbackProcessor",
    "FIELD_NAMES",
    "FieldState",
    "LinkedCase",
    "PredictionRow",
    "ProcessingPipeline",
    "RecoverableRenderError",
    "RecoverableOcrError",
    "RescindedDecisionHandler",
    "Rect",
    "RenderedCase",
    "RenderedPage",
    "RenderFirstFallbackProcessor",
    "ResolveThenFallbackProcessor",
    "ResolvedCase",
    "ResolvedField",
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
