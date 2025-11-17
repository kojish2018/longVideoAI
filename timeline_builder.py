"""Timeline builder for long-form video scenes."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from logging_utils import get_logger
from script_parser import ScriptDocument, ScriptSection

logger = get_logger(__name__)


@dataclass
class SceneChunk:
    section_index: int
    lines: List[str]
    raw_text: str
    word_count: int
    estimated_duration: float

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class SceneType(str, Enum):
    OPENING = "opening"
    CONTENT = "content"
    OUTRO = "outro"


@dataclass
class Scene:
    scene_id: str
    scene_type: SceneType
    start_time: float
    duration: float
    chunks: List[SceneChunk]
    image_prompt: Optional[str]
    bgm_track_id: Optional[str]
    primary_prompt: Optional[str] = None


@dataclass
class TimelinePlan:
    scenes: List[Scene]

    @property
    def total_duration(self) -> float:
        if not self.scenes:
            return 0.0
        last_scene = self.scenes[-1]
        return round(last_scene.start_time + last_scene.duration, 2)


class TimelineBuilder:
    """Build a grouped timeline for long-form videos."""

    def __init__(
        self,
        config: Dict,
        *,
        words_per_minute: int = 150,
    ) -> None:
        self.config = config
        self.words_per_second = max(words_per_minute / 60.0, 0.1)
        self.padding_seconds = float(config.get("simple_mode", {}).get("padding_seconds", 0.5))

        simple_cfg = config.get("simple_mode", {})
        self.duration_mode = str(simple_cfg.get("duration_mode", "voice")).lower()

        sections_cfg = config.get("sections", {})
        self.default_duration = float(sections_cfg.get("default_duration_seconds", 60))
        self.min_duration = float(sections_cfg.get("min_duration_seconds", 5))
        self.max_duration = float(sections_cfg.get("max_duration_seconds", 120))
        raw_max_chunks = sections_cfg.get("max_chunks_per_scene")
        try:
            max_chunks = int(raw_max_chunks) if raw_max_chunks is not None else 2
        except (TypeError, ValueError):
            logger.warning(
                "Invalid max_chunks_per_scene=%s; falling back to default", raw_max_chunks
            )
            max_chunks = 2
        self.max_chunks_per_scene = max_chunks if max_chunks > 0 else 0

        bgm_library = config.get("bgm", {}).get("library", [])
        self.bgm_cycle = [track.get("id") for track in bgm_library if track.get("id")]
        if not self.bgm_cycle:
            self.bgm_cycle = [None]

    def build(self, document: ScriptDocument) -> TimelinePlan:
        if not document.sections:
            raise ValueError("Script document must contain at least one section")

        scenes: List[Scene] = []
        current_start = 0.0

        # Opening
        opening_section = document.sections[0]
        opening_chunk = self._build_chunk(opening_section, SceneType.OPENING)
        opening_primary_prompt = self._extract_focus_text([opening_chunk])
        opening_scene = Scene(
            scene_id="S001",
            scene_type=SceneType.OPENING,
            start_time=round(current_start, 2),
            duration=round(opening_chunk.estimated_duration, 2),
            chunks=[opening_chunk],
            image_prompt=None,
            bgm_track_id=self._select_bgm(1),
        )
        scenes.append(opening_scene)
        current_start += opening_chunk.estimated_duration

        scene_counter = 2
        group_chunks: List[SceneChunk] = []
        group_duration = 0.0
        first_content_pending = True

        for section in document.sections[1:]:
            chunk = self._build_chunk(section, SceneType.CONTENT)

            if not group_chunks:
                group_chunks.append(chunk)
                group_duration = chunk.estimated_duration
                continue

            if self.max_chunks_per_scene and len(group_chunks) >= self.max_chunks_per_scene:
                primary_prompt = (
                    opening_primary_prompt if first_content_pending else None
                )
                scenes.append(
                    self._finalize_content_scene(
                        scene_counter,
                        current_start,
                        group_chunks,
                        primary_prompt=primary_prompt,
                    )
                )
                first_content_pending = False
                current_start += group_duration
                scene_counter += 1
                group_chunks = [chunk]
                group_duration = chunk.estimated_duration
                continue

            proposed_duration = group_duration + chunk.estimated_duration
            should_close = (
                group_duration >= self.min_duration
                and (
                    group_duration >= self.default_duration
                    or proposed_duration > self.max_duration
                )
            )

            if should_close:
                primary_prompt = (
                    opening_primary_prompt if first_content_pending else None
                )
                scenes.append(
                    self._finalize_content_scene(
                        scene_counter,
                        current_start,
                        group_chunks,
                        primary_prompt=primary_prompt,
                    )
                )
                first_content_pending = False
                current_start += group_duration
                scene_counter += 1
                group_chunks = [chunk]
                group_duration = chunk.estimated_duration
            else:
                group_chunks.append(chunk)
                group_duration = proposed_duration

        if group_chunks:
            primary_prompt = (
                opening_primary_prompt if first_content_pending else None
            )
            scenes.append(
                self._finalize_content_scene(
                    scene_counter,
                    current_start,
                    group_chunks,
                    primary_prompt=primary_prompt,
                )
            )
            first_content_pending = False
            current_start += group_duration

        logger.info(
            "Timeline built with %d scenes (total %.2f seconds)",
            len(scenes),
            current_start,
        )
        return TimelinePlan(scenes=scenes)

    def _build_chunk(self, section: ScriptSection, scene_type: SceneType) -> SceneChunk:
        estimated = self._estimate_duration(section, scene_type)
        return SceneChunk(
            section_index=section.index,
            lines=section.lines,
            raw_text=section.raw_text,
            word_count=section.word_count,
            estimated_duration=estimated,
        )

    def _finalize_content_scene(
        self,
        scene_number: int,
        start_time: float,
        chunks: List[SceneChunk],
        *,
        primary_prompt: Optional[str] = None,
    ) -> Scene:
        total_duration = sum(chunk.estimated_duration for chunk in chunks)
        scene_id = f"S{scene_number:03d}"
        focus_text = self._extract_focus_text(chunks)
        bgm_track = self._select_bgm(scene_number)
        return Scene(
            scene_id=scene_id,
            scene_type=SceneType.CONTENT,
            start_time=round(start_time, 2),
            duration=round(total_duration, 2),
            chunks=[chunk for chunk in chunks],
            image_prompt=focus_text,
            bgm_track_id=bgm_track,
            primary_prompt=primary_prompt,
        )

    def _estimate_duration(self, section: ScriptSection, scene_type: SceneType) -> float:
        if section.word_count == 0:
            if scene_type is SceneType.OPENING:
                return max(self.padding_seconds, 3.0)
            return self.default_duration

        voice_seconds = section.word_count / self.words_per_second
        voice_seconds += self.padding_seconds * (len(section.lines) - 1)

        if scene_type is SceneType.OPENING:
            min_duration = max(self.padding_seconds, 3.0)
            max_duration = max(min_duration, self.max_duration)
        else:
            if self.duration_mode == "voice":
                min_duration = max(self.padding_seconds, 1.0)
            else:
                min_duration = self.min_duration
            max_duration = self.max_duration

        candidate = max(voice_seconds, min_duration)
        if max_duration > 0:
            clamped = min(candidate, max_duration)
        else:
            clamped = candidate
        logger.debug(
            "Section %d type=%s word_count=%d -> duration %.2f seconds",
            section.index,
            scene_type.value,
            section.word_count,
            clamped,
        )
        return clamped

    def _extract_focus_text(self, chunks: List[SceneChunk]) -> Optional[str]:
        if not chunks:
            return None
        first_chunk = chunks[0]
        text = first_chunk.text.strip()
        return text if text else None

    def _select_bgm(self, index: int) -> Optional[str]:
        if not self.bgm_cycle:
            return None
        position = (index - 1) % len(self.bgm_cycle)
        return self.bgm_cycle[position]
