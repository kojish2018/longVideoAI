"""Bold headline thumbnail design (style2)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

from .base import ThumbnailContext, ThumbnailDesign
from .utils import draw_text_with_stroke, measure_text


@dataclass(slots=True)
class _LineStyle:
    font_path: str
    base_font_size: int
    max_width_ratio: float
    fill: Tuple[int, int, int]
    stroke_fill: Tuple[int, int, int]
    stroke_width: int
    box_fill: Tuple[int, int, int, int] | None
    box_padding: Tuple[int, int]
    box_radius: int
    shadow_offset: Tuple[int, int]
    shadow_fill: Tuple[int, int, int, int] | None


class Style2ThumbnailDesign(ThumbnailDesign):
    """Thumbnail layout inspired by bold Japanese financial thumbnails."""

    name = "style2"

    BACKGROUND = (6, 17, 64, 255)
    ANCHOR_RATIOS = (0.16, 0.45, 0.74)
    BOX_HEIGHT_RATIOS = (0.28, 0.34, 0.26)
    BOX_WIDTH_RATIOS = (0.9, 0.96, 0.88)

    def render(self, context: ThumbnailContext) -> Image.Image:  # noqa: D401 - see base class
        spec = context.spec
        base_image = Image.new("RGBA", (spec.width, spec.height), self.BACKGROUND)
        draw = ImageDraw.Draw(base_image)

        lines = self._split_lines(context.title)
        styles = self._line_styles(context, len(lines))

        overlay = Image.new("RGBA", base_image.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)

        anchor_positions = self._resolve_anchor_positions(len(lines), spec.height)
        target_boxes = self._resolve_target_boxes(len(lines), spec.width, spec.height)

        for idx, (line, style) in enumerate(zip(lines, styles)):
            box_width, box_height = target_boxes[idx]
            inner_width = max(1, box_width - style.box_padding[0] * 2)
            inner_height = max(1, box_height - style.box_padding[1] * 2)

            font = self._fit_font_to_box(
                text=line,
                font_path=style.font_path,
                base_size=style.base_font_size,
                max_width=inner_width,
                max_height=inner_height,
            )

            text_width, text_height = measure_text(font, line or " ")
            box_center_x = spec.width / 2
            box_center_y = anchor_positions[idx]
            box_left = box_center_x - box_width / 2
            box_top = box_center_y - box_height / 2

            if style.shadow_fill and style.shadow_offset != (0, 0):
                shadow_box = [
                    int(box_left + style.shadow_offset[0]),
                    int(box_top + style.shadow_offset[1]),
                    int(box_left + style.shadow_offset[0] + box_width),
                    int(box_top + style.shadow_offset[1] + box_height),
                ]
                overlay_draw.rounded_rectangle(shadow_box, radius=style.box_radius, fill=style.shadow_fill)

            if style.box_fill:
                box_rect = [
                    int(box_left),
                    int(box_top),
                    int(box_left + box_width),
                    int(box_top + box_height),
                ]
                overlay_draw.rounded_rectangle(box_rect, radius=style.box_radius, fill=style.box_fill)

            inner_left = box_left + style.box_padding[0]
            inner_top = box_top + style.box_padding[1]
            inner_width = max(1, box_width - style.box_padding[0] * 2)
            inner_height = max(1, box_height - style.box_padding[1] * 2)

            text_x = inner_left + (inner_width - text_width) / 2
            text_y = inner_top + (inner_height - text_height) / 2

            draw_text_with_stroke(
                draw,
                xy=(text_x, text_y),
                text=line,
                font=font,
                fill=style.fill,
                stroke_fill=style.stroke_fill,
                stroke_width=style.stroke_width,
            )

        composed = Image.alpha_composite(base_image, overlay)
        return composed.convert("RGB")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _split_lines(self, title: str) -> List[str]:
        if not title:
            return [""]
        normalized = [line.strip() for line in title.replace("\r", "\n").split("\n") if line.strip()]
        if len(normalized) <= 1 and "|" in title:
            normalized = [segment.strip() for segment in title.split("|") if segment.strip()]
        if not normalized:
            normalized = [title.strip() or ""]
        return normalized[:3]

    def _line_styles(self, context: ThumbnailContext, line_count: int) -> List[_LineStyle]:
        spec = context.spec
        base_styles: List[_LineStyle] = [
            _LineStyle(
                font_path=str(context.title_font_path),
                base_font_size=int(spec.title_font_size * 0.92),
                max_width_ratio=0.92,
                fill=(255, 230, 0),
                stroke_fill=(10, 20, 90),
                stroke_width=14,
                box_fill=None,
                box_padding=(12, 8),
                box_radius=18,
                shadow_offset=(0, 0),
                shadow_fill=None,
            ),
            _LineStyle(
                font_path=str(context.title_font_path),
                base_font_size=int(spec.title_font_size * 1.18),
                max_width_ratio=0.9,
                fill=(214, 33, 16),
                stroke_fill=(255, 255, 255),
                stroke_width=18,
                box_fill=None,
                box_padding=(30, 18),
                box_radius=34,
                shadow_offset=(0, 0),
                shadow_fill=None,
            ),
            _LineStyle(
                font_path=str(context.subtitle_font_path),
                base_font_size=max(int(spec.subtitle_font_size * 1.35), 72),
                max_width_ratio=0.88,
                fill=(30, 80, 255),
                stroke_fill=(255, 255, 255),
                stroke_width=12,
                box_fill=None,
                box_padding=(16, 12),
                box_radius=18,
                shadow_offset=(0, 0),
                shadow_fill=None,
            ),
        ]
        if line_count <= len(base_styles):
            return base_styles[:line_count]
        extra_style = base_styles[-1]
        return base_styles + [extra_style] * (line_count - len(base_styles))

    def _resolve_anchor_positions(self, line_count: int, canvas_height: int) -> List[float]:
        ratios = self._extend_ratios(self.ANCHOR_RATIOS, line_count)
        return [canvas_height * ratio for ratio in ratios]

    def _resolve_target_boxes(self, line_count: int, canvas_width: int, canvas_height: int) -> List[Tuple[float, float]]:
        width_ratios = self._extend_ratios(self.BOX_WIDTH_RATIOS, line_count)
        height_ratios = self._extend_ratios(self.BOX_HEIGHT_RATIOS, line_count)
        boxes: List[Tuple[float, float]] = []
        for w_ratio, h_ratio in zip(width_ratios, height_ratios):
            boxes.append((canvas_width * w_ratio, canvas_height * h_ratio))
        return boxes

    def _extend_ratios(self, base: Tuple[float, ...], count: int) -> List[float]:
        if count <= len(base):
            return list(base[:count])
        extended = list(base)
        while len(extended) < count:
            extended.append(base[-1])
        return extended

    def _fit_font_to_box(
        self,
        *,
        text: str,
        font_path: str,
        base_size: int,
        max_width: float,
        max_height: float,
    ) -> ImageFont.FreeTypeFont:
        content = text or " "
        upper = max(int(base_size * 1.8), base_size + 16)
        lower = 12
        best_font: ImageFont.FreeTypeFont | None = None

        while lower <= upper:
            mid = (lower + upper) // 2
            font = ImageFont.truetype(font_path, size=max(mid, 12))
            width, height = measure_text(font, content)
            if width <= max_width and height <= max_height:
                best_font = font
                lower = mid + 1
            else:
                upper = mid - 1

        if best_font is None:
            return ImageFont.truetype(font_path, size=12)
        return best_font
