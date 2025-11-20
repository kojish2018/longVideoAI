"""Lightweight MoviePy renderer for yukkuri dialogue videos."""
from __future__ import annotations

import logging
import math
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import moviepy.audio.fx.all as afx
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy.audio.AudioClip import AudioArrayClip
from moviepy.audio.io.AudioFileClip import AudioFileClip
from moviepy.editor import ColorClip, CompositeAudioClip, CompositeVideoClip, ImageClip, concatenate_videoclips

from .styles import CharacterSpec, YukkuriStyle
from .timeline_builder import ShotPlan

logger = logging.getLogger(__name__)


@dataclass
class RenderUnit:
    plan: ShotPlan
    audio_path: Path
    audio_duration: float
    duration: float
    start: float
    bgm_path: Optional[Path]


class VideoRenderer:
    """Compose backgrounds, character sprites, captions, and audio."""

    def __init__(self, style: YukkuriStyle, config: Dict[str, object]) -> None:
        self.style = style
        self.config = config
        self.font_cache: Dict[Tuple[Path, int], ImageFont.FreeTypeFont] = {}
        bgm_cfg = config.get("bgm", {}) if isinstance(config, dict) else {}
        self.default_bgm = bgm_cfg.get("file")
        self.bgm_volume = float(bgm_cfg.get("volume", 0.12) or 0.12)
        self.bgm_fade_in = float(bgm_cfg.get("fade_in", 0.5) or 0.5)
        self.bgm_fade_out = float(bgm_cfg.get("fade_out", 1.0) or 1.0)

    def render(self, units: Sequence[RenderUnit], *, output_path: Path) -> Path:
        output_path = output_path.resolve()
        run_dir = output_path.parent
        run_dir.mkdir(parents=True, exist_ok=True)

        clips: List[CompositeVideoClip] = []
        final_clip: Optional[CompositeVideoClip] = None

        try:
            for unit in units:
                clip = self._build_clip(unit, cache_dir=run_dir)
                clips.append(clip)

            if not clips:
                raise RuntimeError("No clips to render")

            final_clip = concatenate_videoclips(clips, method="compose")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temp_audio = run_dir / "temp_audio.m4a"

            final_clip.write_videofile(
                str(output_path),
                fps=self.style.layout.fps,
                codec=self.config.get("video", {}).get("codec", "libx264") if isinstance(self.config.get("video", {}), dict) else "libx264",
                audio_codec=self.config.get("video", {}).get("audio_codec", "aac") if isinstance(self.config.get("video", {}), dict) else "aac",
                audio_bitrate=self.config.get("video", {}).get("audio_bitrate") if isinstance(self.config.get("video", {}), dict) else None,
                bitrate=self.config.get("video", {}).get("bitrate") if isinstance(self.config.get("video", {}), dict) else None,
                preset=self.config.get("video", {}).get("preset", "medium") if isinstance(self.config.get("video", {}), dict) else "medium",
                ffmpeg_params=["-crf", str(self.config.get("video", {}).get("crf", 18) if isinstance(self.config.get("video", {}), dict) else 18)],
                temp_audiofile=str(temp_audio),
                remove_temp=True,
                threads=4,
                verbose=False,
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

    def _build_clip(self, unit: RenderUnit, *, cache_dir: Path) -> CompositeVideoClip:
        plan = unit.plan
        duration = max(unit.duration, 0.01)

        background = self._background_clip(plan, duration)
        characters = self._character_clips(plan, duration)
        band = self._text_band(plan, cache_dir, duration)
        overlays = characters + [band]
        composite = CompositeVideoClip(
            [background] + overlays,
            size=(self.style.layout.width, self.style.layout.height),
        ).set_duration(duration)

        audio_clip = self._build_audio(unit, duration)
        composite = composite.set_audio(audio_clip)
        return composite

    def _background_clip(self, plan: ShotPlan, duration: float) -> CompositeVideoClip:
        layout = self.style.layout
        if plan.bg_image and plan.bg_image.exists():
            clip = ImageClip(str(plan.bg_image)).set_duration(duration)
            kb = layout.ken_burns
            margin = max(kb.margin, 0.0)
            zoom = kb.zoom
            travel = kb.travel
            if margin > 0 or zoom > 0:
                w, h = clip.size
                scale_base = max(layout.width / w, layout.height / h) * (1.0 + margin)
                duration_safe = max(duration, 0.01)
                clip = clip.resize(lambda t: scale_base * (1 + zoom * (t / duration_safe)))

                def _pos(t: float, *, _w=layout.width, _h=layout.height) -> Tuple[float, float]:
                    progress = t / duration_safe
                    return (
                        -_w * travel * progress * 0.5,
                        -_h * travel * progress * 0.5,
                    )

                clip = clip.set_position(_pos)
                clip = clip.on_color(size=(layout.width, layout.height), color=(0, 0, 0))
            else:
                clip = clip.resize(height=layout.height).on_color(size=(layout.width, layout.height), color=(0, 0, 0))
            return clip

        logger.warning("Background missing for shot %s; using fallback color", plan.index)
        return ColorClip(
            size=(layout.width, layout.height),
            color=self.style.layout.fallback_bg_color,
        ).set_duration(duration)

    def _character_clips(self, plan: ShotPlan, duration: float) -> List[ImageClip]:
        clips: List[ImageClip] = []
        spec = self.style.characters.get(plan.speaker_key)
        if spec:
            clips.append(self._character_clip(spec, duration))
        else:
            # Default: show both left/right ghosts if speaker unknown
            for candidate in self.style.characters.values():
                clips.append(self._character_clip(candidate, duration, opacity=0.35))
        return clips

    def _character_clip(self, spec: CharacterSpec, duration: float, opacity: float = 1.0) -> ImageClip:
        layout = self.style.layout
        if spec.sprite_path and spec.sprite_path.exists():
            clip = ImageClip(str(spec.sprite_path)).set_duration(duration)
        else:
            size = int(layout.height * 0.32)
            image = Image.new("RGBA", (size, size), (*spec.color, int(220 * opacity)))
            draw = ImageDraw.Draw(image)
            draw.ellipse((0, 0, size, size), fill=(*spec.color, int(240 * opacity)), outline=spec.stroke_color, width=int(6 * opacity))
            text = spec.display_name[:2]
            font = self._font(self.style.text.font_path_bold, int(size * 0.22))
            if hasattr(draw, "textbbox"):
                bbox = draw.textbbox((0, 0), text, font=font)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            else:
                bbox = font.getbbox(text)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text(((size - tw) / 2, (size - th) / 2), text, fill=spec.stroke_color, font=font)
            cache_dir = Path("temp") / "yukkuri_mode"
            cache_dir.mkdir(parents=True, exist_ok=True)
            sprite_path = cache_dir / f"placeholder_{spec.key}.png"
            image.save(sprite_path, "PNG")
            clip = ImageClip(str(sprite_path)).set_duration(duration)

        target_height = int(layout.height * spec.scale)
        if target_height > 0:
            clip = clip.resize(height=target_height)
        x = {
            "left": layout.width * 0.05,
            "right": layout.width * 0.65,
            "center": layout.width * 0.35,
        }.get(spec.anchor, layout.width * 0.05)
        y = layout.height - clip.h + spec.y_offset
        clip = clip.set_position((x, y)).set_opacity(opacity)
        return clip

    def _text_band(self, plan: ShotPlan, cache_dir: Path, duration: float) -> ImageClip:
        cache_dir.mkdir(parents=True, exist_ok=True)
        image_path = cache_dir / f"band_{plan.index:03d}.png"
        if not image_path.exists():
            image = self._render_band_image(plan, image_path)
            image.save(image_path, "PNG")
        with Image.open(image_path) as loaded:
            band_height = loaded.height
        return (
            ImageClip(str(image_path))
            .set_duration(duration)
            .set_position(("center", self.style.layout.height - band_height))
        )

    def _render_band_image(self, plan: ShotPlan, out_path: Path) -> Image.Image:
        text_style = self.style.text
        layout = self.style.layout
        lines = _wrap_by_chars(plan.text, text_style.wrap_chars)
        font = self._font(text_style.font_path_bold, text_style.size)
        line_spacing = max(int(font.size * 0.16), 8)

        text_sizes = [font.getbbox(line) for line in lines]
        text_heights = [bbox[3] - bbox[1] for bbox in text_sizes]
        text_widths = [bbox[2] - bbox[0] for bbox in text_sizes]
        text_block_height = sum(text_heights) + line_spacing * (len(lines) - 1 if lines else 0)
        band_width = layout.width
        band_height = text_block_height + text_style.band_padding[1] * 2 + text_style.band_margin
        image = Image.new("RGBA", (band_width, band_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image, "RGBA")

        margin_x = text_style.band_padding[0]
        margin_y = text_style.band_padding[1]
        rect = [
            (margin_x, margin_y),
            (band_width - margin_x, band_height - margin_y),
        ]
        draw.rounded_rectangle(rect, radius=28, fill=text_style.band_color, outline=text_style.band_border_color, width=4)

        x_center = band_width / 2
        y_cursor = margin_y + (band_height - margin_y * 2 - text_block_height) / 2
        for idx, line in enumerate(lines):
            w = text_widths[idx]
            h = text_heights[idx]
            x = x_center - (w / 2)
            y = y_cursor
            if text_style.drop_shadow > 0:
                draw.text(
                    (x + text_style.drop_shadow, y + text_style.drop_shadow),
                    line,
                    font=font,
                    fill=(0, 0, 0, 120),
                    stroke_width=text_style.stroke_width,
                    stroke_fill=(0, 0, 0, 120),
                )
            draw.text(
                (x, y),
                line,
                font=font,
                fill=text_style.color,
                stroke_width=text_style.stroke_width,
                stroke_fill=text_style.stroke_color,
            )
            y_cursor += h + line_spacing

        # Nameplate
        speaker_spec = self.style.characters.get(plan.speaker_key)
        name = speaker_spec.display_name if speaker_spec else plan.speaker_key
        if name:
            np_style = self.style.nameplate
            np_font = self._font(self.style.text.font_path_bold, np_style.text_size)
            bbox = np_font.getbbox(name)
            nw = bbox[2] - bbox[0]
            nh = bbox[3] - bbox[1]
            pad_x = 12
            pad_y = 8
            plate_w = nw + pad_x * 2
            plate_h = nh + pad_y * 2
            anchor = speaker_spec.anchor if speaker_spec else "left"
            if anchor == "right":
                px = band_width - plate_w - margin_x
            elif anchor == "center":
                px = (band_width - plate_w) / 2
            else:
                px = margin_x
            py = max(int(margin_y * 0.35), 6)
            rect_plate = [(px, py), (px + plate_w, py + plate_h)]
            draw.rounded_rectangle(rect_plate, radius=18, fill=np_style.band_color, outline=np_style.band_border_color, width=3)
            draw.text(
                (px + pad_x, py + pad_y),
                name,
                font=np_font,
                fill=np_style.text_color,
                stroke_width=2,
                stroke_fill=np_style.stroke_color,
            )

        # Drop shadow outside the band (simple alpha copy)
        if text_style.drop_shadow > 0:
            shadow = Image.new("RGBA", (band_width, band_height), (0, 0, 0, 0))
            shadow_draw = ImageDraw.Draw(shadow, "RGBA")
            shadow_rect = [
                (margin_x + text_style.drop_shadow, margin_y + text_style.drop_shadow),
                (band_width - margin_x + text_style.drop_shadow, band_height - margin_y + text_style.drop_shadow),
            ]
            shadow_draw.rounded_rectangle(shadow_rect, radius=28, fill=(0, 0, 0, 90))
            shadow.alpha_composite(image)
            image = shadow

        out_path.parent.mkdir(parents=True, exist_ok=True)
        return image

    def _build_audio(self, unit: RenderUnit, duration: float):
        voice = None
        if unit.audio_path.exists():
            try:
                voice = AudioFileClip(str(unit.audio_path))
            except Exception as exc:  # pragma: no cover
                logger.error("Failed to load voice audio %s (%s)", unit.audio_path, exc)
        if voice is None:
            silence_array = np.zeros((int(math.ceil(duration * 44100)), 1), dtype=np.float32)
            voice = AudioArrayClip(silence_array, fps=44100).set_duration(duration)

        needs_bgm = unit.bgm_path and unit.bgm_path.exists()
        if not needs_bgm:
            return voice.set_duration(duration)

        try:
            bgm = AudioFileClip(str(unit.bgm_path))
            bgm = afx.audio_loop(bgm, duration=duration)
            bgm = bgm.volumex(self.bgm_volume)
            if self.bgm_fade_in > 0:
                bgm = afx.audio_fadein(bgm, self.bgm_fade_in)
            if self.bgm_fade_out > 0:
                bgm = afx.audio_fadeout(bgm, self.bgm_fade_out)
            mixed = CompositeAudioClip([voice, bgm]).set_duration(duration)
            return mixed
        except Exception as exc:  # pragma: no cover
            logger.error("BGM mix failed for %s (%s)", unit.plan.index, exc)
            return voice.set_duration(duration)

    def resolve_bgm_path(self, plan: ShotPlan) -> Optional[Path]:
        candidates: List[Path] = []
        default_candidate = self.default_bgm
        cue = plan.bgm_cue
        if cue:
            candidates.append(Path(cue))
            if not Path(cue).is_absolute() and Path("background_music").exists():
                candidates.append(Path("background_music") / cue)
        if default_candidate:
            candidates.append(Path(default_candidate))
        for candidate in candidates:
            try:
                if candidate.exists():
                    return candidate
            except Exception:
                continue
        return None

    def _font(self, path: Path, size: int) -> ImageFont.FreeTypeFont:
        key = (path, size)
        if key in self.font_cache:
            return self.font_cache[key]
        try:
            font = ImageFont.truetype(str(path), size=size)
        except OSError:
            fallback = Path("fonts") / "NotoSansJP-Bold.ttf"
            try:
                font = ImageFont.truetype(str(fallback), size=size)
            except OSError:
                font = ImageFont.load_default()
        self.font_cache[key] = font
        return font


def _wrap_by_chars(text: str, limit: int) -> List[str]:
    if limit <= 0:
        return [text]
    normalized = text.replace("\n", " ").strip()
    if not normalized:
        return [""]
    return textwrap.wrap(normalized, width=limit, replace_whitespace=False, drop_whitespace=False)
