"""FFmpeg-based renderer for shashin_mode."""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from logging_utils import get_logger

from .config import LayoutConfig
from .overlay_factory import SubtitleOverlayFactory
from .renderer import RenderChunk

logger = get_logger(__name__)


@dataclass
class FFmpegRenderOptions:
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    audio_sample_rate: int = 48000
    preset: str = "medium"
    crf: int = 20
    threads: int = 4
    pix_fmt: str = "yuv420p"
    extra_video_flags: Optional[List[str]] = None


class FFmpegShashinRenderer:
    """Render chunked Shashin outputs by delegating composition to FFmpeg."""

    def __init__(
        self,
        layout: LayoutConfig,
        *,
        overlay_dir: Optional[Path] = None,
        ffmpeg_path: str = "ffmpeg",
        options: Optional[Dict[str, object]] = None,
    ) -> None:
        self.layout = layout
        self.overlay_dir = overlay_dir or Path("shashin_mode/cache_overlays")
        self.overlay_factory = SubtitleOverlayFactory(layout, self.overlay_dir)
        self.ffmpeg_path = ffmpeg_path
        self.render_opts = self._build_render_options(options or {})

    def render(
        self,
        *,
        background: Path,
        chunks: List[RenderChunk],
        output_path: Path,
        temp_dir: Path,
    ) -> Path:
        temp_dir.mkdir(parents=True, exist_ok=True)
        chunk_files: List[Path] = []
        for chunk in chunks:
            overlay_path, overlay_height = self.overlay_factory.create_overlay(chunk.chunk, chunk.duration)
            chunk_file = temp_dir / f"chunk_{chunk.chunk.index:03d}.mp4"
            self._render_chunk(
                background=background,
                render_chunk=chunk,
                overlay_path=overlay_path,
                overlay_height=overlay_height,
                output_path=chunk_file,
            )
            chunk_files.append(chunk_file.resolve())

        if not chunk_files:
            raise RuntimeError("No chunks rendered; nothing to concatenate")

        concat_file = temp_dir / "chunks.txt"
        with concat_file.open("w", encoding="utf-8") as fh:
            for file_path in chunk_files:
                fh.write(f"file '{file_path.as_posix()}'\n")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        concat_cmd = [
            self.ffmpeg_path,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        self._run_ffmpeg(concat_cmd, desc="concat chunks")
        return output_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _render_chunk(
        self,
        *,
        background: Path,
        render_chunk: RenderChunk,
        overlay_path: Path,
        overlay_height: int,
        output_path: Path,
    ) -> None:
        duration = max(render_chunk.duration, 0.01)
        has_center_image = render_chunk.image_path is not None and render_chunk.image_path.exists()

        cmd: List[str] = [self.ffmpeg_path, "-y", "-stream_loop", "-1", "-i", str(background)]
        input_index = 1
        image_idx = None
        if has_center_image:
            cmd += ["-loop", "1", "-i", str(render_chunk.image_path)]
            image_idx = input_index
            input_index += 1

        cmd += ["-loop", "1", "-i", str(overlay_path)]
        overlay_idx = input_index
        input_index += 1

        cmd += ["-i", str(render_chunk.audio_path)]
        audio_idx = input_index

        filter_graph = self._build_filter_graph(
            duration=duration,
            background_idx=0,
            image_idx=image_idx,
            overlay_idx=overlay_idx,
            overlay_height=overlay_height,
        )

        encode_flags = self._build_encode_flags()
        full_cmd = cmd + [
            "-filter_complex",
            filter_graph,
            "-map",
            "[vout]",
            "-map",
            f"{audio_idx}:a:0",
            *encode_flags,
            "-shortest",
            str(output_path),
        ]
        self._run_ffmpeg(full_cmd, desc=f"chunk {render_chunk.chunk.index:03d}")

    def _build_filter_graph(
        self,
        *,
        duration: float,
        background_idx: int,
        image_idx: Optional[int],
        overlay_idx: int,
        overlay_height: int,
    ) -> str:
        width = self.layout.width
        height = self.layout.height
        fps = self.layout.fps
        filters = [
            f"[{background_idx}:v]scale={width}:{height},setsar=1,fps={fps},trim=0:{duration:.6f},setpts=PTS-STARTPTS[bg]",
        ]
        base_label = "[bg]"
        if image_idx is not None:
            target_width = int(self.layout.width * self.layout.image_width_ratio)
            filters.append(f"[{image_idx}:v]scale={target_width}:-1,setsar=1[img]")
            filters.append(
                f"{base_label}[img]overlay=x='(main_w-overlay_w)/2':y={self.layout.image_top_padding_px}:format=auto[bgimg]"
            )
            base_label = "[bgimg]"

        filters.append(
            f"{base_label}[{overlay_idx}:v]overlay=x=0:y={height - overlay_height}:format=auto[vout]"
        )
        return ";".join(filters)

    def _build_render_options(self, overrides: Dict[str, object]) -> FFmpegRenderOptions:
        opts = FFmpegRenderOptions()
        for field_name in ("video_codec", "audio_codec", "audio_bitrate", "audio_sample_rate", "preset", "crf", "threads", "pix_fmt"):
            if field_name in overrides:
                setattr(opts, field_name, overrides[field_name])
        extra_flags = overrides.get("extra_video_flags")
        if isinstance(extra_flags, list):
            opts.extra_video_flags = [str(flag) for flag in extra_flags]
        return opts

    def _build_encode_flags(self) -> List[str]:
        opts = self.render_opts
        flags = [
            "-c:v",
            opts.video_codec,
            "-preset",
            str(opts.preset),
            "-crf",
            str(opts.crf),
            "-pix_fmt",
            opts.pix_fmt,
            "-c:a",
            opts.audio_codec,
            "-b:a",
            str(opts.audio_bitrate),
            "-ar",
            str(opts.audio_sample_rate),
            "-threads",
            str(opts.threads),
        ]
        if opts.extra_video_flags:
            flags.extend(opts.extra_video_flags)
        flags += ["-movflags", "+faststart"]
        return flags

    def _run_ffmpeg(self, cmd: List[str], *, desc: str) -> None:
        logger.info("FFmpeg (%s): %s", desc, shlex.join(cmd))
        completed = subprocess.run(cmd, capture_output=True, text=True)
        if completed.returncode != 0:
            logger.error("FFmpeg (%s) failed:\n%s\n%s", desc, completed.stdout, completed.stderr)
            raise RuntimeError(f"FFmpeg command failed for {desc}")
