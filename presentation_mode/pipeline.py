from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence

from logging_utils import get_logger

from config_loader import AppConfig

from .assets_pipeline import PresentationAssetPipeline, SceneAssets
from .models import PresentationScript
from .renderer import PresentationRenderer

logger = get_logger(__name__)


@dataclass
class PresentationResult:
    run_id: str
    output_dir: Path
    video_path: Path
    plan_path: Path
    timeline_path: Path
    scenes: List[SceneAssets]
    total_duration: float


class PresentationPipeline:
    """High-level orchestration for presentation-mode videos."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.renderer = PresentationRenderer(config.raw)

    def run(self, script: PresentationScript) -> PresentationResult:
        run_id = datetime.utcnow().strftime("presentation_%Y%m%d_%H%M%S")
        output_root = self.config.output_dir
        output_root.mkdir(parents=True, exist_ok=True)
        run_dir = output_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        asset_pipeline = PresentationAssetPipeline(run_dir=run_dir, config=self.config.raw)
        scene_assets = asset_pipeline.prepare(script)
        total_duration = sum(asset.duration for asset in scene_assets)

        final_video_path = run_dir / f"{run_id}.mp4"
        self.renderer.render(
            run_dir=run_dir,
            scene_assets=scene_assets,
            character=script.character,
            output_path=final_video_path,
        )

        plan_path = run_dir / "plan.json"
        timeline_path = run_dir / "timeline.json"
        self._write_plan(plan_path, run_id, script, scene_assets, final_video_path)
        self._write_timeline(timeline_path, scene_assets)

        logger.info("Presentation pipeline complete: %s", final_video_path)
        return PresentationResult(
            run_id=run_id,
            output_dir=run_dir,
            video_path=final_video_path,
            plan_path=plan_path,
            timeline_path=timeline_path,
            scenes=list(scene_assets),
            total_duration=total_duration,
        )

    def _write_plan(
        self,
        path: Path,
        run_id: str,
        script: PresentationScript,
        scenes: Sequence[SceneAssets],
        video_path: Path,
    ) -> None:
        payload: Dict[str, object] = {
            "run_id": run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "title": script.title,
            "description": script.description,
            "tags": list(script.tags),
            "video_path": str(video_path.name),
            "scenes": [
                {
                    "scene_id": scene.scene.scene_id,
                    "panel_title": scene.scene.panel.title,
                    "panel_body": list(scene.scene.panel.body),
                    "conclusion": scene.scene.panel.conclusion,
                    "background": str(self._relative(scene.background_path, path.parent)),
                    "audio": str(self._relative(scene.audio_path, path.parent)),
                    "subtitles": str(self._relative(scene.subtitles_path, path.parent)),
                    "panel_image": str(self._relative(scene.panel_image_path, path.parent)),
                    "subtitle_lines": [
                        {
                            "index": line.index,
                            "start": line.start,
                            "duration": line.duration,
                            "text": line.text,
                        }
                        for line in scene.subtitle_lines
                    ],
                }
                for scene in scenes
            ],
        }

        if script.character:
            payload["character_image"] = str(script.character.image_path)
            payload["character_position"] = {
                "x": script.character.position[0],
                "y": script.character.position[1],
                "scale": script.character.scale,
            }

        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_timeline(self, path: Path, scenes: Sequence[SceneAssets]) -> None:
        entries = [
            {
                "scene_id": scene.scene.scene_id,
                "start": round(scene.start_time, 2),
                "duration": round(scene.duration, 2),
                "narration_audio": str(self._relative(scene.audio_path, path.parent)),
            }
            for scene in scenes
        ]
        payload = {
            "total_duration": round(sum(scene.duration for scene in scenes), 2),
            "scenes": entries,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _relative(target: Path, base: Path) -> Path:
        try:
            return target.resolve().relative_to(base.resolve())
        except ValueError:
            return target.resolve()

