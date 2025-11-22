"""Configuration helpers for shashin_mode."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:  # pragma: no cover - optional at import time
    import yaml  # type: ignore
except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
    raise RuntimeError(
        "PyYAML is required to load renderer overrides. Install it with `pip install pyyaml`."
    ) from exc


def _hex_to_rgba(value: str, *, default_alpha: int = 220) -> Tuple[int, int, int, int]:
    text = value.lstrip("#")
    if len(text) == 8:
        return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4, 6))  # type: ignore[return-value]
    if len(text) == 6:
        r, g, b = (int(text[i : i + 2], 16) for i in (0, 2, 4))
        return (r, g, b, default_alpha)
    raise ValueError(f"Invalid RGBA hex value: {value}")


@dataclass
class LayoutConfig:
    width: int = 1280
    height: int = 720
    fps: int = 30
    image_width_ratio: float = 0.65
    image_top_padding_px: int = 36
    subtitle_font_size: int = 40
    subtitle_font_path: Optional[str] = None
    subtitle_color: Tuple[int, int, int, int] = (255, 255, 255, 255)
    subtitle_band_color: Tuple[int, int, int, int] = _hex_to_rgba("#000000cc")
    subtitle_padding_px: int = 18
    subtitle_radius_px: int = 18
    subtitle_margin_bottom_px: int = 24


@dataclass
class TimingConfig:
    min_chunk_duration: float = 1.4
    padding_seconds: float = 0.35


@dataclass
class ModePaths:
    run_dir: Path
    audio_dir: Path
    image_dir: Path
    overlay_dir: Path
    temp_dir: Path
    subtitle_path: Path
    plan_path: Path
    log_path: Path

    @classmethod
    def build(cls, base_dir: Path, run_id: str) -> "ModePaths":
        run_dir = base_dir / "output" / run_id
        audio_dir = run_dir / "audio"
        image_dir = run_dir / "images"
        overlay_dir = run_dir / "overlays"
        temp_dir = run_dir / "temp"
        subtitle_path = run_dir / f"{run_id}.srt"
        plan_path = run_dir / "plan.json"
        log_path = base_dir / "logs" / f"{run_id}.log"
        for path in (run_dir, audio_dir, image_dir, overlay_dir, temp_dir, log_path.parent):
            path.mkdir(parents=True, exist_ok=True)
        return cls(
            run_dir=run_dir,
            audio_dir=audio_dir,
            image_dir=image_dir,
            overlay_dir=overlay_dir,
            temp_dir=temp_dir,
            subtitle_path=subtitle_path,
            plan_path=plan_path,
            log_path=log_path,
        )


@dataclass
class RendererSettings:
    """Renderer override loaded from `renderer_override.yaml`."""

    name: str = "ffmpeg"
    class_path: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)
    config_path: Optional[Path] = None

    def normalized_name(self) -> str:
        return self.name.lower().strip() or "ffmpeg"


DEFAULT_RENDERER_CONFIG_PATH = Path("shashin_mode/renderer_override.yaml")


def load_renderer_settings(config_path: Optional[Path | str] = None) -> RendererSettings:
    """Load renderer override settings from YAML/JSON.

    The file is optional; missing files return defaults. Supported structure:

        ```yaml
        renderer:
            name: ffmpeg
            class: path.to.CustomRenderer
            options:
                threads: 8
        ```
    """

    path = Path(config_path).expanduser() if config_path else DEFAULT_RENDERER_CONFIG_PATH
    if not path.exists():
        return RendererSettings(config_path=path)

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise RuntimeError(f"Failed to read renderer override file: {path}") from exc

    data: Dict[str, Any]
    if path.suffix.lower() == ".json":
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:  # pragma: no cover - invalid file guard
            raise ValueError(f"Invalid JSON in renderer override file: {path}") from exc
        data = parsed if isinstance(parsed, dict) else {}
    else:
        parsed = yaml.safe_load(content) or {}
        if not isinstance(parsed, dict):
            parsed = {}
        data = parsed

    settings_dict = data.get("renderer", data)
    if not isinstance(settings_dict, dict):
        raise ValueError(f"Renderer override must be a mapping: {path}")

    name = str(settings_dict.get("name", "ffmpeg"))
    class_path = settings_dict.get("class") or settings_dict.get("class_path")
    options = settings_dict.get("options", {})
    if not isinstance(options, dict):
        options = {}

    return RendererSettings(name=name, class_path=class_path, options=options, config_path=path)

