from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from asset_pipeline import AssetPipeline, GeneratedAssets


def _make_generated_assets(
    *,
    scene_id: str,
    run_dir: Path,
    image_path: Path | None,
) -> GeneratedAssets:
    audio_dir = run_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    narration_path = audio_dir / f"{scene_id}.wav"
    narration_path.write_bytes(b"")
    metadata_path = audio_dir / f"{scene_id}.json"
    metadata_path.write_text("{}", encoding="utf-8")
    return GeneratedAssets(
        narration_path=narration_path,
        narration_duration=0.0,
        narration_metadata_path=metadata_path,
        image_path=image_path,
        image_prompt_path=None,
        image_prompt_text=None,
        segments=[],
        scene_id=scene_id,
    )


def test_finalize_images_duplicates_from_success(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    run_dir = project_root / "output" / "run"
    config = {
        "output_dir": str(project_root / "output"),
        "apis": {"pollinations": {"width": 320, "height": 180}},
    }
    pipeline = AssetPipeline(run_dir=run_dir, config=config)

    success_path = pipeline.image_dir / "scene_success.jpg"
    success_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 180), (255, 0, 0)).save(success_path)

    pipeline._successful_images = [success_path]
    pipeline._image_cache["scene_success"] = success_path

    failure_target = pipeline.image_dir / "scene_failure.jpg"
    pipeline._failed_image_targets = {"scene_failure": failure_target}

    assets = [
        _make_generated_assets(scene_id="scene_success", run_dir=run_dir, image_path=success_path),
        _make_generated_assets(scene_id="scene_failure", run_dir=run_dir, image_path=None),
    ]

    pipeline.finalize_images(assets)

    assert pipeline._failed_image_targets == {}
    assert assets[1].image_path == failure_target
    assert failure_target.exists()
    assert failure_target.read_bytes() == success_path.read_bytes()


def test_finalize_images_uses_default_when_no_pool(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    default_dir = project_root / "default_img"
    default_dir.mkdir(parents=True, exist_ok=True)
    default_sample = default_dir / "sample.png"
    Image.new("RGB", (100, 50), (0, 255, 0)).save(default_sample)

    run_dir = project_root / "output" / "run"
    config = {
        "output_dir": str(project_root / "output"),
        "apis": {"pollinations": {"width": 200, "height": 112}},
    }
    pipeline = AssetPipeline(run_dir=run_dir, config=config)

    target_path = pipeline.image_dir / "scene_missing.jpg"
    pipeline._failed_image_targets = {"scene_missing": target_path}

    assets = [
        _make_generated_assets(scene_id="scene_missing", run_dir=run_dir, image_path=None),
    ]

    pipeline.finalize_images(assets)

    assert assets[0].image_path == target_path
    assert target_path.exists()
    assert pipeline._failed_image_targets == {}
