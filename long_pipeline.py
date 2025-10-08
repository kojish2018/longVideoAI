"""High-level orchestration for long-form video generation."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from asset_pipeline import AssetPipeline, GeneratedAssets
from config_loader import AppConfig
from logging_utils import get_logger
from script_parser import ScriptDocument
from timeline_builder import Scene, TimelineBuilder, TimelinePlan
from thumbnail_generator import ThumbnailGenerator
from video_generator import ScenePlan, TextSegmentPlan, VideoGenerator

logger = get_logger(__name__)


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

    def run(self, document: ScriptDocument) -> PipelineResult:
        run_id = datetime.utcnow().strftime("longform_%Y%m%d_%H%M%S")
        run_dir = self.config.output_dir / run_id
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)

        timeline = self.builder.build(document)
        asset_pipeline = AssetPipeline(run_dir=run_dir, config=self.config.raw)

        scenes_output: List[SceneOutput] = []
        current_start = 0.0
        for scene in timeline.scenes:
            assets = asset_pipeline.prepare_scene_assets(scene)
            scene_output = self._build_scene_output(
                run_dir=run_dir,
                scene=scene,
                assets=assets,
                start_time=current_start,
            )
            scenes_output.append(scene_output)
            current_start += scene_output.narration_duration_seconds

        total_duration = round(current_start, 2)

        video_generator = VideoGenerator(self.config.raw)
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
        for scene_output in scenes:
            narration_path = run_dir / scene_output.narration_path
            image_path = run_dir / scene_output.image_path if scene_output.image_path else None
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
                )
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
        try:
            return generator.generate(
                title=title,
                base_image=base_image,
                output_name=output_name,
                subtitle=None,
            )
        except Exception as exc:  # pragma: no cover - safeguarding pipeline
            logger.exception("Thumbnail generation failed: %s", exc)
            return None

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
