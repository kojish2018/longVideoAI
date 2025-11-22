"""Utilities for building subtitle overlay images reused across renderers."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Tuple

from PIL import Image, ImageDraw, ImageFont

from .config import LayoutConfig
from .script_loader import SubtitleChunk


@dataclass
class SubtitleOverlayFactory:
    layout: LayoutConfig
    overlay_dir: Path
    _font_cache: Dict[Tuple[int, bool], ImageFont.FreeTypeFont] = field(default_factory=dict)
    _overlay_cache: Dict[Tuple[Tuple[str, ...], int], Path] = field(default_factory=dict)

    def create_overlay(self, chunk: SubtitleChunk, duration: float) -> tuple[Path, int]:
        lines_tuple = tuple(chunk.lines)
        cache_key = (lines_tuple, int(round(duration * 1000)))
        if cache_key in self._overlay_cache:
            cached = self._overlay_cache[cache_key]
            try:
                height = Image.open(cached).height
            except Exception:
                height = self.layout.subtitle_font_size * max(len(chunk.lines), 1)
            return cached, height

        font = self._get_font(self.layout.subtitle_font_size)
        multi_line = len(chunk.lines) > 1
        line_spacing = int(font.size * (0.44 if multi_line else 0.25))

        text_sizes = [self._measure_text(font, line) for line in chunk.lines]
        text_block_height = sum(size[1] for size in text_sizes)
        if multi_line:
            text_block_height += line_spacing * (len(chunk.lines) - 1)

        padding = self.layout.subtitle_padding_px
        radius = self.layout.subtitle_radius_px
        margin_bottom = self.layout.subtitle_margin_bottom_px

        band_height = text_block_height + padding * 2
        image = Image.new(
            "RGBA",
            (self.layout.width, band_height + margin_bottom),
            (0, 0, 0, 0),
        )
        draw = ImageDraw.Draw(image, "RGBA")

        rect = [(8, 8), (self.layout.width - 8, band_height)]
        draw.rounded_rectangle(rect, radius=radius, fill=self.layout.subtitle_band_color)

        content_width = self.layout.width - 16
        y = 8 + max((band_height - text_block_height) // 2, 0)
        for idx, (line, (text_width, text_height)) in enumerate(zip(chunk.lines, text_sizes)):
            x = 8 + max(int((content_width - text_width) / 2), 0)
            draw.text((x, y), line, font=font, fill=self.layout.subtitle_color)
            y += text_height
            if idx < len(chunk.lines) - 1:
                y += line_spacing

        self.overlay_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.overlay_dir / f"overlay_{chunk.index:03d}_{abs(hash(cache_key))}.png"
        image.save(output_path, format="PNG")
        self._overlay_cache[cache_key] = output_path
        return output_path, image.height

    def _get_font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        cache_key = (size, bold)
        if cache_key in self._font_cache:
            return self._font_cache[cache_key]

        preferred = self.layout.subtitle_font_path
        try:
            if preferred and Path(preferred).exists():
                font = ImageFont.truetype(str(preferred), size=size)
            else:
                fallback_name = "NotoSansJP-ExtraBold.ttf" if bold else "NotoSansJP-Bold.ttf"
                fallback_path = Path("fonts") / fallback_name
                if fallback_path.exists():
                    font = ImageFont.truetype(str(fallback_path), size=size)
                else:
                    font = ImageFont.truetype("DejaVuSans.ttf", size=size)
        except OSError:
            font = ImageFont.load_default()
        self._font_cache[cache_key] = font
        return font

    @staticmethod
    def _measure_text(font: ImageFont.FreeTypeFont, text: str) -> Tuple[int, int]:
        try:
            bbox = font.getbbox(text)
            return bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            return font.getsize(text)
