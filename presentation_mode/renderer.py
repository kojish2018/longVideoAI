from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from PIL import Image

from logging_utils import get_logger
from long_form.ffmpeg.concat import concat_mp4_streamcopy
from long_form.ffmpeg.runner import run_ffmpeg

from .assets_pipeline import SceneAssets
from .models import CharacterPlacement

logger = get_logger(__name__)


@dataclass(frozen=True)
class RendererConfig:
    width: int
    height: int
    fps: int
    codec: str
    bitrate: Optional[str]
    preset: str
    crf: Optional[int]
    audio_codec: str
    audio_bitrate: Optional[str]
    audio_sample_rate: int
    panel_position: tuple[int, int]


def _read_panel_size(panel_path: Path) -> tuple[int, int]:
    with Image.open(panel_path) as img:
        return img.size


class PresentationRenderer:
    """Render per-scene videos via FFmpeg and concatenate the result."""

    def __init__(self, config: dict) -> None:
        video_cfg = config.get("video", {}) if isinstance(config, dict) else {}
        panel_cfg = config.get("presentation_panel", {}) if isinstance(config, dict) else {}
        self.cfg = RendererConfig(
            width=int(video_cfg.get("width", 1920)),
            height=int(video_cfg.get("height", 1080)),
            fps=int(video_cfg.get("fps", 30)),
            codec=str(video_cfg.get("codec", "libx264")),
            bitrate=str(video_cfg.get("bitrate")) if video_cfg.get("bitrate") else None,
            preset=str(video_cfg.get("preset", "medium")),
            crf=int(video_cfg.get("crf", 20)) if video_cfg.get("crf") else None,
            audio_codec=str(video_cfg.get("audio_codec", "aac")),
            audio_bitrate=str(video_cfg.get("audio_bitrate")) if video_cfg.get("audio_bitrate") else None,
            audio_sample_rate=int(video_cfg.get("audio_sample_rate", 48000)),
            panel_position=(
                int(panel_cfg.get("x", 0)),
                int(panel_cfg.get("y", 0)),
            ),
        )

    def render(
        self,
        *,
        run_dir: Path,
        scene_assets: Sequence[SceneAssets],
        character: Optional[CharacterPlacement],
        output_path: Path,
    ) -> Path:
        scene_dir = run_dir / "scenes"
        scene_dir.mkdir(parents=True, exist_ok=True)
        rendered_paths: List[Path] = []

        for idx, assets in enumerate(scene_assets, start=1):
            scene_output = scene_dir / f"{idx:03d}_{assets.scene.scene_id}.mp4"
            logger.info("Rendering scene video: %s", scene_output.name)
            self._render_scene(scene_output, assets, character)
            rendered_paths.append(scene_output)

        final_path = output_path
        concat_mp4_streamcopy(rendered_paths, final_path)
        return final_path

    # ------------------------------------------------------------------

    def _render_scene(
        self,
        output_path: Path,
        assets: SceneAssets,
        character: Optional[CharacterPlacement],
    ) -> None:
        cfg = self.cfg
        panel_width, panel_height = _read_panel_size(assets.panel_image_path)
        panel_x, panel_y = cfg.panel_position

        input_args: List[str] = [
            "-loop",
            "1",
            "-framerate",
            str(cfg.fps),
            "-i",
            str(assets.background_path),
            "-loop",
            "1",
            "-framerate",
            str(cfg.fps),
            "-i",
            str(assets.panel_image_path),
        ]

        character_index = None
        if character and character.image_path.exists():
            input_args += [
                "-loop",
                "1",
                "-framerate",
                str(cfg.fps),
                "-i",
                str(character.image_path),
            ]
            character_index = 2
        else:
            if character and not character.image_path.exists():
                logger.warning("Character image not found: %s", character.image_path)

        input_args += [
            "-i",
            str(assets.audio_path),
        ]

        filter_parts: List[str] = [
            f"[0:v]scale={cfg.width}:{cfg.height}:flags=bicubic,format=rgba[bg]",
            f"[1:v]scale={panel_width}:{panel_height}[panel_scaled]",
            "[panel_scaled]format=rgba[panel]",
            f"[bg][panel]overlay={panel_x}:{panel_y}[layer1]",
        ]

        video_stream = "[layer1]"
        audio_input_index = 2
        if character_index is not None and character:
            scale_factor = character.scale if character.scale else 1.0
            char_label = "[char]"
            filter_parts.append(
                f"[{character_index}:v]scale=iw*{scale_factor:.3f}:ih*{scale_factor:.3f}[char_scaled]"
            )
            filter_parts.append("[char_scaled]format=rgba[char]")
            filter_parts.append(
                f"{video_stream}{char_label}overlay={int(character.position[0])}:{int(character.position[1])}[layer2]"
            )
            video_stream = "[layer2]"
            audio_input_index = character_index + 1

        subtitles_path = assets.subtitles_path
        style = "FontName=Noto Sans JP,BorderStyle=1,Outline=3,Shadow=0"
        filter_parts.append(f"{video_stream}subtitles={self._escape_subtitle_path(subtitles_path)}:force_style='{style}'[vout]")

        filters = ";".join(filter_parts)
        codec_args: List[str] = [
            "-map",
            "[vout]",
            "-map",
            f"{audio_input_index}:a?",
            "-c:v",
            cfg.codec,
            "-r",
            str(cfg.fps),
        ]
        if cfg.crf is not None:
            codec_args += ["-crf", str(cfg.crf)]
        if cfg.preset:
            codec_args += ["-preset", cfg.preset]
        if cfg.bitrate:
            codec_args += ["-b:v", cfg.bitrate]

        codec_args += [
            "-c:a",
            cfg.audio_codec,
            "-ar",
            str(cfg.audio_sample_rate),
        ]
        if cfg.audio_bitrate:
            codec_args += ["-b:a", cfg.audio_bitrate]

        codec_args += [
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            "-y",
            str(output_path),
        ]

        args = (
            input_args
            + [
                "-filter_complex",
                filters,
            ]
            + codec_args
        )

        run_ffmpeg(args)

    @staticmethod
    def _escape_subtitle_path(path: Path) -> str:
        text = path.resolve().as_posix().replace("'", r"\'")
        return f"'{text}'"
