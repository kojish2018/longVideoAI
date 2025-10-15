"""Renderer factory for switching between MoviePy and FFmpeg implementations.

This keeps the existing MoviePy-based `video_generator.VideoGenerator` as the
default, while allowing `config['renderer'] = 'ffmpeg'` to select the FFmpeg
implementation located under `long_form/ffmpeg/`.
"""
from __future__ import annotations

from typing import Any, Dict


def make_renderer(config: Dict[str, Any]):
    renderer_name = str(config.get("renderer", "moviepy")).lower()
    if renderer_name == "ffmpeg":
        from long_form.ffmpeg.renderer import FFmpegVideoGenerator  # lazy import

        return FFmpegVideoGenerator(config)

    # Fallback to existing MoviePy implementation
    from video_generator import VideoGenerator  # type: ignore

    return VideoGenerator(config)

