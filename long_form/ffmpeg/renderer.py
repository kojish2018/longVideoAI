from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from logging_utils import get_logger
from .runner import run_ffmpeg, run_ffmpeg_stream
from .progress import ConsoleBar
from .concat import concat_mp4_streamcopy

logger = get_logger(__name__)


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
    font_path: Optional[str]
    body_font_size: int
    body_color: Tuple[int, int, int]
    band_color: Tuple[int, int, int, int]
    opening_title_font_size: int


class FFmpegVideoGenerator:
    """FFmpeg-based renderer matching the MoviePy VideoGenerator interface."""

    def __init__(self, config: Dict[str, object]) -> None:
        video_cfg = config.get("video", {}) if isinstance(config, dict) else {}
        text_cfg = config.get("text", {}) if isinstance(config, dict) else {}
        animation_cfg = config.get("animation", {}) if isinstance(config, dict) else {}
        # FFmpeg-specific overrides (optional)
        ffmpeg_cfg = config.get("ffmpeg", {}) if isinstance(config, dict) else {}
        if isinstance(ffmpeg_cfg, dict):
            ff_anim = ffmpeg_cfg.get("animation", {})
            if isinstance(ff_anim, dict):
                # Override animation values if present under ffmpeg.animation
                animation_cfg = {**animation_cfg, **ff_anim}

        colors = text_cfg.get("colors", {}) if isinstance(text_cfg, dict) else {}

        opening_title_size = 75

        self.render_cfg = RenderConfig(
            width=int(video_cfg.get("width", 1280)),
            height=int(video_cfg.get("height", 720)),
            fps=int(video_cfg.get("fps", 30)),
            codec=str(video_cfg.get("codec", "libx264")),
            bitrate=str(video_cfg.get("bitrate")) if video_cfg.get("bitrate") else None,
            preset=str(video_cfg.get("preset", "ultrafast")),
            crf=int(video_cfg.get("crf", 20)) if video_cfg.get("crf") else 20,
            audio_codec=str(video_cfg.get("audio_codec", "aac")),
            audio_bitrate=str(video_cfg.get("audio_bitrate")) if video_cfg.get("audio_bitrate") else None,
            audio_sample_rate=int(video_cfg.get("audio_sample_rate", 48000)),
            padding_seconds=float(animation_cfg.get("padding_seconds", 0.35)),
            ken_burns_zoom=float(animation_cfg.get("ken_burns_zoom", 0.03)),
            # Interpreted as fraction of output size (MoviePy parity).
            ken_burns_offset=float(animation_cfg.get("ken_burns_offset", 0.01)),
            font_path=text_cfg.get("font_path"),
            body_font_size=int(text_cfg.get("default_size", 36)),
            body_color=_hex_to_rgb(colors.get("default", "#FFFFFF")),
            band_color=_hex_to_rgba(colors.get("background_box", "#000000F0")),
            opening_title_font_size=opening_title_size,
        )

        self._font_cache: Dict[Tuple[int, bool], ImageFont.FreeTypeFont] = {}
        self._overlay_cache: Dict[Tuple[str, int, Tuple[str, ...]], Path] = {}
        self._opening_cache: Dict[Tuple[str, Tuple[str, ...]], Path] = {}

    # Public API ---------------------------------------------------------
    def render(
        self,
        *,
        run_dir: Path,
        scenes: Iterable[object],
        output_path: Path,
        thumbnail_title: str,
    ) -> Path:
        cfg = self.render_cfg
        scene_dir = run_dir / "ffmpeg_scenes"
        scene_dir.mkdir(parents=True, exist_ok=True)

        rendered: List[Path] = []
        # Compute total program duration (final video length)
        total_duration = 0.0
        scene_list = list(scenes)
        for scene in scene_list:
            try:
                total_duration += float(getattr(scene, "duration", 0.0))
            except Exception:
                pass

        # Render scenes quietly (no bars), matching MoviePy which shows progress only at final write
        for scene in scene_list:
            if getattr(scene, "scene_type", "content") == "opening":
                path = self._render_opening_scene(run_dir, scene_dir, scene, thumbnail_title)
            else:
                path = self._render_content_scene(run_dir, scene_dir, scene)
            rendered.append(path)

        concat_path = run_dir / "temp_concat.mp4"
        concat_mp4_streamcopy(rendered, concat_path)

        # Final write: show a single progress bar like MoviePy
        final_path = output_path
        self._mix_bgm(concat_path, final_path, total_duration=total_duration)
        return final_path

    # Scene builders -----------------------------------------------------
    def _render_opening_scene(
        self,
        run_dir: Path,
        scene_dir: Path,
        scene: object,
        title: str,
        bar=None,
        offset_seconds: float = 0.0,
    ) -> Path:
        duration = max(0.01, float(getattr(scene, "duration", 3.0)))
        scene_id = str(getattr(scene, "scene_id", "OPENING"))
        out = scene_dir / f"{scene_id}.mp4"

        # Prepare overlay image (centered text)
        segs = list(getattr(scene, "text_segments", []))
        lines = list(getattr(segs[0], "lines", [])) if segs else [title]
        overlay = self._create_center_text_image(run_dir, scene_id, lines)

        args: List[str] = []
        cfg = self.render_cfg
        # Inputs: narration audio and overlay image
        narration_path = Path(getattr(scene, "narration_path"))
        args += [
            "-t",
            f"{duration:.3f}",
            "-f",
            "lavfi",
            "-r",
            str(cfg.fps),
            "-i",
            f"color=c=black:size={cfg.width}x{cfg.height}",
            # Loop overlay PNG to full duration to avoid early termination
            "-loop",
            "1",
            "-framerate",
            str(cfg.fps),
            "-t",
            f"{duration:.3f}",
            "-i",
            str(overlay),
            "-i",
            str(narration_path),
            "-filter_complex",
            _overlay_center_filter(cfg.width, cfg.height, cfg.fps),
            "-map",
            "[vout]",
            "-map",
            "2:a:0",
        ]
        args += _encode_args(cfg)
        args += ["-shortest", "-y", str(out)]
        if bar is None:
            run_ffmpeg(args)
        else:
            run_ffmpeg_stream(
                args,
                expected_duration_sec=duration,
                label="Opening",
                external_bar=bar,
                offset_seconds=offset_seconds,
            )
        return out

    def _render_content_scene(
        self,
        run_dir: Path,
        scene_dir: Path,
        scene: object,
        bar=None,
        offset_seconds: float = 0.0,
    ) -> Path:
        cfg = self.render_cfg
        duration = max(0.01, float(getattr(scene, "duration", 1.0)))
        scene_id = str(getattr(scene, "scene_id", "SXXX"))
        out = scene_dir / f"{scene_id}.mp4"

        image_path: Optional[Path] = getattr(scene, "image_path", None)
        if image_path is not None:
            image_path = Path(image_path)

        # Build inputs: base image (or color), overlays for each segment, narration audio
        inputs: List[str] = []
        if image_path and image_path.exists():
            # Use single-frame image input; zoompan will expand it to nframes
            inputs += ["-i", str(image_path)]
        else:
            # Fallback: provide a single-frame color input, zoompan will expand
            one_frame = 1.0 / max(cfg.fps, 1)
            inputs += [
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:size={cfg.width}x{cfg.height}:d={one_frame:.6f}:r={cfg.fps}",
            ]

        overlay_specs: List[Tuple[Path, float, float]] = []
        for seg in getattr(scene, "text_segments", []) or []:
            lines = [str(s) for s in getattr(seg, "lines", [])]
            if not any(line.strip() for line in lines):
                continue
            overlay = self._create_text_overlay(run_dir, scene_id, seg)
            start = float(getattr(seg, "start_offset", 0.0))
            dur = float(getattr(seg, "duration", 0.0))
            overlay_specs.append((overlay, start, dur))

        for overlay, _, _ in overlay_specs:
            inputs += [
                "-loop",
                "1",
                "-framerate",
                str(cfg.fps),
                "-t",
                f"{duration:.3f}",
                "-i",
                str(overlay),
            ]

        narration_path = Path(getattr(scene, "narration_path"))
        inputs += ["-i", str(narration_path)]

        # Build filter graph for base Ken Burns + bottom overlays
        filter_graph = _build_content_filter(
            has_base_image=bool(image_path and image_path.exists()),
            w=cfg.width,
            h=cfg.height,
            fps=cfg.fps,
            duration=duration,
            ken_zoom=cfg.ken_burns_zoom,
            ken_offset=cfg.ken_burns_offset,
            overlays=overlay_specs,
        )

        args: List[str] = inputs + [
            "-filter_complex",
            filter_graph,
            "-map",
            "[vout]",
            "-map",
            f"{len(overlay_specs)+1}:a:0",  # last input is narration audio
        ]
        args += _encode_args(cfg)
        args += ["-shortest", "-y", str(out)]
        if bar is None:
            run_ffmpeg(args)
        else:
            run_ffmpeg_stream(
                args,
                expected_duration_sec=duration,
                label=str(scene_id),
                external_bar=bar,
                offset_seconds=offset_seconds,
            )
        return out

    def _mix_bgm(self, input_video: Path, output_path: Path, *, total_duration: float) -> Path:
        cfg = self.render_cfg
        bgm_file = Path("background_music/Fulero.mp3")
        if not bgm_file.exists():
            # Fast path: just move/copy streams with faststart
            args = ["-i", str(input_video), "-c", "copy", "-movflags", "+faststart", "-y", str(output_path)]
            # Show a single bar for the final write (MoviePy-like)
            run_ffmpeg_stream(args, expected_duration_sec=total_duration, label="Render")
            return output_path

        # Loop BGM, fade in/out, mix with narration audio (stereo), keep video stream
        sr = str(cfg.audio_sample_rate)
        fade_out_st = max(total_duration - 1.0, 0.0)
        logger.info(
            "BGM mix: file=%s, total=%.2fs, fade_out_at=%.2fs, bgm_gain=%.2f, stereo=%s",
            bgm_file,
            total_duration,
            fade_out_st,
            0.10,
            "on",
        )
        filter_complex = (
            # Prepare BGM: low level, gentle fade, correct fade-out timing, force stereo @ sample rate
            f"[1:a]atrim=0:duration={total_duration:.3f},asetpts=PTS-STARTPTS,"
            f"volume=0.10,afade=t=in:st=0:d=0.5,afade=t=out:st={fade_out_st:.3f}:d=1.0,"
            f"aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo[bgm];"
            # Prepare narration: force stereo @ sample rate
            f"[0:a]aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo[narr];"
            # Mix 2 inputs, duration=first keeps final length tied to video/narration
            f"[narr][bgm]amix=inputs=2:duration=first:dropout_transition=2[a];"
            # Loudness normalization to -14 LUFS with -1.5 dBTP headroom
            f"[a]loudnorm=I=-14:LRA=7:TP=-1.5,"
            f"aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo[aout]"
        )
        args: List[str] = [
            "-i",
            str(input_video),
            "-stream_loop",
            "-1",
            "-i",
            str(bgm_file),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            cfg.audio_codec,
            "-ar",
            str(cfg.audio_sample_rate),
            "-ac",
            "2",
        ]
        if cfg.audio_bitrate:
            args += ["-b:a", str(cfg.audio_bitrate)]
        args += ["-movflags", "+faststart", "-shortest", "-y", str(output_path)]
        # Display a single overall bar on the final write step (MoviePy-like)
        run_ffmpeg_stream(args, expected_duration_sec=total_duration, label="Render")
        return output_path

    # Overlay image helpers ---------------------------------------------
    def _get_font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        key = (size, bold)
        if key in self._font_cache:
            return self._font_cache[key]

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
        self._font_cache[key] = font
        return font

    def _measure_text(self, font: ImageFont.FreeTypeFont, text: str) -> Tuple[int, int]:
        try:
            bbox = font.getbbox(text)
            return bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            return font.getsize(text)

    def _create_text_overlay(self, run_dir: Path, scene_id: str, segment: object) -> Path:
        lines: List[str] = [str(s) for s in getattr(segment, "lines", [])]
        cache_key = (scene_id, int(getattr(segment, "segment_index", 0)), tuple(lines))
        if cache_key in self._overlay_cache:
            return self._overlay_cache[cache_key]

        font = self._get_font(self.render_cfg.body_font_size)
        multi_line = len(lines) > 1
        line_spacing = int(font.size * (0.42 if multi_line else 0.25))

        text_sizes = [self._measure_text(font, line) for line in lines]
        text_block_height = sum(size[1] for size in text_sizes)
        if multi_line:
            text_block_height += line_spacing * (len(lines) - 1)

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

        for idx, (line, (text_width, text_height)) in enumerate(zip(lines, text_sizes)):
            x = horizontal_margin + max(int((content_width - text_width) / 2), 0)
            draw.text((x, y), line, font=font, fill=self.render_cfg.body_color)
            y += text_height
            if idx < len(lines) - 1:
                y += line_spacing

        overlay_dir = run_dir / "overlays"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        seg_index = int(getattr(segment, "segment_index", 0))
        output_path = overlay_dir / f"{scene_id}_seg{seg_index:02d}.png"
        image.save(output_path, format="PNG")
        self._overlay_cache[cache_key] = output_path
        return output_path

    def _create_center_text_image(self, run_dir: Path, scene_id: str, lines: List[str]) -> Path:
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
        total_height += int(font.size * 0.6) * max(len(lines) - 1, 0)

        current_y = (self.render_cfg.height - total_height) / 2
        for line in lines:
            text_width, text_height = self._measure_text(font, line)
            draw.text(
                ((self.render_cfg.width - text_width) / 2, current_y),
                line,
                font=font,
                fill=(255, 255, 255),
            )
            current_y += text_height + int(font.size * 0.6)

        overlay_dir = run_dir / "overlays"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        output_path = overlay_dir / f"{scene_id}_opening.png"
        image.save(output_path, format="PNG")
        self._opening_cache[cache_key] = output_path
        return output_path


# ------------------------------ helpers --------------------------------
def _encode_args(cfg: RenderConfig) -> List[str]:
    args: List[str] = [
        "-r",
        str(cfg.fps),
        "-c:v",
        cfg.codec,
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-level:v",
        "4.1",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-movflags",
        "+faststart",
        "-c:a",
        cfg.audio_codec,
        "-ar",
        str(cfg.audio_sample_rate),
    ]
    if cfg.crf is not None:
        args += ["-crf", str(cfg.crf)]
    if cfg.bitrate:
        args += ["-b:v", str(cfg.bitrate)]
    if cfg.preset:
        args += ["-preset", str(cfg.preset)]
    if cfg.audio_bitrate:
        args += ["-b:a", str(cfg.audio_bitrate)]
    return args


def _overlay_center_filter(w: int, h: int, fps: int) -> str:
    # No shortest=1; base stream duration (-t) governs output length
    return (
        f"[0:v][1:v]overlay=x=(W-w)/2:y=(H-h)/2:eval=init:format=auto,"
        f"fps={fps},format=yuv420p[vout]".replace("W", str(w)).replace("H", str(h))
    )


def _build_content_filter(
    *,
    has_base_image: bool,
    w: int,
    h: int,
    fps: int,
    duration: float,
    ken_zoom: float,
    ken_offset: float,
    overlays: List[Tuple[Path, float, float]],
) -> str:
    """Return a filter_complex string for base Ken Burns and timed overlays.

    - If `has_base_image` is True, apply zoompan to the image; otherwise assume a
      color source already sized w x h is provided.
    - Overlays are placed at bottom with enable between(t,start,end).
    """
    chains: List[str] = []

    if has_base_image:
        # idx 0 is the (single-frame) image input; expand to nframes with zoompan
        zmax = 1.0 + max(ken_zoom, 0.0)
        nframes = max(int(round(duration * fps)), 1)
        step = (zmax - 1.0) / nframes
        # pzoom ensures cumulative zoom progresses across frames
        zoom = f"min(max(zoom,pzoom)+{step:.7f},{zmax:.6f})"
        progress = f"(on/{nframes})"
        offset = max(ken_offset, 0.0)
        x = "iw/2-(iw/zoom/2) - (iw/zoom) * " + f"{offset:.6f}*{progress}"
        y = "ih/2-(ih/zoom/2) - (ih/zoom) * " + f"{offset:.6f}*{progress}"
        chains.append(
            f"[0:v]zoompan=z='{zoom}':x='{x}':y='{y}':d={nframes}:s={w}x{h}:fps={fps}[base]"
        )
        last = "[base]"
        next_input_index = 1
    else:
        # idx 0 is a color video already at w x h
        last = "[0:v]"
        next_input_index = 1

    # Timed overlays
    for i, (_overlay, start, dur) in enumerate(overlays, start=0):
        end = start + max(dur, 0.0)
        idx = next_input_index + i
        label = f"[v{i}]"
        enable = f"between(t,{start:.3f},{end:.3f})"
        chains.append(
            f"{last}[{idx}:v]overlay=x=0:y=H-h:enable='{enable}'{label}".replace("H", str(h))
        )
        last = label

    chains.append(f"{last}format=yuv420p[vout]")
    return ";".join(chains)


def _hex_to_rgb(value: str) -> Tuple[int, int, int]:
    v = value.lstrip("#")
    if len(v) == 6:
        return tuple(int(v[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    raise ValueError(f"Invalid RGB hex value: {value}")


def _hex_to_rgba(value: str) -> Tuple[int, int, int, int]:
    v = value.lstrip("#")
    if len(v) == 8:
        return tuple(int(v[i : i + 2], 16) for i in (0, 2, 4, 6))  # type: ignore[return-value]
    if len(v) == 6:
        rgb = tuple(int(v[i : i + 2], 16) for i in (0, 2, 4))
        return (*rgb, 200)
    raise ValueError(f"Invalid RGBA hex value: {value}")
