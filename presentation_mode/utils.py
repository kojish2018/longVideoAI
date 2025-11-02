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
