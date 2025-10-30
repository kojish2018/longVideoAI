"""Utility helpers shared across thumbnail designs."""
from __future__ import annotations

from typing import Iterable, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

if hasattr(Image, "Resampling"):
    _RESAMPLE = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
else:  # pragma: no cover - Pillow < 9.1 fallback
    _RESAMPLE = Image.LANCZOS  # type: ignore[attr-defined]


def fit_image(image: Image.Image, target: Tuple[int, int]) -> Image.Image:
    """Resize and crop an image to fit the target rectangle."""

    target_w, target_h = target
    if target_w <= 0 or target_h <= 0:
        raise ValueError("Target size must be positive")

    src_w, src_h = image.size
    if src_w == 0 or src_h == 0:
        return Image.new("RGB", target, "#202020")

    scale = max(target_w / src_w, target_h / src_h)
    new_size = (max(1, int(src_w * scale)), max(1, int(src_h * scale)))
    resized = image.resize(new_size, _RESAMPLE)

    left = max(0, (resized.width - target_w) // 2)
    top = max(0, (resized.height - target_h) // 2)
    right = left + target_w
    bottom = top + target_h
    return resized.crop((left, top, right, bottom))


def measure_text(font: ImageFont.FreeTypeFont, text: str) -> Tuple[int, int]:
    """Return width and height for the given text."""

    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:  # pragma: no cover - legacy pillow fallback
        return font.getsize(text)


def max_text_width(lines: Sequence[str], font: ImageFont.FreeTypeFont) -> int:
    widths = [measure_text(font, line)[0] for line in lines]
    return max(widths) if widths else 0


def compress_lines(lines: Sequence[str], max_lines: int) -> list[str]:
    if max_lines <= 0:
        return []
    if len(lines) <= max_lines:
        return list(lines)
    compact = list(lines[: max_lines - 1])
    compact.append("".join(lines[max_lines - 1 :]))
    return compact


def draw_text_with_stroke(
    draw: ImageDraw.ImageDraw,
    *,
    xy: Tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: Tuple[int, int, int] | Tuple[int, int, int, int],
    stroke_fill: Tuple[int, int, int] | Tuple[int, int, int, int] = (0, 0, 0),
    stroke_width: int = 0,
) -> None:
    """Convenience wrapper for stroked text drawing."""

    draw.text(xy, text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)


def ensure_iterable(value: str | Iterable[str]) -> Iterable[str]:
    if isinstance(value, str):
        return (value,)
    return value
