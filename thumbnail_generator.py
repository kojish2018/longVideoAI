"""Thumbnail generator for long-form video outputs."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from PIL import Image

from thumbnail_designs import (
    ClassicThumbnailDesign,
    Style2ThumbnailDesign,
    ThumbnailContext,
    ThumbnailDesign,
)
from thumbnail_designs.utils import fit_image

from logging_utils import get_logger

logger = get_logger(__name__)


@dataclass
class ThumbnailSpec:
    width: int = 1280
    height: int = 720
    title_font_size: int = 120
    subtitle_font_size: int = 64
    overlay_rgba: Optional[Tuple[int, int, int, int]] = None
    top_band_ratio: float = 0.28
    gap: int = 6


class ThumbnailGenerator:
    """Compose thumbnails using pluggable design implementations."""

    def __init__(self, config: Dict[str, object] | None = None) -> None:
        config = config or {}
        thumb_cfg = config.get("thumbnail", {}) if isinstance(config, dict) else {}
        output_cfg = config.get("output", {}) if isinstance(config, dict) else {}
        text_cfg = config.get("text", {}) if isinstance(config, dict) else {}

        width = int(thumb_cfg.get("width", 1280)) if thumb_cfg else 1280
        height = int(thumb_cfg.get("height", 720)) if thumb_cfg else 720
        title_size = int(thumb_cfg.get("title_font_size", 120)) if thumb_cfg else 120
        subtitle_size = int(thumb_cfg.get("subtitle_font_size", 64)) if thumb_cfg else 64
        overlay = _parse_color(thumb_cfg.get("overlay_color")) if thumb_cfg else None
        ratio = _parse_ratio(thumb_cfg.get("top_band_ratio"), default=0.28)
        gap = _parse_int(thumb_cfg.get("gap"), default=6, minimum=0)

        self.spec = ThumbnailSpec(
            width=width,
            height=height,
            title_font_size=title_size,
            subtitle_font_size=subtitle_size,
            overlay_rgba=overlay,
            top_band_ratio=ratio,
            gap=gap,
        )

        self.thumbnail_directory = _resolve_path(output_cfg.get("thumbnail_directory"))

        self.title_font_path = _resolve_font(
            thumb_cfg.get("title_font_path")
            or text_cfg.get("font_path")
            or "fonts/NotoSansJP-ExtraBold.ttf",
            fallback="fonts/NotoSansJP-ExtraBold.ttf",
        )
        self.subtitle_font_path = _resolve_font(
            thumb_cfg.get("subtitle_font_path")
            or text_cfg.get("font_path")
            or "fonts/NotoSansJP-Bold.ttf",
            fallback="fonts/NotoSansJP-Bold.ttf",
        )

        style_name = str(thumb_cfg.get("style", "style1")).strip().lower() if thumb_cfg else "style1"
        self.default_style = style_name or "style1"
        self._designs = _build_design_registry()
        if self.default_style not in self._designs:
            logger.warning(
                "Unknown configured thumbnail style '%s'; falling back to style1",
                self.default_style,
            )
            self.default_style = "style1"

    def available_styles(self) -> list[str]:
        """Return the list of supported style identifiers."""

        return sorted(self._designs.keys())

    def generate(
        self,
        *,
        title: str,
        base_image: Optional[Path],
        output_name: str,
        subtitle: Optional[str] = None,
        style: Optional[str] = None,
    ) -> Path:
        if not title:
            raise ValueError("Title is required for thumbnail generation")

        self.thumbnail_directory.mkdir(parents=True, exist_ok=True)
        output_path = self.thumbnail_directory / output_name

        design = self._resolve_design(style)
        context = ThumbnailContext(
            title=title,
            subtitle=subtitle if design.supports_subtitle() else None,
            base_image_path=base_image,
            spec=self.spec,
            title_font_path=self.title_font_path,
            subtitle_font_path=self.subtitle_font_path,
            prepare_hero_image=self._prepare_hero_image,
            logger=logger,
        )

        image = design.render(context)
        image.save(output_path, format="PNG")

        logger.info("Thumbnail saved: %s (style=%s)", output_path, design.name)
        return output_path

    def _resolve_design(self, style: Optional[str]) -> ThumbnailDesign:
        style_key = (style or self.default_style or "style1").strip().lower()
        design = self._designs.get(style_key)
        if design is None:
            logger.warning("Unknown thumbnail style '%s'; falling back to style1", style_key)
            design = self._designs.get("style1")
            if design is None:  # pragma: no cover - defensive
                raise ValueError("No thumbnail designs are registered")
        return design

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prepare_hero_image(self, image_path: Optional[Path], target_size: Tuple[int, int]) -> Image.Image:
        if image_path and image_path.exists():
            try:
                image = Image.open(image_path).convert("RGB")
            except OSError:
                logger.warning("Could not open %s; using fallback", image_path)
                image = Image.new("RGB", target_size, "#202020")
        else:
            if image_path:
                logger.warning("Thumbnail hero image missing: %s", image_path)
            image = Image.new("RGB", target_size, "#202020")

        fitted = fit_image(image, target_size)
        if self.spec.overlay_rgba and self.spec.overlay_rgba[3] > 0:
            overlay = Image.new("RGBA", target_size, self.spec.overlay_rgba)
            fitted = Image.alpha_composite(fitted.convert("RGBA"), overlay).convert("RGB")
        return fitted


def _build_design_registry() -> Dict[str, ThumbnailDesign]:
    designs: list[ThumbnailDesign] = [ClassicThumbnailDesign(), Style2ThumbnailDesign()]
    return {design.name.lower(): design for design in designs}
def _parse_color(value: object | None) -> Optional[Tuple[int, int, int, int]]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        if len(value) == 4:
            return tuple(int(c) for c in value)  # type: ignore[return-value]
        raise ValueError(f"Invalid color tuple: {value}")
    if not isinstance(value, str):
        return None

    text = value.strip()
    if text.startswith("#"):
        text = text.lstrip("#")
        if len(text) == 6:
            r = int(text[0:2], 16)
            g = int(text[2:4], 16)
            b = int(text[4:6], 16)
            return (r, g, b, 255)
        if len(text) == 8:
            r = int(text[0:2], 16)
            g = int(text[2:4], 16)
            b = int(text[4:6], 16)
            a = int(text[6:8], 16)
            return (r, g, b, a)
        raise ValueError(f"Invalid hex color: {value}")

    lower = text.lower()
    if lower.startswith("rgba"):
        numbers = lower.lower().replace("rgba", "").strip("() ")
        parts = [p.strip() for p in numbers.split(",") if p.strip()]
        if len(parts) != 4:
            raise ValueError(f"Invalid rgba color: {value}")
        r, g, b = (int(float(parts[i])) for i in range(3))
        alpha_part = parts[3]
        alpha = float(alpha_part)
        if 0 <= alpha <= 1:
            a = int(round(alpha * 255))
        else:
            a = int(alpha)
        return (r, g, b, max(0, min(a, 255)))
    return None


def _resolve_path(path_value: object | None) -> Path:
    if not path_value:
        return (Path.cwd() / "output" / "thumbnails").resolve()
    path = Path(str(path_value)).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _resolve_font(primary: object | None, *, fallback: str) -> Path:
    if isinstance(primary, str) and primary:
        candidate_list = [primary, fallback, "fonts/NotoSansJP-ExtraBold.ttf", "fonts/NotoSansJP-Bold.ttf"]
    else:
        candidate_list = [fallback, "fonts/NotoSansJP-ExtraBold.ttf", "fonts/NotoSansJP-Bold.ttf"]
    for candidate in candidate_list:
        path = Path(candidate).expanduser()
        if path.is_absolute() and path.exists():
            return path
        resolved = (Path.cwd() / path).resolve()
        if resolved.exists():
            return resolved
    raise FileNotFoundError(f"Font file not found. Tried: {candidate_list}")


def _parse_ratio(value: object | None, *, default: float) -> float:
    try:
        if value is None:
            raise ValueError
        ratio = float(value)
    except (TypeError, ValueError):
        ratio = default
    return max(0.15, min(ratio, 0.5))


def _parse_int(value: object | None, *, default: int, minimum: int = 0) -> int:
    try:
        if value is None:
            raise ValueError
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)
