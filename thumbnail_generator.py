"""Thumbnail generator for long-form video outputs."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from logging_utils import get_logger

logger = get_logger(__name__)


if hasattr(Image, "Resampling"):
    _RESAMPLE = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
else:  # pragma: no cover - Pillow < 9.1 fallback
    _RESAMPLE = Image.LANCZOS  # type: ignore[attr-defined]


@dataclass
class ThumbnailSpec:
    width: int = 1280
    height: int = 720
    title_font_size: int = 72
    subtitle_font_size: int = 54
    overlay_rgba: Optional[Tuple[int, int, int, int]] = None
    top_band_ratio: float = 0.42
    gap: int = 8


class ThumbnailGenerator:
    """Compose title band and hero image into a thumbnail."""

    def __init__(self, config: Dict[str, object] | None = None) -> None:
        config = config or {}
        thumb_cfg = config.get("thumbnail", {}) if isinstance(config, dict) else {}
        output_cfg = config.get("output", {}) if isinstance(config, dict) else {}
        text_cfg = config.get("text", {}) if isinstance(config, dict) else {}

        width = int(thumb_cfg.get("width", 1280)) if thumb_cfg else 1280
        height = int(thumb_cfg.get("height", 720)) if thumb_cfg else 720
        title_size = int(thumb_cfg.get("title_font_size", 72)) if thumb_cfg else 72
        subtitle_size = int(thumb_cfg.get("subtitle_font_size", 54)) if thumb_cfg else 54
        overlay = _parse_color(thumb_cfg.get("overlay_color")) if thumb_cfg else None

        self.spec = ThumbnailSpec(
            width=width,
            height=height,
            title_font_size=title_size,
            subtitle_font_size=subtitle_size,
            overlay_rgba=overlay,
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

    def generate(
        self,
        *,
        title: str,
        base_image: Optional[Path],
        output_name: str,
        subtitle: Optional[str] = None,
    ) -> Path:
        if not title:
            raise ValueError("Title is required for thumbnail generation")

        self.thumbnail_directory.mkdir(parents=True, exist_ok=True)
        output_path = self.thumbnail_directory / output_name

        canvas = Image.new("RGB", (self.spec.width, self.spec.height), "#000000")
        top_band_height = max(int(self.spec.height * self.spec.top_band_ratio), self.spec.title_font_size * 2)
        top_band = Image.new("RGB", (self.spec.width, top_band_height), "#000000")
        canvas.paste(top_band, (0, 0))

        hero_area_height = max(1, self.spec.height - top_band_height - self.spec.gap)
        hero_box = (0, top_band_height + self.spec.gap)
        hero_size = (self.spec.width, hero_area_height)
        hero_image = self._prepare_hero_image(base_image, hero_size)
        canvas.paste(hero_image, hero_box)

        draw = ImageDraw.Draw(canvas)
        title_font = ImageFont.truetype(str(self.title_font_path), size=self.spec.title_font_size)

        title_lines, fitted_font = self._fit_text_lines(
            title,
            title_font,
            self.title_font_path,
            max_width=self.spec.width - 80,
            max_lines=3,
        )

        subtitle_lines: Sequence[str] = []
        subtitle_font = None
        if subtitle:
            subtitle_font = ImageFont.truetype(str(self.subtitle_font_path), size=self.spec.subtitle_font_size)
            subtitle_lines, subtitle_font = self._fit_text_lines(
                subtitle,
                subtitle_font,
                self.subtitle_font_path,
                max_width=self.spec.width - 120,
                max_lines=2,
            )

        self._draw_text_block(
            draw,
            title_lines,
            fitted_font,
            subtitle_lines,
            subtitle_font,
            top_band_height,
        )

        canvas.save(output_path, format="PNG")
        logger.info("Thumbnail saved: %s", output_path)
        return output_path

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

        fitted = _fit_image(image, target_size)
        if self.spec.overlay_rgba and self.spec.overlay_rgba[3] > 0:
            overlay = Image.new("RGBA", target_size, self.spec.overlay_rgba)
            fitted = Image.alpha_composite(fitted.convert("RGBA"), overlay).convert("RGB")
        return fitted

    def _fit_text_lines(
        self,
        text: str,
        font: ImageFont.FreeTypeFont,
        font_path: Path,
        *,
        max_width: int,
        max_lines: int,
    ) -> Tuple[List[str], ImageFont.FreeTypeFont]:
        lines = self._wrap_text(text, font, max_width)
        current_font = font
        attempts = 0
        while (len(lines) > max_lines or _max_text_width(lines, current_font) > max_width) and attempts < 4:
            new_size = max(24, int(current_font.size * 0.9))
            current_font = ImageFont.truetype(str(font_path), size=new_size)
            lines = self._wrap_text(text, current_font, max_width)
            attempts += 1
        if len(lines) > max_lines:
            lines = _compress_lines(lines, max_lines)
        return lines, current_font

    def _wrap_text(
        self,
        text: str,
        font: ImageFont.FreeTypeFont,
        max_width: int,
    ) -> List[str]:
        if not text:
            return [""]
        lines: List[str] = []
        buffer = ""
        for char in text:
            candidate = buffer + char
            w, _ = _measure_text(font, candidate)
            if w <= max_width:
                buffer = candidate
                continue
            if buffer:
                lines.append(buffer)
                buffer = char
            else:
                lines.append(char)
                buffer = ""
        if buffer:
            lines.append(buffer)
        return lines

    def _draw_text_block(
        self,
        draw: ImageDraw.ImageDraw,
        title_lines: Sequence[str],
        title_font: ImageFont.FreeTypeFont,
        subtitle_lines: Sequence[str],
        subtitle_font: Optional[ImageFont.FreeTypeFont],
        top_band_height: int,
    ) -> None:
        padding_top = 36
        line_spacing = int(title_font.size * 0.3)

        block_height = sum(_measure_text(title_font, line)[1] for line in title_lines)
        block_height += line_spacing * (len(title_lines) - 1 if title_lines else 0)

        if subtitle_lines and subtitle_font:
            block_height += int(title_font.size * 0.5)
            block_height += sum(_measure_text(subtitle_font, line)[1] for line in subtitle_lines)
            block_height += int(subtitle_font.size * 0.25) * (len(subtitle_lines) - 1)

        y = max(padding_top, (top_band_height - block_height) // 2)
        for line in title_lines:
            w, h = _measure_text(title_font, line)
            draw.text(((self.spec.width - w) / 2, y), line, font=title_font, fill=(255, 255, 255))
            y += h + line_spacing

        if subtitle_lines and subtitle_font:
            y += int(title_font.size * 0.2)
            for line in subtitle_lines:
                w, h = _measure_text(subtitle_font, line)
                draw.text(((self.spec.width - w) / 2, y), line, font=subtitle_font, fill=(255, 255, 255))
                y += h + int(subtitle_font.size * 0.25)


def _fit_image(image: Image.Image, target: Tuple[int, int]) -> Image.Image:
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


def _max_text_width(lines: Sequence[str], font: ImageFont.FreeTypeFont) -> int:
    widths = [_measure_text(font, line)[0] for line in lines]
    return max(widths) if widths else 0


def _measure_text(font: ImageFont.FreeTypeFont, text: str) -> Tuple[int, int]:
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:  # pragma: no cover - very old Pillow
        return font.getsize(text)


def _compress_lines(lines: Sequence[str], max_lines: int) -> List[str]:
    if max_lines <= 0:
        return []
    if len(lines) <= max_lines:
        return list(lines)
    compact = list(lines[: max_lines - 1])
    compact.append("".join(lines[max_lines - 1 :]))
    return compact


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
