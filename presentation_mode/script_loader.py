from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from logging_utils import get_logger

from .models import (
    BackgroundDefaults,
    CharacterAnimationSettings,
    CharacterPlacement,
    PanelContent,
    PanelFontOverrides,
    PresentationScene,
    PresentationScript,
)

logger = get_logger(__name__)


def _ensure_str_list(name: str, values: Iterable[Any]) -> Tuple[str, ...]:
    normalized: List[str] = []
    for idx, value in enumerate(values, start=1):
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f"{name} must contain strings only (item {idx})")
        stripped = value.strip()
        if stripped:
            normalized.append(stripped)
    return tuple(normalized)


def _parse_font_overrides(raw: Dict[str, Any] | None) -> PanelFontOverrides:
    if not raw:
        return PanelFontOverrides()

    def _extract_int(key: str) -> int | None:
        value = raw.get(key)
        if value is None:
            return None
        try:
            ivalue = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"font_overrides.{key} must be an integer")
        if ivalue <= 0:
            raise ValueError(f"font_overrides.{key} must be positive")
        return ivalue

    return PanelFontOverrides(
        title_size=_extract_int("title_size"),
        body_size=_extract_int("body_size"),
        conclusion_size=_extract_int("conclusion_size"),
    )


def _parse_panel(raw: Dict[str, Any]) -> PanelContent:
    if "title" not in raw or "body" not in raw:
        raise ValueError("panel must include 'title' and 'body'")

    title = str(raw["title"]).strip()
    if not title:
        raise ValueError("panel.title cannot be empty")

    body_raw = raw["body"]
    if not isinstance(body_raw, (list, tuple)):
        raise ValueError("panel.body must be an array of strings")
    body = _ensure_str_list("panel.body", body_raw)
    if not body:
        raise ValueError("panel.body must contain at least one non-empty string")

    conclusion = raw.get("conclusion")
    conclusion_text = str(conclusion).strip() if isinstance(conclusion, str) else None

    font_overrides = _parse_font_overrides(raw.get("font_overrides"))
    return PanelContent(
        title=title,
        body=body,
        conclusion=conclusion_text if conclusion_text else None,
        font_overrides=font_overrides,
    )


def _parse_scene(raw: Dict[str, Any], *, index: int) -> PresentationScene:
    scene_id = str(raw.get("id") or f"S{index:03d}")
    narration = raw.get("narration")
    if not isinstance(narration, str) or not narration.strip():
        raise ValueError(f"Scene {scene_id} must include non-empty 'narration'")
    panel_raw = raw.get("panel")
    if not isinstance(panel_raw, dict):
        raise ValueError(f"Scene {scene_id} must include 'panel' object")
    panel = _parse_panel(panel_raw)

    background_prompt = raw.get("background_prompt")
    if background_prompt is not None and not isinstance(background_prompt, str):
        raise ValueError(f"Scene {scene_id} background_prompt must be a string if set")
    subtitle_override = raw.get("subtitle_override")
    if subtitle_override is not None and not isinstance(subtitle_override, str):
        raise ValueError(f"Scene {scene_id} subtitle_override must be a string if set")

    subtitle_lines_raw = raw.get("subtitle_lines")
    if subtitle_lines_raw is not None:
        if not isinstance(subtitle_lines_raw, (list, tuple)):
            raise ValueError(f"Scene {scene_id} subtitle_lines must be an array of strings")
        subtitle_lines = _ensure_str_list("subtitle_lines", subtitle_lines_raw)
        if not subtitle_lines:
            subtitle_lines = None
    else:
        subtitle_lines = None

    return PresentationScene(
        scene_id=scene_id,
        narration=narration.strip(),
        panel=panel,
        background_prompt=background_prompt.strip() if isinstance(background_prompt, str) and background_prompt.strip() else None,
        subtitle_override=subtitle_override.strip() if isinstance(subtitle_override, str) and subtitle_override.strip() else None,
        subtitle_lines=subtitle_lines,
    )


def _parse_character(raw: Dict[str, Any] | None, *, base_dir: Path) -> CharacterPlacement | None:
    if not raw:
        return None
    image_path_raw = raw.get("image_path")
    if not isinstance(image_path_raw, str) or not image_path_raw.strip():
        raise ValueError("character.image_path must be a non-empty string")
    image_path = Path(image_path_raw).expanduser()
    if not image_path.is_absolute():
        image_path = (base_dir / image_path).resolve()

    position_raw = raw.get("position", {})
    if position_raw is None:
        position_raw = {}
    if not isinstance(position_raw, dict):
        raise ValueError("character.position must be an object with x/y")

    def _extract_float(name: str, default: float = 0.0) -> float:
        value = position_raw.get(name, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(f"character.position.{name} must be a number")

    x = _extract_float("x", 0.0)
    y = _extract_float("y", 0.0)

    scale_raw = raw.get("scale", 1.0)
    try:
        scale = float(scale_raw)
    except (TypeError, ValueError):
        raise ValueError("character.scale must be numeric")
    if scale <= 0:
        raise ValueError("character.scale must be greater than zero")

    animation = _parse_character_animation(raw.get("animation"))

    return CharacterPlacement(
        image_path=image_path,
        position=(x, y),
        scale=scale,
        animation=animation,
    )


def _parse_character_animation(raw: Dict[str, Any] | None) -> CharacterAnimationSettings:
    if not raw:
        return CharacterAnimationSettings()
    if not isinstance(raw, dict):
        raise ValueError("character.animation must be an object if provided")

    def _extract_bool(key: str, default: bool) -> bool:
        value = raw.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        raise ValueError(f"character.animation.{key} must be a boolean")

    def _extract_float(key: str, default: float, *, minimum: float | None = None) -> float:
        value = raw.get(key, default)
        try:
            fvalue = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"character.animation.{key} must be numeric")
        if minimum is not None and fvalue < minimum:
            raise ValueError(f"character.animation.{key} must be >= {minimum}")
        return fvalue

    enabled = _extract_bool("enabled", True)
    amplitude = _extract_float("amplitude", 24.0, minimum=0.0)
    move_duration = _extract_float("move_duration", 2.0, minimum=0.01)
    rest_duration = _extract_float("rest_duration", 5.0, minimum=0.0)

    return CharacterAnimationSettings(
        enabled=enabled,
        amplitude=amplitude,
        move_duration=move_duration,
        rest_duration=rest_duration,
    )


def _parse_background_defaults(raw: Dict[str, Any] | None) -> BackgroundDefaults:
    if not raw:
        return BackgroundDefaults()

    prompt = raw.get("prompt")
    if prompt is not None and not isinstance(prompt, str):
        raise ValueError("background_defaults.prompt must be a string if set")

    interval_raw = raw.get("change_interval_seconds")
    interval = 120
    if interval_raw is not None:
        try:
            interval = int(interval_raw)
        except (TypeError, ValueError):
            raise ValueError("background_defaults.change_interval_seconds must be an integer")
        if interval < 30:
            logger.warning("change_interval_seconds too small (%s); clamping to 30", interval)
            interval = 30

    return BackgroundDefaults(
        prompt=prompt.strip() if isinstance(prompt, str) and prompt.strip() else None,
        change_interval_seconds=interval,
    )


def load_presentation_script(path: Path | str) -> PresentationScript:
    script_path = Path(path).expanduser().resolve()
    if not script_path.exists():
        raise FileNotFoundError(f"Presentation script not found: {script_path}")

    with script_path.open("r", encoding="utf-8") as f:
        try:
            raw = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to parse JSON ({exc})") from exc

    if not isinstance(raw, dict):
        raise ValueError("Presentation script root must be an object")

    title_raw = raw.get("title")
    if not isinstance(title_raw, str) or not title_raw.strip():
        raise ValueError("Root 'title' field is required")
    title = title_raw.strip()

    tags_raw = raw.get("tags", [])
    if tags_raw is None:
        tags_raw = []
    if not isinstance(tags_raw, (list, tuple)):
        raise ValueError("tags must be an array of strings if provided")
    tags = _ensure_str_list("tags", tags_raw)

    description_raw = raw.get("description")
    description = (
        description_raw.strip()
        if isinstance(description_raw, str) and description_raw.strip()
        else None
    )

    scenes_raw = raw.get("scenes")
    if not isinstance(scenes_raw, list) or not scenes_raw:
        raise ValueError("'scenes' must be a non-empty array")

    scenes: List[PresentationScene] = []
    for idx, scene_raw in enumerate(scenes_raw, start=1):
        if not isinstance(scene_raw, dict):
            raise ValueError(f"Scene at index {idx} must be an object")
        scene = _parse_scene(scene_raw, index=idx)
        scenes.append(scene)

    character = _parse_character(raw.get("character"), base_dir=script_path.parent)
    bg_defaults = _parse_background_defaults(raw.get("background_defaults"))

    logger.info("Loaded presentation script: %s (%d scenes)", title, len(scenes))
    return PresentationScript(
        title=title,
        scenes=tuple(scenes),
        tags=tags,
        description=description,
        character=character,
        background_defaults=bg_defaults,
    )
