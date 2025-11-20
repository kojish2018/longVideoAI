"""Visual style helpers for yukkuri_mode rendering."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_FONT = Path("fonts") / "NotoSansJP-Bold.ttf"
DEFAULT_FONT_BOLD = Path("fonts") / "NotoSansJP-ExtraBold.ttf"


@dataclass(frozen=True)
class KenBurnsProfile:
    zoom: float
    travel: float
    margin: float


@dataclass(frozen=True)
class TextStyle:
    font_path: Path
    font_path_bold: Path
    size: int
    wrap_chars: int
    color: Tuple[int, int, int]
    stroke_color: Tuple[int, int, int]
    stroke_width: int
    band_color: Tuple[int, int, int, int]
    band_border_color: Tuple[int, int, int, int]
    band_padding: Tuple[int, int]
    band_margin: int
    drop_shadow: int


@dataclass(frozen=True)
class NameplateStyle:
    text_size: int
    text_color: Tuple[int, int, int]
    stroke_color: Tuple[int, int, int]
    band_color: Tuple[int, int, int, int]
    band_border_color: Tuple[int, int, int, int]


@dataclass(frozen=True)
class CharacterSpec:
    key: str
    display_name: str
    anchor: str
    sprite_path: Optional[Path]
    color: Tuple[int, int, int]
    stroke_color: Tuple[int, int, int]
    scale: float
    y_offset: int


@dataclass(frozen=True)
class LayoutStyle:
    width: int
    height: int
    fps: int
    backgrounds: List[Path]
    fallback_bg_color: Tuple[int, int, int]
    ken_burns: KenBurnsProfile


@dataclass(frozen=True)
class YukkuriStyle:
    layout: LayoutStyle
    text: TextStyle
    nameplate: NameplateStyle
    characters: Dict[str, CharacterSpec]
    aliases: Dict[str, str]


def _hex_to_rgb(value: str) -> Tuple[int, int, int]:
    stripped = value.lstrip("#")
    if len(stripped) != 6:
        raise ValueError(f"Expected 6-digit hex color, got: {value}")
    return tuple(int(stripped[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _hex_to_rgba(value: str) -> Tuple[int, int, int, int]:
    stripped = value.lstrip("#")
    if len(stripped) == 6:
        rgb = _hex_to_rgb(value)
        return (*rgb, 255)
    if len(stripped) == 8:
        return tuple(int(stripped[i : i + 2], 16) for i in (0, 2, 4, 6))  # type: ignore[return-value]
    raise ValueError(f"Expected 6 or 8 digit hex color, got: {value}")


def _resolve_path(base: Path, maybe_path: Optional[str]) -> Optional[Path]:
    if not maybe_path:
        return None
    candidate = Path(maybe_path)
    if candidate.is_absolute():
        return candidate
    # Prefer paths that already exist relative to CWD (e.g., "fonts/...").
    if candidate.exists():
        return candidate.resolve()
    # Fall back to config-local resolution (e.g., config directory).
    return (base / candidate).resolve()


def load_style_config(raw: Dict[str, Any], base_dir: Path) -> YukkuriStyle:
    style_cfg = raw.get("style", {}) if isinstance(raw, dict) else {}

    text = TextStyle(
        font_path=_resolve_path(base_dir, style_cfg.get("font_path")) or DEFAULT_FONT,
        font_path_bold=_resolve_path(base_dir, style_cfg.get("font_path_bold")) or DEFAULT_FONT_BOLD,
        size=int(style_cfg.get("base_text_size", 62) or 62),
        wrap_chars=int(style_cfg.get("wrap_chars", 20) or 20),
        color=_hex_to_rgb(style_cfg.get("text_color", "#FFE04A")),
        stroke_color=_hex_to_rgb(style_cfg.get("stroke_color", "#0A0A0A")),
        stroke_width=int(style_cfg.get("stroke_width", 4) or 4),
        band_color=_hex_to_rgba(style_cfg.get("band_color", "#0B245DCC")),
        band_border_color=_hex_to_rgba(style_cfg.get("band_border_color", "#C6A249")),
        band_padding=(
            int(style_cfg.get("band_horizontal_padding", 32) or 32),
            int(style_cfg.get("band_vertical_padding", 26) or 26),
        ),
        band_margin=int(style_cfg.get("band_margin", 30) or 30),
        drop_shadow=int(style_cfg.get("drop_shadow", 6) or 6),
    )

    np_cfg = style_cfg.get("nameplate", {}) if isinstance(style_cfg, dict) else {}
    nameplate = NameplateStyle(
        text_size=int(np_cfg.get("text_size", 42) or 42),
        text_color=_hex_to_rgb(np_cfg.get("text_color", "#FFFFFF")),
        stroke_color=_hex_to_rgb(np_cfg.get("stroke_color", "#0A0A0A")),
        band_color=_hex_to_rgba(np_cfg.get("band_color", "#2E2E2ECC")),
        band_border_color=_hex_to_rgba(np_cfg.get("band_border_color", "#C6A249")),
    )

    backgrounds_cfg = raw.get("backgrounds", {}) if isinstance(raw, dict) else {}
    bg_dirs = backgrounds_cfg.get("search_dirs", []) if isinstance(backgrounds_cfg, dict) else []
    backgrounds: List[Path] = []
    for entry in bg_dirs:
        resolved = _resolve_path(base_dir, str(entry))
        if resolved:
            backgrounds.append(resolved)

    kb_cfg = backgrounds_cfg.get("ken_burns", {}) if isinstance(backgrounds_cfg, dict) else {}
    ken_burns = KenBurnsProfile(
        zoom=float(kb_cfg.get("zoom", 0.06) or 0.06),
        travel=float(kb_cfg.get("travel", 0.08) or 0.08),
        margin=float(kb_cfg.get("margin", 0.08) or 0.08),
    )

    layout = LayoutStyle(
        width=int(raw.get("video", {}).get("width", 1920) if isinstance(raw.get("video", {}), dict) else 1920),
        height=int(raw.get("video", {}).get("height", 1080) if isinstance(raw.get("video", {}), dict) else 1080),
        fps=int(raw.get("video", {}).get("fps", 30) if isinstance(raw.get("video", {}), dict) else 30),
        backgrounds=backgrounds,
        fallback_bg_color=_hex_to_rgb(backgrounds_cfg.get("fallback_color", "#0D0D15")),
        ken_burns=ken_burns,
    )

    characters_cfg = raw.get("characters", {}) if isinstance(raw, dict) else {}
    sprite_defaults = {
        "scale": float(characters_cfg.get("default_scale", 0.54) or 0.54),
        "y_offset": int(characters_cfg.get("default_y_offset", -80) or -80),
    }
    sprites_cfg = characters_cfg.get("sprites", {}) if isinstance(characters_cfg, dict) else {}

    character_map: Dict[str, CharacterSpec] = {}
    for key, cfg in sprites_cfg.items():
        if not isinstance(cfg, dict):
            continue
        sprite_path = _resolve_path(base_dir, cfg.get("sprite_path"))
        character_map[key] = CharacterSpec(
            key=key,
            display_name=str(cfg.get("display_name", key)),
            anchor=str(cfg.get("anchor", "left")),
            sprite_path=sprite_path,
            color=_hex_to_rgb(cfg.get("color", "#FFFFFF")),
            stroke_color=_hex_to_rgb(cfg.get("stroke_color", "#0A0A0A")),
            scale=float(cfg.get("scale", sprite_defaults["scale"]) or sprite_defaults["scale"]),
            y_offset=int(cfg.get("y_offset", sprite_defaults["y_offset"]) or sprite_defaults["y_offset"]),
        )

    raw_aliases = characters_cfg.get("aliases", {}) if isinstance(characters_cfg, dict) else {}
    aliases: Dict[str, str] = {}
    for alias, target in raw_aliases.items():
        if target:
            aliases[str(alias)] = str(target)

    return YukkuriStyle(
        layout=layout,
        text=text,
        nameplate=nameplate,
        characters=character_map,
        aliases=aliases,
    )
