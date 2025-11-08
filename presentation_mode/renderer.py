from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from PIL import Image

from logging_utils import get_logger
from long_form.ffmpeg.concat import concat_mp4_streamcopy
from long_form.ffmpeg.runner import run_ffmpeg

from .assets_pipeline import SceneAssets
from .bgm import PresentationBgmMixer
from .models import CharacterPlacement
from .utils import build_vertical_bob_expression

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


def _read_image_size(image_path: Path) -> tuple[int, int]:
    with Image.open(image_path) as img:
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
        self.bgm_mixer = PresentationBgmMixer(config)
        start_sound_root = Path(__file__).resolve().parent / "sound_effects" / "start_sounds"
        self.start_sound_dir = start_sound_root
        self.start_sound_paths = self._discover_start_sounds(start_sound_root)
        self._rng = random.Random()

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

        start_events: List[tuple[Path, float, float]] = []

        for idx, assets in enumerate(scene_assets, start=1):
            scene_output = scene_dir / f"{idx:03d}_{assets.scene.scene_id}.mp4"
            logger.info("Rendering scene video: %s", scene_output.name)
            if idx > 1 and self.start_sound_paths:
                sfx_path = self._rng.choice(self.start_sound_paths)
                start_events.append((sfx_path, assets.start_time, assets.pre_padding))
                logger.debug(
                    "Selected start sound '%s' at %.3fs for scene %s",
                    sfx_path.name,
                    assets.start_time,
                    assets.scene.scene_id,
                )
            self._render_scene(scene_output, assets, character)
            rendered_paths.append(scene_output)

        concat_path = run_dir / "presentation_concat.mp4"
        concat_mp4_streamcopy(rendered_paths, concat_path)

        total_duration = sum(asset.duration for asset in scene_assets)
        concat_with_sfx = self._apply_start_sounds(
            concat_path,
            start_events,
            total_duration=total_duration,
        )
        final_path = self.bgm_mixer.mix(
            concat_with_sfx,
            output_path,
            total_duration=total_duration,
            audio_codec=self.cfg.audio_codec,
            audio_sample_rate=self.cfg.audio_sample_rate,
            audio_bitrate=self.cfg.audio_bitrate,
        )

        if final_path != concat_with_sfx and concat_with_sfx.exists():
            try:
                concat_with_sfx.unlink()
            except Exception:
                logger.warning("Failed to delete temporary concatenated file: %s", concat_with_sfx)

        return final_path

    # ------------------------------------------------------------------

    def _render_scene(
        self,
        output_path: Path,
        assets: SceneAssets,
        character: Optional[CharacterPlacement],
    ) -> None:
        cfg = self.cfg
        panel_width, panel_height = _read_image_size(assets.panel_image_path)
        panel_x, panel_y = cfg.panel_position

        bg_scale_factor = 1.10
        bg_scaled_width = max(1, int(round(cfg.width * bg_scale_factor)))
        bg_scaled_height = max(1, int(round(cfg.height * bg_scale_factor)))
        bg_crop_x = max(0, (bg_scaled_width - cfg.width) // 2)

        input_args: List[str] = []
        input_count = 0

        def add_loop_input(path: Path) -> int:
            nonlocal input_count
            input_args.extend([
                "-loop",
                "1",
                "-framerate",
                str(cfg.fps),
                "-i",
                str(path),
            ])
            idx = input_count
            input_count += 1
            return idx

        def add_input(path: Path) -> int:
            nonlocal input_count
            input_args.extend([
                "-i",
                str(path),
            ])
            idx = input_count
            input_count += 1
            return idx

        background_index = add_loop_input(assets.background_path)
        panel_index = add_loop_input(assets.panel_image_path)

        character_index: Optional[int] = None
        character_scale_factor: float | None = None
        character_overlay_x: int | None = None
        character_overlay_y: int | None = None
        if character and character.image_path.exists():
            character_index = add_loop_input(character.image_path)

            raw_char_width, raw_char_height = _read_image_size(character.image_path)
            base_scale = character.scale if character.scale and character.scale > 0 else 1.0
            character_scale_factor = base_scale * 1.8

            scaled_width = max(1, int(round(raw_char_width * character_scale_factor)))
            scaled_height = max(1, int(round(raw_char_height * character_scale_factor)))

            panel_right = panel_x + panel_width
            remaining_width = max(0, cfg.width - panel_right)
            base_x = panel_right + max((remaining_width - scaled_width) / 2.0, 0.0)
            base_y = max((cfg.height - scaled_height) / 2.0, 0.0)

            offset_x, offset_y = character.position
            base_x += offset_x
            base_y += offset_y

            max_x = max(cfg.width - scaled_width, 0)
            max_y = max(cfg.height - scaled_height, 0)
            character_overlay_x = int(round(min(max(base_x, 0.0), max_x)))
            character_overlay_y = int(round(min(max(base_y, 0.0), max_y)))
        else:
            if character and not character.image_path.exists():
                logger.warning("Character image not found: %s", character.image_path)

        narration_index = add_input(assets.audio_path)
        audio_input_index = narration_index

        filter_parts: List[str] = [
            f"[{background_index}:v]scale={bg_scaled_width}:{bg_scaled_height}:flags=bicubic,"
            f"crop={cfg.width}:{cfg.height}:{bg_crop_x}:0,format=rgba[bg]",
            f"[{panel_index}:v]scale={panel_width}:{panel_height}[panel_scaled]",
            "[panel_scaled]format=rgba[panel]",
            f"[bg][panel]overlay={panel_x}:{panel_y}[layer1]",
        ]

        video_stream = "[layer1]"
        if (
            character_index is not None
            and character
            and character_scale_factor is not None
            and character_overlay_x is not None
            and character_overlay_y is not None
        ):
            char_label = "[char]"
            filter_parts.append(
                f"[{character_index}:v]scale=iw*{character_scale_factor:.3f}:ih*{character_scale_factor:.3f}[char_scaled]"
            )
            filter_parts.append("[char_scaled]format=rgba[char]")
            animation_expr = None
            if getattr(character, "animation", None) and character.animation.enabled:
                animation_expr = build_vertical_bob_expression(
                    character_overlay_y,
                    amplitude=character.animation.amplitude,
                    move_duration=character.animation.move_duration,
                    rest_duration=character.animation.rest_duration,
                )

            if animation_expr:
                overlay_cmd = (
                    f"{video_stream}{char_label}overlay=x='{character_overlay_x:.3f}':"
                    f"y='{animation_expr}':eval=frame[layer2]"
                )
            else:
                overlay_cmd = f"{video_stream}{char_label}overlay={character_overlay_x}:{character_overlay_y}[layer2]"

            filter_parts.append(overlay_cmd)
            video_stream = "[layer2]"

        subtitles_path = assets.subtitles_path
        style = "FontName=Noto Sans JP,BorderStyle=1,Outline=3,Shadow=0"
        filter_parts.append(f"{video_stream}subtitles={self._escape_subtitle_path(subtitles_path)}:force_style='{style}'[vout]")

        filters = ";".join(filter_parts)
        scene_duration = max(assets.duration, 0.05)

        codec_args: List[str] = [
            "-map",
            "[vout]",
            "-map",
            f"{audio_input_index}:a?",
            "-t",
            f"{scene_duration:.6f}",
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

    def _discover_start_sounds(self, directory: Path) -> List[Path]:
        try:
            files = [p.resolve() for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".mp3"]
        except FileNotFoundError:
            logger.warning("Start sound directory not found: %s", directory)
            return []

        files.sort()
        if not files:
            logger.warning("No start sound mp3 files found in directory: %s", directory)
        return files

    def _apply_start_sounds(
        self,
        base_video: Path,
        start_events: Sequence[tuple[Path, float, float]],
        *,
        total_duration: float,
    ) -> Path:
        if not start_events:
            return base_video

        sr = self.cfg.audio_sample_rate
        output_path = base_video.with_name(base_video.stem + "_sfx.mp4")

        args: List[str] = ["-i", str(base_video)]
        filter_parts: List[str] = [
            f"[0:a]aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo[base]"
        ]

        mix_inputs = ["[base]"]
        fade_duration = 0.2
        max_duration = 0.6
        base_idx_offset = 1
        sfx_volume = 0.32

        for idx, (path, start_time, pre_padding) in enumerate(start_events, start=base_idx_offset):
            args += ["-i", str(path)]
            label = f"[s{idx}]"
            delay_ms = max(int(round(start_time * 1000.0)), 0)
            available = max_duration
            if pre_padding and pre_padding > 0:
                available = min(max(pre_padding, 0.2) + 0.2, max_duration)
            available = min(available, max(total_duration - start_time, 0.1))
            fade = min(fade_duration, available / 2.0)
            start_trim = max(available - fade, 0.0)
            filter_parts.append(
                f"[{idx}:a]aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo"
                f",atrim=0:{available:.3f},asetpts=PTS-STARTPTS"
                f",afade=t=in:st=0:d={min(0.08, available):.3f}"
                f",afade=t=out:st={start_trim:.3f}:d={fade:.3f}"
                f",volume={sfx_volume:.3f},adelay={delay_ms}|{delay_ms}{label}"
            )
            mix_inputs.append(label)

        input_count = len(mix_inputs)
        mix_chain = "".join(mix_inputs)
        filter_parts.append(
            f"{mix_chain}amix=inputs={input_count}:duration=first:dropout_transition=0[startmix]"
        )
        filter_parts.append(
            f"[startmix]loudnorm=I=-14:LRA=7:TP=-1.5," \
            f"aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo[aout]"
        )

        args += [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            self.cfg.audio_codec,
            "-ar",
            str(sr),
            "-movflags",
            "+faststart",
            "-shortest",
            "-y",
            str(output_path),
        ]
        if self.cfg.audio_bitrate:
            args += ["-b:a", self.cfg.audio_bitrate]

        try:
            run_ffmpeg(args)
        except Exception:
            logger.exception("Failed to overlay start sounds; falling back to base video")
            return base_video

        try:
            base_video.unlink(missing_ok=True)
        except Exception:
            logger.warning("Failed to remove temporary base video without SFX: %s", base_video)

        return output_path
