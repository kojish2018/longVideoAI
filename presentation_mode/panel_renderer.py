from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter

from .models import PanelContent
from .utils import hex_to_rgb, load_font


@dataclass(frozen=True)
class PanelLayout:
    canvas_size: Tuple[int, int]
    title_box: Tuple[int, int, int, int]
    body_box: Tuple[int, int, int, int]
    conclusion_box: Tuple[int, int, int, int]
    bullet_prefix: str = "・"


@dataclass(frozen=True)
class PanelTheme:
    board_fill: Tuple[int, int, int, int] = (255, 255, 255, 255)
    border_color: Tuple[int, int, int, int] = (170, 178, 255, 255)
    top_bar_color: Tuple[int, int, int, int] = (233, 226, 255, 255)
    shadow_color: Tuple[int, int, int, int] = (170, 160, 255, 90)
    title_highlight_color: Tuple[int, int, int, int] = (255, 79, 159, 255)
    bullet_fill_color: Tuple[int, int, int, int] = (66, 143, 255, 255)
    bullet_icon_color: Tuple[int, int, int, int] = (255, 255, 255, 255)
    conclusion_fill_color: Tuple[int, int, int, int] = (255, 227, 236, 255)
    conclusion_border_color: Tuple[int, int, int, int] | None = None

    padding_ratio: float = 0.035
    border_width_ratio: float = 0.018
    border_radius_ratio: float = 0.06
    top_bar_height_ratio: float = 0.045
    shadow_offset_ratio: Tuple[float, float] = (0.012, 0.02)
    shadow_blur_radius_ratio: float = 0.045

    title_highlight_pad_x_ratio: float = 0.18
    title_highlight_pad_y_ratio: float = 0.45

    bullet_radius_factor: float = 0.52
    bullet_text_gap_factor: float = 0.52
    bullet_group_gap_factor: float = 0.7
    check_thickness_ratio: float = 0.12

    conclusion_padding_x_factor: float = 0.9
    conclusion_padding_y_factor: float = 0.6
    conclusion_radius_factor: float = 0.55

    @staticmethod
    def _parse_color(value, default: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        if not value:
            return default
        if isinstance(value, str):
            text = value.strip().lstrip("#")
            if len(text) not in (6, 8):
                return default
            r = int(text[0:2], 16)
            g = int(text[2:4], 16)
            b = int(text[4:6], 16)
            if len(text) == 8:
                a = int(text[6:8], 16)
            else:
                a = 255
            return (r, g, b, a)
        if isinstance(value, Sequence):
            seq = list(value)
            if len(seq) == 3:
                return (int(seq[0]), int(seq[1]), int(seq[2]), 255)
            if len(seq) == 4:
                return (int(seq[0]), int(seq[1]), int(seq[2]), int(seq[3]))
        return default

    @classmethod
    def from_dict(cls, data: object) -> "PanelTheme":
        if not isinstance(data, dict):
            return cls()
        defaults = cls()
        kwargs = {}
        for field in ("board_fill", "border_color", "top_bar_color", "shadow_color",
                      "title_highlight_color", "bullet_fill_color", "bullet_icon_color",
                      "conclusion_fill_color", "conclusion_border_color"):
            if field in data:
                kwargs[field] = cls._parse_color(data[field], getattr(defaults, field))
        for field in (
            "padding_ratio",
            "border_width_ratio",
            "border_radius_ratio",
            "top_bar_height_ratio",
            "shadow_offset_ratio",
            "shadow_blur_radius_ratio",
            "title_highlight_pad_x_ratio",
            "title_highlight_pad_y_ratio",
            "bullet_radius_factor",
            "bullet_text_gap_factor",
            "bullet_group_gap_factor",
            "check_thickness_ratio",
            "conclusion_padding_x_factor",
            "conclusion_padding_y_factor",
            "conclusion_radius_factor",
        ):
            if field in data:
                kwargs[field] = data[field]
        return cls(**kwargs)


DEFAULT_THEME = PanelTheme()


DEFAULT_LAYOUT = PanelLayout(
    canvas_size=(1140, 1080),
    title_box=(60, 70, 1080, 250),
    body_box=(60, 270, 1080, 880),
    conclusion_box=(60, 900, 1080, 1040),
)


def scale_layout(base: PanelLayout, target_width: int, target_height: int) -> PanelLayout:
    """Scale a base layout to the desired panel dimensions."""
    base_width, base_height = base.canvas_size
    if base_width <= 0 or base_height <= 0:
        raise ValueError("Base layout canvas size must be positive.")

    scale_x = target_width / base_width
    scale_y = target_height / base_height

    def _scale_box(box: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        x0, y0, x1, y1 = box
        return (
            int(round(x0 * scale_x)),
            int(round(y0 * scale_y)),
            int(round(x1 * scale_x)),
            int(round(y1 * scale_y)),
        )

    return PanelLayout(
        canvas_size=(int(target_width), int(target_height)),
        title_box=_scale_box(base.title_box),
        body_box=_scale_box(base.body_box),
        conclusion_box=_scale_box(base.conclusion_box),
        bullet_prefix=base.bullet_prefix,
    )


class PanelRenderer:
    """Render text overlays for the fixed left panel."""

    def __init__(
        self,
        *,
        template_path: Path | None,
        layout: PanelLayout = DEFAULT_LAYOUT,
        font_path: str | None,
        title_size: int = 72,
        body_size: int = 52,
        conclusion_size: int = 58,
        text_color: Tuple[int, int, int] = (30, 30, 30),
        accent_color: Tuple[int, int, int] = (255, 80, 160),
        theme: PanelTheme = DEFAULT_THEME,
    ) -> None:
        self.template_path = template_path
        self.layout = layout
        self.font_path = font_path
        self.title_size = title_size
        self.body_size = body_size
        self.conclusion_size = conclusion_size
        self.text_color = text_color
        self.accent_color = accent_color
        self.theme = theme

    def render(self, panel: PanelContent, output_path: Path) -> Path:
        base = self._load_base_canvas()
        draw = ImageDraw.Draw(base)

        title_font_size = panel.font_overrides.title_size or self.title_size
        body_font_size = panel.font_overrides.body_size or self.body_size
        conclusion_font_size = panel.font_overrides.conclusion_size or self.conclusion_size

        title_font = load_font(self.font_path, title_font_size)
        body_font = load_font(self.font_path, body_font_size)
        conclusion_font = load_font(self.font_path, conclusion_font_size)

        self._draw_title(draw, panel.title, title_font, title_font_size)
        body_end_y = self._draw_bullet_body(draw, panel.body, body_font, body_font_size)
        if panel.conclusion:
            self._draw_conclusion(
                draw,
                panel.conclusion,
                conclusion_font,
                conclusion_font_size,
                start_y=body_end_y,
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        base.save(output_path)
        return output_path

    # ------------------------------------------------------------------

    def _load_base_canvas(self) -> Image.Image:
        if self.template_path and self.template_path.exists():
            try:
                template = Image.open(self.template_path).convert("RGBA")
                template = template.copy()
                target_size = self.layout.canvas_size
                if target_size != template.size:
                    resampling = getattr(Image, "Resampling", Image)
                    template = template.resize(target_size, resampling.LANCZOS)
                return template
            except Exception:
                pass
        return self._create_board_canvas()

    def _create_board_canvas(self) -> Image.Image:
        width, height = self.layout.canvas_size
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        padding = int(width * self.theme.padding_ratio)
        border_width = max(2, int(width * self.theme.border_width_ratio))
        border_radius = max(border_width + 6, int(width * self.theme.border_radius_ratio))
        top_bar_height = max(0, int(height * self.theme.top_bar_height_ratio))

        # Drop shadow
        shadow_offset_x = int(width * self.theme.shadow_offset_ratio[0])
        shadow_offset_y = int(height * self.theme.shadow_offset_ratio[1])
        shadow_radius = max(0, int(width * self.theme.shadow_blur_radius_ratio))

        board_rect = (
            padding,
            padding,
            width - padding,
            height - padding,
        )

        shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_rect = (
            board_rect[0] + shadow_offset_x,
            board_rect[1] + shadow_offset_y,
            board_rect[2] + shadow_offset_x,
            board_rect[3] + shadow_offset_y,
        )
        shadow_draw.rounded_rectangle(shadow_rect, radius=border_radius, fill=self.theme.shadow_color)
        if shadow_radius > 0:
            shadow = shadow.filter(ImageFilter.GaussianBlur(shadow_radius))
        canvas = Image.alpha_composite(canvas, shadow)

        board = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        board_draw = ImageDraw.Draw(board)
        # Offset by half the stroke so the border stays inside the canvas
        half_width = border_width / 2
        rounded_rect = (
            board_rect[0] + half_width,
            board_rect[1] + half_width,
            board_rect[2] - half_width,
            board_rect[3] - half_width,
        )
        board_draw.rounded_rectangle(
            rounded_rect,
            radius=border_radius,
            fill=self.theme.board_fill,
            outline=self.theme.border_color,
            width=border_width,
        )
        if top_bar_height > 0:
            top_bar_rect = (
                rounded_rect[0],
                rounded_rect[1],
                rounded_rect[2],
                min(rounded_rect[1] + top_bar_height, rounded_rect[3]),
            )
            board_draw.rectangle(top_bar_rect, fill=self.theme.top_bar_color)

        canvas = Image.alpha_composite(canvas, board)
        return canvas

    # ------------------------------------------------------------------

    def _draw_title(self, draw: ImageDraw.ImageDraw, title: str, font, font_size: int) -> None:
        if not title.strip():
            return
        box = self.layout.title_box
        max_width = box[2] - box[0]
        lines = self._wrap_text(title, font, max_width)
        if not lines:
            return

        line_spacing = int(font_size * 0.15)
        metrics, total_height, max_line_width = self._measure_wrapped_lines(lines, font, line_spacing)

        cursor_y = box[1]
        for line, line_height in zip(lines, metrics):
            draw.text((box[0], cursor_y), line, font=font, fill=self.accent_color, anchor="lt")
            cursor_y += line_height + line_spacing

    def _draw_bullet_body(self, draw: ImageDraw.ImageDraw, body: Sequence[str], font, font_size: int) -> int:
        if not body:
            return self.layout.body_box[1]
        box = self.layout.body_box
        x0, y0, x1, y1 = box
        cursor_y = y0
        line_spacing = int(font_size * 0.25)
        bullet_radius = max(6, int(font_size * self.theme.bullet_radius_factor))
        bullet_gap = int(font_size * self.theme.bullet_text_gap_factor)
        group_gap = int(font_size * self.theme.bullet_group_gap_factor)
        check_thickness = max(2, int(bullet_radius * 2 * self.theme.check_thickness_ratio))

        usable_width = max(10, x1 - x0 - bullet_radius * 2 - bullet_gap)
        total_items = sum(1 for line in body if line and line.strip())
        rendered = 0
        for raw_line in body:
            line = raw_line.strip()
            if not line:
                continue
            wrapped = self._wrap_text(line, font, usable_width)
            if not wrapped:
                continue
            first_line = wrapped[0]
            bbox = font.getbbox(first_line, anchor="lt")
            first_line_height = max(1, bbox[3] - bbox[1])

            center_x = x0 + bullet_radius
            center_y = cursor_y + first_line_height // 2
            self._draw_bullet_icon(draw, (center_x, center_y), bullet_radius, check_thickness)

            text_x = center_x + bullet_radius + bullet_gap
            for index, segment in enumerate(wrapped):
                if cursor_y > y1:
                    return cursor_y
                draw.text((text_x, cursor_y), segment, font=font, fill=self.text_color, anchor="lt")
                bbox = font.getbbox(segment, anchor="lt")
                line_height = max(1, bbox[3] - bbox[1])
                cursor_y += line_height
                if index < len(wrapped) - 1:
                    cursor_y += line_spacing
            rendered += 1
            if rendered < total_items:
                cursor_y += group_gap

        return cursor_y

    def _draw_conclusion(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font,
        font_size: int,
        *,
        start_y: int | None = None,
    ) -> None:
        box = self.layout.conclusion_box
        if not text.strip():
            return
        max_width = box[2] - box[0]
        lines = self._wrap_text(text, font, max_width)
        if not lines:
            return

        pad_x = int(font_size * self.theme.conclusion_padding_x_factor)
        pad_y = int(font_size * self.theme.conclusion_padding_y_factor)
        line_spacing = int(font_size * 0.18)
        metrics, total_height, max_line_width = self._measure_wrapped_lines(lines, font, line_spacing)

        gap_after_body = max(10, int(font_size * 0.3))
        candidate_top = box[1]
        if start_y is not None and start_y > 0:
            candidate_top = start_y + gap_after_body

        available_height = total_height + pad_y * 2
        max_top = box[3] - available_height
        min_top = self.layout.body_box[1]
        content_top = max(min(candidate_top, max_top), min_top)

        rect = (
            box[0],
            content_top,
            min(box[0] + max_line_width + pad_x * 2, box[2]),
            min(content_top + total_height + pad_y * 2, box[3]),
        )
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            return
        radius = max(8, int(font_size * self.theme.conclusion_radius_factor))

        draw.rounded_rectangle(
            rect,
            radius=radius,
            fill=self.theme.conclusion_fill_color,
            outline=self.theme.conclusion_border_color,
            width=2 if self.theme.conclusion_border_color else 0,
        )

        inner_height = rect[3] - rect[1]
        inner_width = rect[2] - rect[0]
        available_vertical = max(0.0, inner_height - total_height)
        vertical_padding = max(float(pad_y), available_vertical / 2.0)
        vertical_padding = min(vertical_padding, available_vertical)
        cursor_y = rect[1] + vertical_padding
        for idx, (line, line_height) in enumerate(zip(lines, metrics)):
            line_width = font.getlength(line)
            if line_width <= max(1.0, inner_width - pad_x * 2):
                line_x = rect[0] + (inner_width - line_width) / 2
            else:
                line_x = rect[0] + pad_x
            draw.text((line_x, cursor_y), line, font=font, fill=self.text_color, anchor="lt")
            cursor_y += line_height
            if idx < len(lines) - 1:
                cursor_y += line_spacing

    def _draw_bullet_icon(
        self,
        draw: ImageDraw.ImageDraw,
        center: Tuple[int, int],
        radius: int,
        check_thickness: int,
    ) -> None:
        cx, cy = center
        bbox = (
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
        )
        draw.ellipse(bbox, fill=self.theme.bullet_fill_color)

        # Draw check mark
        arm1_start = (cx - radius * 0.45, cy)
        arm1_end = (cx - radius * 0.1, cy + radius * 0.45)
        arm2_end = (cx + radius * 0.55, cy - radius * 0.4)
        draw.line([arm1_start, arm1_end], fill=self.theme.bullet_icon_color, width=check_thickness)
        draw.line([arm1_end, arm2_end], fill=self.theme.bullet_icon_color, width=check_thickness)

    def _measure_wrapped_lines(
        self,
        lines: Sequence[str],
        font,
        line_spacing: int,
    ) -> Tuple[List[int], int, int]:
        metrics: List[int] = []
        max_width = 0
        total_height = 0
        for idx, line in enumerate(lines):
            bbox = font.getbbox(line, anchor="lt")
            line_height = max(0, bbox[3] - bbox[1])
            metrics.append(line_height)
            total_height += line_height
            if idx < len(lines) - 1:
                total_height += line_spacing
            width = int(round(font.getlength(line)))
            if width > max_width:
                max_width = width
        return metrics, total_height, max_width

    @staticmethod
    def _wrap_text(text: str, font, max_width: int) -> List[str]:
        # Simple wrapping that respects Japanese/English characters.
        parts: List[str] = []
        buffer = ""
        for char in text:
            candidate = buffer + char
            width = font.getlength(candidate)
            if width <= max_width:
                buffer = candidate
                continue
            if buffer:
                parts.append(buffer)
                buffer = char
            else:
                parts.append(candidate)
                buffer = ""
        if buffer:
            parts.append(buffer)
        return parts or [text]
