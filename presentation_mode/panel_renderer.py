from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple

from PIL import Image, ImageDraw

from .models import PanelContent
from .utils import hex_to_rgb, load_font


@dataclass(frozen=True)
class PanelLayout:
    canvas_size: Tuple[int, int]
    title_box: Tuple[int, int, int, int]
    body_box: Tuple[int, int, int, int]
    conclusion_box: Tuple[int, int, int, int]
    bullet_prefix: str = "・"


DEFAULT_LAYOUT = PanelLayout(
    canvas_size=(1140, 1080),
    title_box=(60, 70, 1080, 250),
    body_box=(60, 270, 1080, 880),
    conclusion_box=(60, 900, 1080, 1040),
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
    ) -> None:
        self.template_path = template_path
        self.layout = layout
        self.font_path = font_path
        self.title_size = title_size
        self.body_size = body_size
        self.conclusion_size = conclusion_size
        self.text_color = text_color
        self.accent_color = accent_color

    def render(self, panel: PanelContent, output_path: Path) -> Path:
        base = self._load_base_canvas()
        draw = ImageDraw.Draw(base)

        title_font_size = panel.font_overrides.title_size or self.title_size
        body_font_size = panel.font_overrides.body_size or self.body_size
        conclusion_font_size = panel.font_overrides.conclusion_size or self.conclusion_size

        title_font = load_font(self.font_path, title_font_size)
        body_font = load_font(self.font_path, body_font_size)
        conclusion_font = load_font(self.font_path, conclusion_font_size)

        self._draw_wrapped_text(
            draw,
            panel.title,
            self.layout.title_box,
            title_font,
            fill=self.accent_color,
            line_spacing=int(title_font_size * 0.15),
        )

        body_lines: List[str] = []
        for line in panel.body:
            normalized = line.strip()
            if not normalized:
                continue
            body_lines.append(f"{self.layout.bullet_prefix}{normalized}")

        self._draw_wrapped_lines(
            draw,
            body_lines,
            self.layout.body_box,
            body_font,
            fill=self.text_color,
            line_spacing=int(body_font_size * 0.3),
        )

        if panel.conclusion:
            self._draw_wrapped_text(
                draw,
                panel.conclusion,
                self.layout.conclusion_box,
                conclusion_font,
                fill=self.text_color,
                line_spacing=int(conclusion_font_size * 0.2),
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        base.save(output_path)
        return output_path

    # ------------------------------------------------------------------

    def _load_base_canvas(self) -> Image.Image:
        if self.template_path and self.template_path.exists():
            try:
                base = Image.open(self.template_path).convert("RGBA")
                base = base.copy()
                return base
            except Exception:
                pass
        width, height = self.layout.canvas_size
        base = Image.new("RGBA", (width, height), (245, 244, 255, 255))
        border = ImageDraw.Draw(base)
        border.rounded_rectangle(
            (0, 0, width - 1, height - 1),
            radius=26,
            outline=(200, 180, 250, 255),
            width=8,
        )
        bar_height = 28
        border.rectangle((0, 0, width, bar_height), fill=(255, 120, 200, 230))
        return base

    def _draw_wrapped_lines(
        self,
        draw: ImageDraw.ImageDraw,
        lines: Iterable[str],
        box: Tuple[int, int, int, int],
        font,
        *,
        fill: Tuple[int, int, int],
        line_spacing: int,
    ) -> None:
        x0, y0, x1, y1 = box
        cursor_y = y0
        max_width = x1 - x0
        for line in lines:
            wrapped = self._wrap_text(line, font, max_width)
            for part in wrapped:
                if cursor_y > y1:
                    return
                draw.text((x0, cursor_y), part, font=font, fill=fill)
                _, top, _, bottom = font.getbbox(part)
                line_height = bottom - top
                cursor_y += line_height + line_spacing

    def _draw_wrapped_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        box: Tuple[int, int, int, int],
        font,
        *,
        fill: Tuple[int, int, int],
        line_spacing: int,
    ) -> None:
        if not text.strip():
            return
        lines = self._wrap_text(text, font, box[2] - box[0])
        self._draw_wrapped_lines(draw, lines, box, font, fill=fill, line_spacing=line_spacing)

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
