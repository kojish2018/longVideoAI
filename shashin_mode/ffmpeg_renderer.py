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
        bgm_directory: str = "background_music",
        bgm_selected: str = "Everet.mp3",
    ) -> None:
        self.layout = layout
        self.overlay_dir = overlay_dir or Path("shashin_mode/cache_overlays")
        self.overlay_factory = SubtitleOverlayFactory(layout, self.overlay_dir)
        self.ffmpeg_path = ffmpeg_path
        self.render_opts = self._build_render_options(options or {})
        self._bgm_directory = bgm_directory
        self._bgm_selected = bgm_selected

    def render(
        self,
        *,
        background: Path,
        chunks: List[RenderChunk],
        output_path: Path,
        temp_dir: Path,
    ) -> Path:
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # 背景動画の長さを1回だけ取得
        bg_duration = self._get_background_duration(background)
        
        chunk_files: List[Path] = []
        for chunk in chunks:
            overlay_path, overlay_height = self.overlay_factory.create_overlay(chunk.chunk, chunk.duration)
            chunk_file = temp_dir / f"chunk_{chunk.chunk.index:03d}.mp4"
            self._render_chunk(
                background=background,
                render_chunk=chunk,
                overlay_path=overlay_path,
                overlay_height=overlay_height,
                bg_duration=bg_duration,
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
        
        # 連結動画を一時ファイルに出力
        temp_concat = temp_dir / "concat_temp.mp4"
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
            str(temp_concat),
        ]
        self._run_ffmpeg(concat_cmd, desc="concat chunks")
        
        # BGMミキシングを実行
        total_duration = sum(chunk.duration for chunk in chunks)
        final_output = self._mix_bgm(temp_concat, output_path, total_duration=total_duration)
        
        # 一時ファイルをクリーンアップ
        try:
            temp_concat.unlink(missing_ok=True)
        except Exception:
            pass
        
        return final_output

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_background_duration(self, background: Path) -> float:
        """背景動画の長さを取得"""
        try:
            ffprobe_path = self.ffmpeg_path.replace("ffmpeg", "ffprobe")
            if not Path(ffprobe_path).exists():
                ffprobe_path = "ffprobe"
            
            result = subprocess.run(
                [
                    ffprobe_path,
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(background),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            duration = float(result.stdout.strip())
            logger.info("Background video duration: %.2f seconds", duration)
            return duration
        except Exception as e:
            logger.warning("Failed to get background duration, assuming 10 seconds: %s", e)
            return 10.0

    def _render_chunk(
        self,
        *,
        background: Path,
        render_chunk: RenderChunk,
        overlay_path: Path,
        overlay_height: int,
        bg_duration: float,
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
            start_offset=render_chunk.start,
            bg_duration=bg_duration,
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
        start_offset: float,
        bg_duration: float,
        background_idx: int,
        image_idx: Optional[int],
        overlay_idx: int,
        overlay_height: int,
    ) -> str:
        width = self.layout.width
        height = self.layout.height
        fps = self.layout.fps
        
        # 背景動画の長さで割った余りを使う → 修正前と同じ速度！
        actual_offset = start_offset % bg_duration if bg_duration > 0 else 0.0
        
        filters = [
            f"[{background_idx}:v]scale={width}:{height},setsar=1,fps={fps},trim={actual_offset:.6f}:{actual_offset + duration:.6f},setpts=PTS-STARTPTS[bg]",
        ]
        base_label = "[bg]"
        if image_idx is not None:
            # 高さを全体の80%に設定し、アスペクト比を維持（幅は-1で自動計算）
            target_height = int(height * 0.8)
            filters.append(f"[{image_idx}:v]scale=-1:{target_height},setsar=1[img]")
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

    def _mix_bgm(self, input_video: Path, output_path: Path, *, total_duration: float) -> Path:
        """Mix background music with narration audio using the same logic as long_form/ffmpeg/renderer.py"""
        bgm_path = self._resolve_bgm_path()
        if bgm_path is None or not bgm_path.exists():
            # Fast path: just move/copy streams with faststart
            output_path.parent.mkdir(parents=True, exist_ok=True)
            args = [
                self.ffmpeg_path,
                "-i",
                str(input_video),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-y",
                str(output_path),
            ]
            self._run_ffmpeg(args, desc="passthrough (no BGM)")
            return output_path

        # Loop BGM, fade in/out, mix with narration audio (stereo), keep video stream
        sr = str(self.render_opts.audio_sample_rate)
        fade_out_st = max(total_duration - 1.0, 0.0)
        logger.info(
            "BGM mix: file=%s, total=%.2fs, fade_out_at=%.2fs, bgm_gain=%.2f, stereo=%s",
            bgm_path,
            total_duration,
            fade_out_st,
            0.24,
            "on",
        )
        filter_complex = (
            # Prepare BGM: EBU R128 normalize first, then reduce level, fade, and format
            f"[1:a]atrim=0:duration={total_duration:.3f},asetpts=PTS-STARTPTS,"
            f"loudnorm=I=-30:LRA=7:TP=-2,"
            f"volume=0.24,afade=t=in:st=0:d=0.5,afade=t=out:st={fade_out_st:.3f}:d=1.0,"
            f"aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo[bgm];"
            # Prepare narration: force stereo @ sample rate
            f"[0:a]aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo[narr];"
            # Mix 2 inputs, duration=first keeps final length tied to video/narration
            f"[narr][bgm]amix=inputs=2:duration=first:dropout_transition=2[a];"
            # Final loudness normalization for the whole program
            f"[a]loudnorm=I=-14:LRA=7:TP=-1.5,"
            f"aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo[aout]"
        )
        args: List[str] = [
            self.ffmpeg_path,
            "-i",
            str(input_video),
            "-stream_loop",
            "-1",
            "-i",
            str(bgm_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            self.render_opts.audio_codec,
            "-ar",
            sr,
            "-ac",
            "2",
        ]
        if self.render_opts.audio_bitrate:
            args += ["-b:a", str(self.render_opts.audio_bitrate)]
        args += ["-movflags", "+faststart", "-shortest", "-y", str(output_path)]
        self._run_ffmpeg(args, desc="mix BGM")
        return output_path

    def _resolve_bgm_path(self) -> Optional[Path]:
        """Resolve BGM file path, similar to long_form/ffmpeg/renderer.py"""
        if not getattr(self, "_bgm_selected", None):
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
        logger.warning(
            "BGM file not found; falling back to passthrough. selection=%s directory=%s",
            self._bgm_selected,
            self._bgm_directory,
        )
        return None

    def _run_ffmpeg(self, cmd: List[str], *, desc: str) -> None:
        logger.info("FFmpeg (%s): %s", desc, shlex.join(cmd))
        completed = subprocess.run(cmd, capture_output=True, text=True)
        if completed.returncode != 0:
            logger.error("FFmpeg (%s) failed:\n%s\n%s", desc, completed.stdout, completed.stderr)
            raise RuntimeError(f"FFmpeg command failed for {desc}")
