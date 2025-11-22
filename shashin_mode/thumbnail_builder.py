"""Thumbnail composer for shashin_mode."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from logging_utils import get_logger

logger = get_logger(__name__)

CANVAS_SIZE = (1280, 720)
BACKGROUND_COLOR = (245, 245, 245)
YELLOW_BAR_COLOR = (255, 230, 0)
BANNER_TEXT_COLOR = (214, 0, 0)
TITLE_TEXT_COLOR = (0, 0, 0)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
LINE_SPACING = 10

FONT_PRIMARY_CANDIDATES = [
    "fonts/NotoSansJP-Black.otf",
    "fonts/NotoSansJP-ExtraBold.ttf",
    "fonts/NotoSansJP-Bold.ttf",
]
FONT_SECONDARY_CANDIDATES = [
    "fonts/NotoSansJP-Bold.ttf",
    "fonts/NotoSansJP-Medium.otf",
    "fonts/NotoSansJP-Regular.otf",
]


def generate_thumbnail(
    *,
    title_text: str,
    banner_text: str,
    image_candidates: Sequence[Path],
    output_path: Path,
) -> Optional[Path]:
    """Compose a thumbnail using two Openverse images and provided text."""

    if not title_text:
        logger.warning("Thumbnail skipped: title text is empty")
        return None
    if not banner_text:
        logger.warning("Thumbnail skipped: banner text is empty")
        return None

    usable_images = _collect_images(image_candidates, needed=2)
    if not usable_images:
        logger.warning("Thumbnail skipped: no usable Openverse images were available")
        return None

    canvas = Image.new("RGB", CANVAS_SIZE, BACKGROUND_COLOR)

    top_area_height = 470
    margin = 20

    left_rect = (margin, margin, CANVAS_SIZE[0] // 2 + 40, top_area_height)
    right_rect = (left_rect[2] + margin, margin, CANVAS_SIZE[0] - margin, top_area_height)

    _paste_cover(usable_images[0], canvas, left_rect)
    _paste_cover(usable_images[min(1, len(usable_images) - 1)], canvas, right_rect)

    draw = ImageDraw.Draw(canvas)
    yellow_top = top_area_height + 10
    yellow_bottom = CANVAS_SIZE[1]
    draw.rectangle([(0, yellow_top), (CANVAS_SIZE[0], yellow_bottom)], fill=YELLOW_BAR_COLOR)

    title_box = (40, yellow_top + 10, CANVAS_SIZE[0] - 40, yellow_bottom - 40)
    title_font = _fit_font_to_box(
        FONT_PRIMARY_CANDIDATES,
        text=title_text,
        box=title_box,
        min_size=80,
        max_size=220,
    )
    banner_font = _load_font(FONT_SECONDARY_CANDIDATES, size=60)

    _draw_centered_text(
        draw,
        text=title_text,
        font=title_font,
        box=title_box,
        fill=TITLE_TEXT_COLOR,
        stroke_width=6,
        stroke_fill=WHITE,
    )

    _draw_slanted_banner(
        canvas,
        text=banner_text,
        font=banner_font,
        box_size=(650, 80),
        position=(80, yellow_top - 160),
        angle=-8,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG")
    logger.info("Thumbnail created: %s", output_path)
    return output_path


def _collect_images(paths: Sequence[Path], *, needed: int) -> List[Image.Image]:
    images: List[Image.Image] = []
    for path in paths:
        if len(images) >= needed:
            break
        if not path or not Path(path).exists():
            continue
        try:
            images.append(Image.open(path).convert("RGB"))
        except OSError:
            logger.warning("Failed to open thumbnail source image: %s", path)
            continue
    if images and len(images) < needed:
        images.extend(images[: needed - len(images)])
    return images


def _paste_cover(image: Image.Image, canvas: Image.Image, rect: Tuple[int, int, int, int]) -> None:
    target_width = rect[2] - rect[0]
    target_height = rect[3] - rect[1]
    scale = max(target_width / image.width, target_height / image.height)
    resized = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
    left = rect[0] + (target_width - resized.width) // 2
    top = rect[1] + (target_height - resized.height) // 2
    canvas.paste(resized, (left, top))


def _load_font(candidates: Iterable[str], *, size: int) -> ImageFont.FreeTypeFont:
    for candidate in candidates:
        path = (Path(candidate).expanduser()).resolve()
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    logger.warning("Fallback to default PIL font for thumbnail text")
    return ImageFont.load_default()


def _fit_font_to_box(
    candidates: Iterable[str],
    *,
    text: str,
    box: Tuple[int, int, int, int],
    min_size: int,
    max_size: int,
) -> ImageFont.FreeTypeFont:
    x0, y0, x1, y1 = box
    box_width = max(1, x1 - x0)
    box_height = max(1, y1 - y0)
    best_font = _load_font(candidates, size=min_size)
    low, high = min_size, max_size
    while low <= high:
        mid = (low + high) // 2
        font = _load_font(candidates, size=mid)
        lines = _auto_wrap(text, font, box_width)
        total_height, max_line_width = _measure_text_block(lines, font)
        if max_line_width <= box_width and total_height <= box_height:
            best_font = font
            low = mid + 2
        else:
            high = mid - 2
    return best_font


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    *,
    text: str,
    font: ImageFont.FreeTypeFont,
    box: Tuple[int, int, int, int],
    fill,
    stroke_width: int = 0,
    stroke_fill=None,
) -> None:
    x0, y0, x1, y1 = box
    max_width = x1 - x0
    wrapped_lines = _auto_wrap(text, font, max_width)
    total_height = sum(font.getbbox(line)[3] - font.getbbox(line)[1] for line in wrapped_lines)
    total_height += (len(wrapped_lines) - 1) * LINE_SPACING
    current_y = y0 + max(0, (y1 - y0 - total_height) // 2)
    for line in wrapped_lines:
        bbox = font.getbbox(line)
        line_width = bbox[2] - bbox[0]
        x = x0 + max(0, (max_width - line_width) // 2)
        draw.text(
            (x, current_y),
            line,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        current_y += (bbox[3] - bbox[1]) + LINE_SPACING


def _auto_wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    if font.getbbox(text)[2] - font.getbbox(text)[0] <= max_width:
        return [text]
    words = list(text)
    lines: List[str] = []
    current = ""
    for char in words:
        tentative = current + char
        width = font.getbbox(tentative)[2] - font.getbbox(tentative)[0]
        if width > max_width and current:
            lines.append(current)
            current = char
        else:
            current = tentative
    if current:
        lines.append(current)
    return lines


def _measure_text_block(lines: Sequence[str], font: ImageFont.FreeTypeFont) -> Tuple[int, int]:
    total_height = 0
    max_line_width = 0
    for line in lines:
        bbox = font.getbbox(line)
        line_width = bbox[2] - bbox[0]
        line_height = bbox[3] - bbox[1]
        total_height += line_height
        max_line_width = max(max_line_width, line_width)
    if lines:
        total_height += LINE_SPACING * (len(lines) - 1)
    return total_height, max_line_width


def _draw_slanted_banner(
    canvas: Image.Image,
    *,
    text: str,
    font: ImageFont.FreeTypeFont,
    box_size: Tuple[int, int],
    position: Tuple[int, int],
    angle: float,
) -> None:
    banner = Image.new("RGBA", box_size, (0, 0, 0, 0))
    banner_draw = ImageDraw.Draw(banner)
    banner_draw.rectangle([(0, 0), (box_size[0], box_size[1])], fill=WHITE, outline=BLACK, width=6)

    text_bbox = font.getbbox(text)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    text_x = max(20, (box_size[0] - text_width) // 2)
    text_y = max(10, (box_size[1] - text_height) // 2)
    banner_draw.text((text_x, text_y), text, font=font, fill=BANNER_TEXT_COLOR)

    rotated = banner.rotate(angle, expand=True, resample=Image.BICUBIC)
    canvas.paste(rotated, position, rotated)
