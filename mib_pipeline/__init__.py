"""Offline MIB document-processing pipeline."""

from .batch import BatchRunReport, BatchRunner, discover_case_pdfs
from .models import FIELD_NAMES, PredictionRow, RowValidationError
from .pipeline import ProcessingPipeline, SafeFallbackProcessor
from .writer import CanonicalJsonlWriter, DuplicateCaseIdError

__all__ = [
    "BatchRunReport",
    "BatchRunner",
    "CanonicalJsonlWriter",
    "DuplicateCaseIdError",
    "FIELD_NAMES",
    "PredictionRow",
    "ProcessingPipeline",
    "RowValidationError",
    "SafeFallbackProcessor",
    "discover_case_pdfs",
]
