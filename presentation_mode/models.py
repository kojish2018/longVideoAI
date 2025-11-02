from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class PanelFontOverrides:
    title_size: Optional[int] = None
    body_size: Optional[int] = None
    conclusion_size: Optional[int] = None


@dataclass(frozen=True)
class PanelContent:
    title: str
    body: Tuple[str, ...]
    conclusion: Optional[str] = None
    font_overrides: PanelFontOverrides = field(default_factory=PanelFontOverrides)


@dataclass(frozen=True)
class CharacterPlacement:
    image_path: Path
    position: Tuple[float, float] = (0.0, 0.0)
    scale: float = 1.0


@dataclass(frozen=True)
class PresentationScene:
    scene_id: str
    narration: str
    panel: PanelContent
    background_prompt: Optional[str] = None
    subtitle_override: Optional[str] = None


@dataclass(frozen=True)
class BackgroundDefaults:
    prompt: Optional[str] = None
    change_interval_seconds: int = 120


@dataclass(frozen=True)
class PresentationScript:
    title: str
    scenes: Tuple[PresentationScene, ...]
    tags: Tuple[str, ...] = field(default_factory=tuple)
    description: Optional[str] = None
    character: Optional[CharacterPlacement] = None
    background_defaults: BackgroundDefaults = field(default_factory=BackgroundDefaults)

    def background_prompt_for_index(self, scene_index: int) -> Optional[str]:
        if scene_index < 0 or scene_index >= len(self.scenes):
            return None
        scene = self.scenes[scene_index]
        if scene.background_prompt:
            return scene.background_prompt
        return self.background_defaults.prompt

    def change_interval(self) -> int:
        return max(30, self.background_defaults.change_interval_seconds)

