"""Thumbnail design implementations for the long-form pipeline."""

from .base import ThumbnailContext, ThumbnailDesign
from .classic import ClassicThumbnailDesign
from .style2 import Style2ThumbnailDesign

__all__ = [
    "ThumbnailContext",
    "ThumbnailDesign",
    "ClassicThumbnailDesign",
    "Style2ThumbnailDesign",
]
