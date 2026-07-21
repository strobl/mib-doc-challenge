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
from .models import FIELD_NAMES, PredictionRow, RowValidationError
from .pipeline import (
    ProcessingPipeline,
    RenderFirstFallbackProcessor,
    SafeFallbackProcessor,
)
from .writer import CanonicalJsonlWriter, DuplicateCaseIdError

__all__ = [
    "BatchRunReport",
    "BatchRunner",
    "CanonicalJsonlWriter",
    "DuplicateCaseIdError",
    "DocumentRenderer",
    "FIELD_NAMES",
    "PredictionRow",
    "ProcessingPipeline",
    "RecoverableRenderError",
    "Rect",
    "RenderedCase",
    "RenderedPage",
    "RenderFirstFallbackProcessor",
    "RowValidationError",
    "SafeFallbackProcessor",
    "TextLayerReader",
    "TextSpan",
    "discover_case_pdfs",
]
