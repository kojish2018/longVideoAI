"""High-level orchestration for long-form video generation."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from asset_pipeline import AssetPipeline, GeneratedAssets
from config_loader import AppConfig
from logging_utils import get_logger
from prompt_translator import PromptTranslator
from script_parser import ScriptDocument
from timeline_builder import Scene, TimelineBuilder, TimelinePlan
from thumbnail_generator import ThumbnailGenerator
from video_generator import ScenePlan, TextSegmentPlan, VideoGenerator
from renderer_factory import make_renderer
from image_provider_factory import make_image_client

logger = get_logger(__name__)


_KEN_BURNS_DIRECTIONS = [(-1.0, 0.0), (1.0, 0.0), (0.0, 1.0)]


@dataclass
class TextSegmentOutput:
    segment_index: int
    start_offset: float
    duration: float
    lines: List[str]


@dataclass
class SceneOutput:
    scene_id: str
    scene_type: str
    start_time: float
    duration: float
    narration_path: str
    narration_duration_seconds: float
    narration_metadata_path: str
    image_path: str | None
    image_prompt_path: str | None
    image_prompt: str | None
    bgm_track_id: str | None
    text_segments: List[TextSegmentOutput]


@dataclass
class PipelineResult:
    run_id: str
    output_dir: Path
    plan_file: Path
    timeline_file: Path
    scenes: List[SceneOutput]
    total_duration: float
    video_path: Path
    thumbnail_path: Path | None


class LongFormPipeline:
    """Orchestrate long-form pipeline with real audio/image assets."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.builder = TimelineBuilder(config.raw)
        animation_cfg = config.raw.get("animation", {}) if isinstance(config.raw, dict) else {}
        self._ken_burns_margin = float(animation_cfg.get("ken_burns_margin", 0.08))

    def run(self, document: ScriptDocument) -> PipelineResult:
        run_id = datetime.utcnow().strftime("longform_%Y%m%d_%H%M%S")
        run_dir = self.config.output_dir / run_id
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)

        timeline = self.builder.build(document)
        asset_pipeline = AssetPipeline(run_dir=run_dir, config=self.config.raw)

        scene_assets: List[tuple[Scene, GeneratedAssets]] = []
        for scene in timeline.scenes:
            assets = asset_pipeline.prepare_scene_assets(scene)
            scene_assets.append((scene, assets))

        asset_pipeline.finalize_images([assets for _, assets in scene_assets])

        scenes_output: List[SceneOutput] = []
        current_start = 0.0
        for scene, assets in scene_assets:
            scene_output = self._build_scene_output(
                run_dir=run_dir,
                scene=scene,
                assets=assets,
                start_time=current_start,
            )
            scenes_output.append(scene_output)
            current_start += scene_output.narration_duration_seconds

        total_duration = round(current_start, 2)

        # Select renderer by config (moviepy default, ffmpeg supported)
        video_generator = make_renderer(self.config.raw)
        video_output_path = run_dir / f"{run_id}.mp4"
        scene_plans = self._build_scene_plans(run_dir, scenes_output)
        video_generator.render(
            run_dir=run_dir,
            scenes=scene_plans,
            output_path=video_output_path,
            thumbnail_title=document.thumbnail_title or "Longform Video",
        )

        thumbnail_generator = ThumbnailGenerator(self.config.raw)
        thumbnail_path = self._generate_thumbnail(
            generator=thumbnail_generator,
            run_dir=run_dir,
            run_id=run_id,
            document=document,
            scenes=scenes_output,
        )

        result = PipelineResult(
            run_id=run_id,
            output_dir=run_dir,
            plan_file=run_dir / "plan.json",
            timeline_file=run_dir / "timeline.json",
            scenes=scenes_output,
            total_duration=total_duration,
            video_path=video_output_path,
            thumbnail_path=thumbnail_path,
        )

        self._write_plan(result, document)
        self._write_timeline(result)
        logger.info(
            "Pipeline completed: %s (total duration %.2f s)",
            result.output_dir,
            result.total_duration,
        )
        logger.info("Video rendered: %s", video_output_path)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_scene_output(
        self,
        run_dir: Path,
        scene: Scene,
        assets: GeneratedAssets,
        start_time: float,
    ) -> SceneOutput:
        text_segments = [
            TextSegmentOutput(
                segment_index=segment.segment_index,
                start_offset=segment.start_offset,
                duration=segment.duration,
                lines=segment.lines,
            )
            for segment in assets.segments
        ]

        start_time_rounded = round(start_time, 2)
        duration = assets.narration_duration
        duration_rounded = round(duration, 2)

        return SceneOutput(
            scene_id=scene.scene_id,
            scene_type=scene.scene_type.value,
            start_time=start_time_rounded,
            duration=duration_rounded,
            narration_path=str(assets.narration_path.relative_to(run_dir)),
            narration_duration_seconds=duration,
            narration_metadata_path=str(assets.narration_metadata_path.relative_to(run_dir)),
            image_path=str(assets.image_path.relative_to(run_dir)) if assets.image_path else None,
            image_prompt_path=str(assets.image_prompt_path.relative_to(run_dir)) if assets.image_prompt_path else None,
            image_prompt=assets.image_prompt_text,
            bgm_track_id=scene.bgm_track_id,
            text_segments=text_segments,
        )

    def _build_scene_plans(
        self,
        run_dir: Path,
        scenes: List[SceneOutput],
    ) -> List[ScenePlan]:
        plans: List[ScenePlan] = []
        margin = self._ken_burns_margin
        for scene_output in scenes:
            narration_path = run_dir / scene_output.narration_path
            image_path = run_dir / scene_output.image_path if scene_output.image_path else None
            rng = random.Random(scene_output.scene_id)
            pan_vector = rng.choice(_KEN_BURNS_DIRECTIONS)
            segments = [
                TextSegmentPlan(
                    segment_index=segment.segment_index,
                    start_offset=segment.start_offset,
                    duration=segment.duration,
                    lines=segment.lines,
                )
                for segment in scene_output.text_segments
            ]
            plans.append(
                ScenePlan(
                    scene_id=scene_output.scene_id,
                    scene_type=scene_output.scene_type,
                    duration=scene_output.narration_duration_seconds,
                    start_time=scene_output.start_time,
                    narration_path=narration_path,
                    image_path=image_path,
                    text_segments=segments,
                    ken_burns_vector=pan_vector,
                    ken_burns_margin=margin,
                )
            )
            logger.debug(
                "Scene %s pan vector selected: %s (margin=%.3f)",
                scene_output.scene_id,
                pan_vector,
                margin,
            )
        return plans

    def _write_plan(self, result: PipelineResult, document: ScriptDocument) -> None:
        plan_payload: Dict[str, object] = {
            "run_id": result.run_id,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "thumbnail_title": document.thumbnail_title,
            "total_duration_seconds": result.total_duration,
            "video_path": str(result.video_path.relative_to(result.output_dir)),
            "thumbnail_path": str(result.thumbnail_path) if result.thumbnail_path else None,
            "scenes": [asdict(scene_output) for scene_output in result.scenes],
            "script_tags": document.tags,
            "script_description": document.description,
            "notes": {
                "description": "Generated with MoviePy. Replace assets or re-render as needed.",
            },
        }
        result.plan_file.write_text(json.dumps(plan_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug("Plan file written: %s", result.plan_file)

    def _generate_thumbnail(
        self,
        *,
        generator: ThumbnailGenerator,
        run_dir: Path,
        run_id: str,
        document: ScriptDocument,
        scenes: List[SceneOutput],
    ) -> Path | None:
        base_image = self._select_thumbnail_image(run_dir, scenes)
        title = document.thumbnail_title or "Longform Video"
        output_name = f"thumbnail_{run_id}.png"
        thumb_cfg = self.config.raw.get("thumbnail", {}) if isinstance(self.config.raw, dict) else {}
        style_override: str | None = None
        if isinstance(thumb_cfg, dict):
            style_value = thumb_cfg.get("style")
            if isinstance(style_value, str) and style_value.strip():
                style_override = style_value.strip().lower()

        active_style = (style_override or generator.default_style or "style1").strip().lower()
        base_image = self._maybe_prepare_style1_thumbnail_image(
            run_dir=run_dir,
            existing_image=base_image,
            document=document,
            style_key=active_style,
        )

        try:
            return generator.generate(
                title=title,
                base_image=base_image,
                output_name=output_name,
                subtitle=None,
                style=style_override,
            )
        except Exception as exc:  # pragma: no cover - safeguarding pipeline
            logger.exception("Thumbnail generation failed: %s", exc)
            return None

    def _maybe_prepare_style1_thumbnail_image(
        self,
        *,
        run_dir: Path,
        existing_image: Optional[Path],
        document: ScriptDocument,
        style_key: str,
    ) -> Optional[Path]:
        if style_key != "style1":
            return existing_image

        prompt_raw = (document.thumbnail_image_prompt or "").strip()
        if not prompt_raw:
            logger.debug("No image\" metadata found; using first scene image for thumbnail")
            return existing_image

        translator = PromptTranslator(self.config.raw)
        prepared_prompt, translated = self._prepare_thumbnail_prompt(prompt_raw, translator)
        if not prepared_prompt:
            logger.warning("Thumbnail image prompt resolved empty; falling back to first scene image")
            return existing_image

        asset_dir = run_dir / "thumbnail_inputs"
        asset_dir.mkdir(parents=True, exist_ok=True)
        image_client = make_image_client(self.config.raw)
        provider_name = type(image_client).__name__
        target_path = asset_dir / "style1_base.jpg"

        fetched = image_client.fetch(prepared_prompt, target_path)
        if fetched:
            metadata_path = asset_dir / "style1_prompt.json"
            self._write_thumbnail_prompt_metadata(
                metadata_path,
                original_prompt=prompt_raw,
                final_prompt=prepared_prompt,
                translated=translated,
            )
            logger.info(
                "Prepared style1 thumbnail hero image via %s (translated=%s)",
                provider_name,
                translated,
            )
            return fetched

        logger.warning(
            "%s thumbnail generation failed; falling back to scene-derived image",
            provider_name,
        )
        return existing_image

    def _prepare_thumbnail_prompt(
        self,
        prompt_raw: str,
        translator: PromptTranslator,
    ) -> tuple[str, bool]:
        normalized = prompt_raw.strip()
        if not normalized:
            return "", False

        contains_japanese = self._contains_japanese(normalized)
        if not contains_japanese:
            logger.info("Thumbnail prompt language=English (heuristic); using as-is")
            return normalized, False

        logger.info("Thumbnail prompt language=Japanese (heuristic); requesting DeepL translation")
        translated = translator.translate(normalized).strip()
        if not translated:
            logger.warning("Thumbnail prompt translation returned empty; using original text")
            return normalized, False

        if self._contains_japanese(translated):
            logger.warning("Thumbnail prompt translation still contains Japanese; using original text")
            return normalized, False

        if translated == normalized:
            logger.info("DeepL translation identical to original; treating as already English")
            return normalized, False

        logger.info("Thumbnail prompt translated to English via DeepL")
        return translated, True

    @staticmethod
    def _contains_japanese(text: str) -> bool:
        for char in text:
            codepoint = ord(char)
            if (
                0x3000 <= codepoint <= 0x303F  # punctuation
                or 0x3040 <= codepoint <= 0x30FF  # hiragana / katakana
                or 0x31F0 <= codepoint <= 0x31FF  # katakana phonetic extensions
                or 0x3400 <= codepoint <= 0x4DBF  # CJK Extension A
                or 0x4E00 <= codepoint <= 0x9FFF  # CJK Unified Ideographs
                or 0xFF66 <= codepoint <= 0xFF9D  # half-width katakana
            ):
                return True
        return False

    def _write_thumbnail_prompt_metadata(
        self,
        metadata_path: Path,
        *,
        original_prompt: str,
        final_prompt: str,
        translated: bool,
    ) -> None:
        payload = {
            "original_prompt": original_prompt,
            "final_prompt": final_prompt,
            "translated": translated,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _select_thumbnail_image(self, run_dir: Path, scenes: List[SceneOutput]) -> Path | None:
        for scene in scenes:
            if scene.image_path:
                candidate = run_dir / scene.image_path
                if candidate.exists():
                    return candidate
        return None

    def _write_timeline(self, result: PipelineResult) -> None:
        timeline_payload = {
            "total_duration_seconds": result.total_duration,
            "scenes": [
                {
                    "scene_id": scene_output.scene_id,
                    "scene_type": scene_output.scene_type,
                    "start_time": scene_output.start_time,
                    "duration": scene_output.duration,
                    "image_prompt": scene_output.image_prompt,
                    "bgm_track_id": scene_output.bgm_track_id,
                }
                for scene_output in result.scenes
            ],
        }
        result.timeline_file.write_text(json.dumps(timeline_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug("Timeline file written: %s", result.timeline_file)
