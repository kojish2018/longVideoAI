"""MoviePy-based renderer for long-form videos."""
from __future__ import annotations

import logging
import numpy as np
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
from moviepy.audio.io.AudioFileClip import AudioFileClip
from moviepy.audio.AudioClip import AudioArrayClip, CompositeAudioClip
import moviepy.audio.fx.all as afx
from moviepy.editor import (
    ColorClip,
    CompositeVideoClip,
    ImageClip,
    concatenate_videoclips,
)
import pyloudnorm as pyln
from animation_config import resolve_ken_burns_profile

logger = logging.getLogger(__name__)


@dataclass
class RenderConfig:
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
    padding_seconds: float
    ken_burns_zoom: float
    ken_burns_offset: float
    ken_burns_margin: float
    font_path: Optional[str]
    body_font_size: int
    body_color: Tuple[int, int, int]
    accent_color: Tuple[int, int, int]
    band_color: Tuple[int, int, int, int]
    title_font_size: int
    opening_title_font_size: int


@dataclass
class TextSegmentPlan:
    segment_index: int
    start_offset: float
    duration: float
    lines: List[str]


@dataclass
class ScenePlan:
    scene_id: str
    scene_type: str
    duration: float
    start_time: float
    narration_path: Path
    image_path: Optional[Path]
    text_segments: List[TextSegmentPlan]
    ken_burns_vector: Tuple[float, float] = (-1.0, -1.0)
    ken_burns_margin: float = 0.0


class VideoGenerator:
    """Render long-form video using MoviePy with lightweight effects."""

    def __init__(self, config: Dict[str, object]) -> None:
        video_cfg = config.get("video", {}) if isinstance(config, dict) else {}
        text_cfg = config.get("text", {}) if isinstance(config, dict) else {}
        animation_cfg = config.get("animation", {}) if isinstance(config, dict) else {}

        colors = text_cfg.get("colors", {}) if isinstance(text_cfg, dict) else {}
        kb_profile = resolve_ken_burns_profile(animation_cfg)

        # Resolve sizes
        thumb_title_size = int(config.get("thumbnail", {}).get("title_font_size", 72)) if isinstance(config, dict) else 72
        # 開幕シーンは固定で小さめのサイズを使用（要望により固定値）
        opening_title_size = 75

        self.render_cfg = RenderConfig(
            width=int(video_cfg.get("width", 1280)),
            height=int(video_cfg.get("height", 720)),
            fps=int(video_cfg.get("fps", 30)),
            codec=str(video_cfg.get("codec", "libx264")),
            bitrate=video_cfg.get("bitrate"),
            preset=str(video_cfg.get("preset", "ultrafast")),
            crf=int(video_cfg.get("crf", 20)) if video_cfg.get("crf") else 20,
            audio_codec=str(video_cfg.get("audio_codec", "aac")),
            audio_bitrate=video_cfg.get("audio_bitrate"),
            audio_sample_rate=int(video_cfg.get("audio_sample_rate", 48000)),
            padding_seconds=kb_profile.padding_seconds,
            ken_burns_zoom=kb_profile.zoom,
            ken_burns_offset=kb_profile.offset,
            ken_burns_margin=kb_profile.margin,
            font_path=text_cfg.get("font_path"),
            body_font_size=int(text_cfg.get("default_size", 36)),
            body_color=_hex_to_rgb(colors.get("default", "#FFFFFF")),
            accent_color=_hex_to_rgb(colors.get("highlight", "#FF4B2B")),
            band_color=_hex_to_rgba(colors.get("background_box", "#000000F0")),
            title_font_size=thumb_title_size,
            opening_title_font_size=opening_title_size,
        )

        self._font_cache: Dict[Tuple[int, bool], ImageFont.FreeTypeFont] = {}
        self._overlay_cache: Dict[Tuple[str, int, Tuple[str, ...]], Path] = {}
        self._opening_cache: Dict[Tuple[str, Tuple[str, ...]], Path] = {}
        bgm_cfg = config.get("bgm", {}) if isinstance(config, dict) else {}
        directory = str(bgm_cfg.get("directory", "background_music") or "background_music").strip()
        self._bgm_directory = directory if directory else "background_music"
        selected = str(bgm_cfg.get("selected", "Fulero.mp3") or "Fulero.mp3").strip()
        if selected and not selected.lower().endswith(".mp3"):
            selected = f"{selected}.mp3"
        self._bgm_selected = selected

    def _resolve_bgm_path(self) -> Optional[Path]:
        if not self._bgm_selected:
            return None

        candidates: List[Path] = []
        selected_path = Path(self._bgm_selected)
        if not selected_path.is_absolute():
            candidates.append(Path(self._bgm_directory) / self._bgm_selected)
        candidates.append(selected_path)

        for candidate in candidates:
            try:
                if candidate.exists():
                    return candidate
            except Exception:
                continue
        return None

    def render(
        self,
        *,
        run_dir: Path,
        scenes: Iterable[ScenePlan],
        output_path: Path,
        thumbnail_title: str,
    ) -> Path:
        clips: List[CompositeVideoClip] = []
        final_clip: Optional[CompositeVideoClip] = None
        try:
            for scene in scenes:
                clip = self._build_scene_clip(run_dir, scene, thumbnail_title)
                clips.append(clip)

            if not clips:
                raise RuntimeError("No clips generated for rendering")

            final_clip = concatenate_videoclips(clips, method="compose")

            # --------------------------------------------------------------
            # Mix narration (existing) with background music looped/cut to fit
            # Requested track: background_music/Vandals.mp3
            # Behavior: if music is shorter => loop; if longer => cut
            # Volume: small (focus on narration) with gentle fade in/out
            # --------------------------------------------------------------
            try:
                bgm_path = self._resolve_bgm_path()
                if bgm_path and bgm_path.exists() and final_clip.audio is not None:
                    narration = final_clip.audio
                    bgm = AudioFileClip(str(bgm_path))
                    bgm = self._normalize_bgm_clip(bgm)
                    bgm = afx.audio_loop(bgm, duration=final_clip.duration)
                    # Reduce BGM level (approx -22 dB)
                    bgm = bgm.volumex(0.08)
                    bgm = afx.audio_fadein(bgm, 0.5)
                    bgm = afx.audio_fadeout(bgm, 1.0)
                    mixed = CompositeAudioClip([narration, bgm]).set_duration(final_clip.duration)
                    # Ensure FPS is set for normalization (CompositeAudioClip may miss fps)
                    mixed = mixed.set_fps(self.render_cfg.audio_sample_rate)
                    # Normalize combined audio to prevent clipping, then keep ~-1 dB headroom
                    try:
                        normalized = afx.audio_normalize(mixed)
                        normalized = normalized.volumex(0.89)
                        final_clip = final_clip.set_audio(normalized)
                    except Exception as exc_norm:  # pragma: no cover - safety net
                        logger.exception("Audio normalize failed, using unnormalized mix: %s", exc_norm)
                        final_clip = final_clip.set_audio(mixed)
                else:
                    logger.warning(
                        "BGM file not found or audio missing: selection=%s directory=%s",
                        self._bgm_selected,
                        self._bgm_directory,
                    )
            except Exception as exc:  # pragma: no cover - safeguard audio pipeline
                logger.exception("Failed to mix BGM: %s", exc)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temp_audio = run_dir / "temp_audio.m4a"

            final_clip.write_videofile(
                str(output_path),
                fps=self.render_cfg.fps,
                codec=self.render_cfg.codec,
                audio_codec=self.render_cfg.audio_codec,
                audio_fps=self.render_cfg.audio_sample_rate,
                audio_bitrate=self.render_cfg.audio_bitrate,
                bitrate=self.render_cfg.bitrate,
                preset=self.render_cfg.preset,
                ffmpeg_params=[
                    "-crf",
                    str(self.render_cfg.crf),
                    "-ar",
                    str(self.render_cfg.audio_sample_rate),
                ],
                temp_audiofile=str(temp_audio),
                remove_temp=True,
                threads=4,
                verbose=True,
            )
            return output_path
        finally:
            for clip in clips:
                try:
                    clip.close()
                except Exception:  # pragma: no cover
                    pass
            if final_clip is not None:
                try:
                    final_clip.close()
                except Exception:  # pragma: no cover
                    pass

    # ------------------------------------------------------------------
    # Scene builders
    # ------------------------------------------------------------------

    def _normalize_bgm_clip(self, clip: AudioFileClip) -> AudioFileClip:
        sample_rate = self.render_cfg.audio_sample_rate
        try:
            array = clip.to_soundarray(fps=sample_rate)
        except Exception:
            return clip

        if array.size == 0:
            return clip

        if array.ndim == 1:
            array = array[:, np.newaxis]

        meter = pyln.Meter(sample_rate)
        loudness = meter.integrated_loudness(array)
        target_lufs = -28.0

        if np.isfinite(loudness):
            gain_db = target_lufs - loudness
            gain = 10 ** (gain_db / 20)
            normalized = np.clip(array * gain, -1.0, 1.0).astype(np.float32)
        else:
            normalized = array.astype(np.float32)

        duration = clip.duration
        clip.close()

        normalized_clip = AudioArrayClip(normalized, fps=sample_rate)
        if duration is not None:
            normalized_clip = normalized_clip.set_duration(duration)
        return normalized_clip

    def _build_scene_clip(
        self,
        run_dir: Path,
        scene: ScenePlan,
        thumbnail_title: str,
    ) -> CompositeVideoClip:
        if scene.scene_type == "opening":
            visual_clip = self._build_opening_clip(run_dir, scene, thumbnail_title)
        else:
            visual_clip = self._build_content_clip(run_dir, scene)

        audio_clip = AudioFileClip(str(scene.narration_path))
        audio_duration = audio_clip.duration

        target_duration = scene.duration
        if audio_duration is not None and audio_duration > 0:
            if target_duration > 0:
                target_duration = min(target_duration, audio_duration)
            else:
                target_duration = audio_duration

        if target_duration <= 0:
            target_duration = audio_duration if audio_duration and audio_duration > 0 else scene.duration
        if target_duration <= 0:
            target_duration = 0.01

        audio_clip = audio_clip.subclip(0, target_duration)
        visual_clip = visual_clip.set_duration(target_duration)
        composite = CompositeVideoClip(
            [visual_clip], size=(self.render_cfg.width, self.render_cfg.height)
        )
        composite = composite.set_duration(target_duration).set_audio(audio_clip)
        return composite

    def _build_opening_clip(
        self,
        run_dir: Path,
        scene: ScenePlan,
        thumbnail_title: str,
    ) -> CompositeVideoClip:
        background = ColorClip(
            size=(self.render_cfg.width, self.render_cfg.height),
            color=(0, 0, 0),
        ).set_duration(scene.duration)

        title_lines = scene.text_segments[0].lines if scene.text_segments else [thumbnail_title]
        overlay_path = self._create_center_text_image(run_dir, scene.scene_id, title_lines)
        overlay_clip = ImageClip(str(overlay_path)).set_duration(scene.duration).set_position("center")
        return CompositeVideoClip([background, overlay_clip], size=background.size).set_duration(scene.duration)

    def _build_content_clip(self, run_dir: Path, scene: ScenePlan) -> CompositeVideoClip:
        base_clip = self._load_base_image(scene)
        overlay_clips: List[ImageClip] = []

        for segment in scene.text_segments:
            overlay_path = self._create_text_overlay(run_dir, scene.scene_id, segment)
            overlay_clip = (
                ImageClip(str(overlay_path))
                .set_duration(segment.duration)
                .set_start(segment.start_offset)
                .set_position((0, "bottom"))
            )
            overlay_clips.append(overlay_clip)

        clips = [base_clip] + overlay_clips
        return CompositeVideoClip(clips, size=(self.render_cfg.width, self.render_cfg.height)).set_duration(scene.duration)

    def _load_base_image(self, scene: ScenePlan) -> ImageClip:
        duration = scene.duration
        if scene.image_path and scene.image_path.exists():
            clip = ImageClip(str(scene.image_path)).set_duration(duration)
            zoom = self.render_cfg.ken_burns_zoom
            offset = self.render_cfg.ken_burns_offset
            dir_x, dir_y = getattr(scene, "ken_burns_vector", (-1.0, -1.0))
            margin = getattr(scene, "ken_burns_margin", self.render_cfg.ken_burns_margin)
            margin = max(margin, 0.0)

            src_w, src_h = clip.size
            target_w = self.render_cfg.width
            target_h = self.render_cfg.height
            if src_w == 0 or src_h == 0:
                base_scale = 1.0
            else:
                base_scale = max(target_w / src_w, target_h / src_h)

            scale_factor = base_scale * (1.0 + margin)
            duration_safe = max(duration, 0.01)

            clip = clip.resize(
                lambda t: scale_factor * (1 + zoom * (t / duration_safe))
            )

            available_fraction_x = max((scale_factor * src_w) / target_w - 1.0, 0.0) / 2.0
            available_fraction_y = max((scale_factor * src_h) / target_h - 1.0, 0.0) / 2.0
            if zoom > 0:
                final_scale = scale_factor * (1 + zoom)
                available_fraction_x = max((final_scale * src_w) / target_w - 1.0, 0.0) / 2.0
                available_fraction_y = max((final_scale * src_h) / target_h - 1.0, 0.0) / 2.0

            if margin > 0:
                travel_ratio = min(offset / margin, 1.0)
                eff_offset_x = available_fraction_x * travel_ratio
                eff_offset_y = available_fraction_y * travel_ratio
            else:
                eff_offset_x = offset
                eff_offset_y = offset

            def _position_fn(t: float, *, _w=target_w, _h=target_h) -> Tuple[float, float]:
                progress = t / duration_safe
                return (
                    dir_x * _w * eff_offset_x * progress,
                    dir_y * _h * eff_offset_y * progress,
                )

            clip = clip.set_position(_position_fn)
            clip = clip.on_color(
                size=(target_w, target_h),
                color=(0, 0, 0),
                pos=("center", "center"),
            )
        else:
            logger.warning("Image missing for %s; using fallback background", scene.scene_id)
            clip = ColorClip(
                size=(self.render_cfg.width, self.render_cfg.height),
                color=(10, 10, 10),
            ).set_duration(duration)
        return clip

    # ------------------------------------------------------------------
    # Overlay helpers
    # ------------------------------------------------------------------

    def _create_text_overlay(
        self,
        run_dir: Path,
        scene_id: str,
        segment: TextSegmentPlan,
    ) -> Path:
        cache_key = (scene_id, segment.segment_index, tuple(segment.lines))
        if cache_key in self._overlay_cache:
            return self._overlay_cache[cache_key]

        font = self._get_font(self.render_cfg.body_font_size)
        multi_line = len(segment.lines) > 1
        line_spacing = int(font.size * (0.42 if multi_line else 0.25))

        text_sizes = [self._measure_text(font, line) for line in segment.lines]
        text_block_height = sum(size[1] for size in text_sizes)
        if multi_line:
            text_block_height += line_spacing * (len(segment.lines) - 1)

        outer_margin_top = max(int(font.size * 0.12), 6)
        outer_margin_bottom = max(int(font.size * 0.35), 18)
        inner_padding_top = max(int(font.size * 0.45), 20)
        inner_padding_bottom = max(int(font.size * 0.7), 28)

        band_height = (
            text_block_height
            + inner_padding_top
            + inner_padding_bottom
            + outer_margin_top
            + outer_margin_bottom
        )
        image = Image.new("RGBA", (self.render_cfg.width, band_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image, "RGBA")

        horizontal_margin = max(int(self.render_cfg.width * 0.018), 18)
        radius = max(int(font.size * 0.42), 18)
        rect_top = outer_margin_top
        rect_bottom = band_height - outer_margin_bottom
        rect = [
            (horizontal_margin, rect_top),
            (self.render_cfg.width - horizontal_margin, rect_bottom),
        ]
        draw.rounded_rectangle(rect, radius=radius, fill=self.render_cfg.band_color)

        inner_top = rect_top + inner_padding_top
        inner_bottom = rect_bottom - inner_padding_bottom
        available_inner = max(inner_bottom - inner_top, 0)
        y = inner_top + max((available_inner - text_block_height) // 2, 0)
        content_width = self.render_cfg.width - (horizontal_margin * 2)

        for idx, (line, (text_width, text_height)) in enumerate(zip(segment.lines, text_sizes)):
            x = horizontal_margin + max(int((content_width - text_width) / 2), 0)
            draw.text((x, y), line, font=font, fill=self.render_cfg.body_color)
            y += text_height
            if idx < len(segment.lines) - 1:
                y += line_spacing

        overlay_dir = run_dir / "overlays"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        output_path = overlay_dir / f"{scene_id}_seg{segment.segment_index:02d}.png"
        image.save(output_path, format="PNG")
        self._overlay_cache[cache_key] = output_path
        return output_path

    def _create_center_text_image(
        self,
        run_dir: Path,
        scene_id: str,
        lines: List[str],
    ) -> Path:
        cache_key = (scene_id, tuple(lines))
        if cache_key in self._opening_cache:
            return self._opening_cache[cache_key]

        image = Image.new("RGBA", (self.render_cfg.width, self.render_cfg.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        font = self._get_font(self.render_cfg.opening_title_font_size, bold=True)

        total_height = 0
        for line in lines:
            text_width, text_height = self._measure_text(font, line)
            total_height += text_height
        total_height += font.size * 0.6 * (len(lines) - 1)

        current_y = (self.render_cfg.height - total_height) / 2
        for line in lines:
            text_width, text_height = self._measure_text(font, line)
            draw.text(
                ((self.render_cfg.width - text_width) / 2, current_y),
                line,
                font=font,
                fill=(255, 255, 255),
            )
            current_y += text_height + font.size * 0.6

        overlay_dir = run_dir / "overlays"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        output_path = overlay_dir / f"{scene_id}_opening.png"
        image.save(output_path, format="PNG")
        self._opening_cache[cache_key] = output_path
        return output_path

    def _get_font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        cache_key = (size, bold)
        if cache_key in self._font_cache:
            return self._font_cache[cache_key]

        font_path = self.render_cfg.font_path
        try:
            if font_path and Path(font_path).exists():
                font = ImageFont.truetype(str(font_path), size=size)
            else:
                fallback_name = "NotoSansJP-ExtraBold.ttf" if bold else "NotoSansJP-Bold.ttf"
                fallback_path = Path("fonts") / fallback_name
                if fallback_path.exists():
                    font = ImageFont.truetype(str(fallback_path), size=size)
                else:
                    system_fallback = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
                    font = ImageFont.truetype(system_fallback, size=size)
        except OSError:
            font = ImageFont.load_default()
        self._font_cache[cache_key] = font
        return font

    def _measure_text(self, font: ImageFont.FreeTypeFont, text: str) -> Tuple[int, int]:
        try:
            bbox = font.getbbox(text)
            return bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            return font.getsize(text)


def _hex_to_rgb(value: str) -> Tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 6:
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    raise ValueError(f"Invalid RGB hex value: {value}")


def _hex_to_rgba(value: str) -> Tuple[int, int, int, int]:
    value = value.lstrip("#")
    if len(value) == 8:
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4, 6))  # type: ignore[return-value]
    if len(value) == 6:
        rgb = tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))
        return (*rgb, 200)
    raise ValueError(f"Invalid RGBA hex value: {value}")
