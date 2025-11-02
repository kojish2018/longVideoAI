"""
Presentation-mode video generation package.

This package hosts the MVP pipeline for slide-style long videos that renders
prepared panel templates, character overlays, and narration subtitles.
"""

from __future__ import annotations

__all__ = [
    "load_presentation_script",
    "PresentationPipeline",
]

from .script_loader import load_presentation_script
from .pipeline import PresentationPipeline

