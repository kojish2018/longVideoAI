"""Renderer for shashin_mode (background GIF + center image + subtitles)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from moviepy.audio.AudioClip import AudioClip, CompositeAudioClip
from moviepy.audio.io.AudioFileClip import AudioFileClip
from moviepy.editor import CompositeVideoClip, ImageClip, VideoFileClip, concatenate_videoclips

from logging_utils import get_logger
from .config import LayoutConfig
from .overlay_factory import SubtitleOverlayFactory
from .script_loader import SubtitleChunk

logger = get_logger(__name__)


@dataclass
class RenderChunk:
    chunk: SubtitleChunk
    audio_path: Path
    image_path: Optional[Path]
    duration: float
    start: float


class ShashinRenderer:
    def __init__(
        self,
        layout: LayoutConfig,
        *,
        overlay_dir: Optional[Path] = None,
        write_options: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.layout = layout
        self.overlay_dir = overlay_dir or Path("shashin_mode/cache_overlays")
        self.overlay_factory = SubtitleOverlayFactory(layout, self.overlay_dir)
        self.write_options = dict(write_options or {})

    def render(self, *, background: Path, chunks: List[RenderChunk], output_path: Path, temp_dir: Path) -> Path:
        clips: List[CompositeVideoClip] = []
        final_clip: Optional[CompositeVideoClip] = None
        try:
            for chunk in chunks:
                clip = self._build_chunk_clip(background, chunk)
                clips.append(clip)

            final_clip = concatenate_videoclips(clips, method="compose")
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_audio = temp_dir / "temp_audio.m4a"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            write_kwargs: Dict[str, Any] = {
                "fps": self.layout.fps,
                "codec": "libx264",
                "audio_codec": "aac",
                "audio_fps": 48000,
                "audio_bitrate": "192k",
                "preset": "medium",
                "ffmpeg_params": ["-crf", "20"],
                "temp_audiofile": str(temp_audio),
                "remove_temp": True,
                "threads": 4,
                "verbose": True,
            }
            write_kwargs.update(self.write_options)
            final_clip.write_videofile(str(output_path), **write_kwargs)
            return output_path
        finally:
            for clip in clips:
                try:
                    clip.close()
                except Exception:
                    pass
            if final_clip:
                try:
                    final_clip.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------ #
    # Builders
    # ------------------------------------------------------------------ #

    def _build_chunk_clip(self, background: Path, render_chunk: RenderChunk) -> CompositeVideoClip:
        duration = max(render_chunk.duration, 0.01)
        base_clip = self._load_background(background, duration)
        overlays = []

        if render_chunk.image_path and render_chunk.image_path.exists():
            image_clip = self._prepare_image_clip(render_chunk.image_path, duration)
            overlays.append(image_clip)
        else:
            logger.warning("Missing image for chunk %s; skipping center image", render_chunk.chunk.index)

        overlay_path, overlay_height = self.overlay_factory.create_overlay(render_chunk.chunk, duration)
        subtitle_clip = (
            ImageClip(str(overlay_path))
            .set_duration(duration)
            .set_position(("center", self.layout.height - overlay_height))
        )
        overlays.append(subtitle_clip)

        audio_clip = self._prepare_audio_clip(render_chunk.audio_path, duration)

        composite = CompositeVideoClip(
            [base_clip, *overlays],
            size=(self.layout.width, self.layout.height),
        ).set_duration(duration)
        composite = composite.set_audio(audio_clip)
        return composite

    def _load_background(self, path: Path, duration: float) -> VideoFileClip:
        clip = VideoFileClip(str(path)).resize((self.layout.width, self.layout.height)).loop(duration=duration)
        return clip.set_duration(duration)

    def _prepare_image_clip(self, image_path: Path, duration: float) -> ImageClip:
        target_width = int(self.layout.width * self.layout.image_width_ratio)
        clip = ImageClip(str(image_path)).set_duration(duration)
        clip = clip.resize(width=target_width)
        clip = clip.set_position(("center", self.layout.image_top_padding_px))
        return clip

    def _prepare_audio_clip(self, audio_path: Path, duration: float):
        audio_clip = AudioFileClip(str(audio_path))
        actual = audio_clip.duration or duration
        target = max(duration, 0.01)

        # If the VOICEVOX output is shorter than intended (rare but observed),
        # pad the tail with silence so moviepy does not seek past the stream.
        if actual + 0.01 < target:
            pad = target - actual
            silence = AudioClip(lambda t: [0] * audio_clip.nchannels, duration=pad, fps=audio_clip.fps)
            audio_clip = CompositeAudioClip([audio_clip, silence.set_start(actual)]).set_duration(target)
        else:
            audio_clip = audio_clip.subclip(0, target)
        return audio_clip
