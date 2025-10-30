"""Base classes and context for thumbnail designs."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Tuple

from PIL import Image

if TYPE_CHECKING:  # pragma: no cover - typing only
    from thumbnail_generator import ThumbnailSpec


@dataclass(slots=True)
class ThumbnailContext:
    """Rendering parameters passed to each thumbnail design."""

    title: str
    subtitle: Optional[str]
    base_image_path: Optional[Path]
    spec: "ThumbnailSpec"
    title_font_path: Path
    subtitle_font_path: Path
    prepare_hero_image: Callable[[Optional[Path], Tuple[int, int]], Image.Image]
    logger: logging.Logger


class ThumbnailDesign(ABC):
    """Interface for thumbnail design renderers."""

    name: str = ""

    @abstractmethod
    def render(self, context: ThumbnailContext) -> Image.Image:
        """Render and return a Pillow image for the thumbnail."""

    def supports_subtitle(self) -> bool:
        """Whether the design makes use of the subtitle field."""

        return False


__all__ = ["ThumbnailContext", "ThumbnailDesign"]
