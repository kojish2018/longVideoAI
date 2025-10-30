"""Classic (style1) thumbnail design implementation."""
from __future__ import annotations

from pathlib import Path
from typing import List, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from .base import ThumbnailContext, ThumbnailDesign
from .utils import compress_lines, max_text_width, measure_text


class ClassicThumbnailDesign(ThumbnailDesign):
    """Original black-band thumbnail layout preserved as style1."""

    name = "style1"

    def render(self, context: ThumbnailContext) -> Image.Image:  # noqa: D401 - see base class
        spec = context.spec
        canvas = Image.new("RGB", (spec.width, spec.height), "#000000")

        top_band_height = max(int(spec.height * spec.top_band_ratio), int(spec.title_font_size * 1.6))
        top_band = Image.new("RGB", (spec.width, top_band_height), "#000000")
        canvas.paste(top_band, (0, 0))

        hero_area_height = max(1, spec.height - top_band_height - spec.gap)
        hero_box_y = top_band_height + spec.gap
        hero_size = (spec.width, hero_area_height)
        hero_image = context.prepare_hero_image(context.base_image_path, hero_size)
        canvas.paste(hero_image, (0, hero_box_y))

        draw = ImageDraw.Draw(canvas)
        title_font = ImageFont.truetype(str(context.title_font_path), size=spec.title_font_size)

        title_lines, fitted_font = self._fit_text_lines(
            context.title,
            title_font,
            context.title_font_path,
            max_width=spec.width - 80,
            max_lines=3,
        )

        subtitle_lines: Sequence[str] = []
        subtitle_font: ImageFont.FreeTypeFont | None = None

        self._draw_text_block(
            draw,
            title_lines,
            fitted_font,
            subtitle_lines,
            subtitle_font,
            spec.width,
            top_band_height,
        )

        return canvas

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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
        while (len(lines) > max_lines or max_text_width(lines, current_font) > max_width) and attempts < 4:
            new_size = max(24, int(current_font.size * 0.9))
            current_font = ImageFont.truetype(str(font_path), size=new_size)
            lines = self._wrap_text(text, current_font, max_width)
            attempts += 1
        if len(lines) > max_lines:
            lines = compress_lines(lines, max_lines)
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
            w, _ = measure_text(font, candidate)
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
        subtitle_font: ImageFont.FreeTypeFont | None,
        canvas_width: int,
        top_band_height: int,
    ) -> None:
        line_spacing = int(title_font.size * 0.3)
        block_height = sum(measure_text(title_font, line)[1] for line in title_lines)
        if title_lines:
            block_height += line_spacing * (len(title_lines) - 1)

        if subtitle_lines and subtitle_font:
            block_height += int(title_font.size * 0.5)
            block_height += sum(measure_text(subtitle_font, line)[1] for line in subtitle_lines)
            block_height += int(subtitle_font.size * 0.25) * (len(subtitle_lines) - 1)

        y = max(0, (top_band_height - block_height) // 2)
        for line in title_lines:
            w, h = measure_text(title_font, line)
            draw.text(((canvas_width - w) / 2, y), line, font=title_font, fill=(255, 255, 255))
            y += h + line_spacing

        if subtitle_lines and subtitle_font:
            y += int(title_font.size * 0.2)
            for line in subtitle_lines:
                w, h = measure_text(subtitle_font, line)
                draw.text(((canvas_width - w) / 2, y), line, font=subtitle_font, fill=(255, 255, 255))
                y += h + int(subtitle_font.size * 0.25)
