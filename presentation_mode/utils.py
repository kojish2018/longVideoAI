from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Tuple

from PIL import ImageFont


def hex_to_rgb(value: str | None, fallback: Tuple[int, int, int] = (255, 255, 255)) -> Tuple[int, int, int]:
    if not value:
        return fallback
    text = value.strip().lstrip("#")
    if len(text) not in (3, 6, 8):
        return fallback
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) == 8:
        text = text[:6]
    try:
        r = int(text[0:2], 16)
        g = int(text[2:4], 16)
        b = int(text[4:6], 16)
        return (r, g, b)
    except ValueError:
        return fallback


def hex_to_rgba(value: str | None, fallback: Tuple[int, int, int, int] = (255, 255, 255, 255)) -> Tuple[int, int, int, int]:
    if not value:
        return fallback
    text = value.strip().lstrip("#")
    if len(text) == 6:
        text += "FF"
    if len(text) != 8:
        return fallback
    try:
        r = int(text[0:2], 16)
        g = int(text[2:4], 16)
        b = int(text[4:6], 16)
        a = int(text[6:8], 16)
        return (r, g, b, a)
    except ValueError:
        return fallback


@lru_cache(maxsize=32)
def load_font(path: str | None, size: int) -> ImageFont.ImageFont:
    if path:
        try:
            font_path = Path(path).expanduser()
            if font_path.exists():
                return ImageFont.truetype(str(font_path), size=size)
        except Exception:
            pass
    try:
        return ImageFont.truetype("Arial.ttf", size=size)
    except Exception:
        return ImageFont.load_default()


def stable_hash(parts: Iterable[str], length: int = 10) -> str:
    digest = hashlib.sha1("||".join(parts).encode("utf-8")).hexdigest()
    return digest[:length]


def build_vertical_bob_expression(
    base_y: float,
    *,
    amplitude: float,
    move_duration: float,
    rest_duration: float,
) -> str | None:
    amplitude = float(amplitude)
    move_duration = float(move_duration)
    rest_duration = max(0.0, float(rest_duration))

    if amplitude == 0.0 or move_duration <= 0.0:
        return None

    cycle_duration = move_duration + rest_duration
    if cycle_duration <= 0.0:
        return None

    mod_expr = f"mod(t,{cycle_duration:.6f})"

    if rest_duration > 0.0:
        gate_expr = f"lt({mod_expr},{move_duration:.6f})"
        motion_expr = f"{gate_expr}*({amplitude:.6f}*sin(2*PI*{mod_expr}/{move_duration:.6f}))"
    else:
        motion_expr = f"{amplitude:.6f}*sin(2*PI*{mod_expr}/{move_duration:.6f})"

    return f"{base_y:.3f} + ({motion_expr})"
