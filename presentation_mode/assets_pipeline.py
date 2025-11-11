from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageOps

from logging_utils import get_logger
from speech_sanitizer import sanitize_for_voicevox

from .models import PresentationScene, PresentationScript
from .panel_renderer import DEFAULT_LAYOUT, PanelRenderer, PanelTheme, scale_layout
from .subtitles import SubtitleLine, write_ass_subtitles
from .utils import hex_to_rgb
from .voicevox_adapter import PresentationVoicevoxClient, QueryTiming

logger = get_logger(__name__)


@dataclass(frozen=True)
class SceneAssets:
    scene: PresentationScene
    audio_path: Path
    duration: float
    subtitles_path: Path
    subtitle_lines: Tuple[SubtitleLine, ...]
    panel_image_path: Path
    background_path: Path
    start_time: float
    pre_padding: float
    post_padding: float
    speech_duration: float


class BackgroundManager:
    """Provide a static background image for all presentation scenes."""

    def __init__(
        self,
        *,
        run_dir: Path,
        config: Dict[str, object],
    ) -> None:
        self.run_dir = run_dir
        self._config = config
        self.background_dir = run_dir / "backgrounds"
        self.background_dir.mkdir(parents=True, exist_ok=True)
        backgrounds_dir = Path(__file__).resolve().parent / "backgrounds"
        candidate_names = ("back8.png", "back8.PNG", "back8.jpg", "back8.JPG")
        static_source = None
        for name in candidate_names:
            candidate = backgrounds_dir / name
            if candidate.exists():
                static_source = candidate
                break
        if static_source is None:
            raise FileNotFoundError(
                "Static background not found: expected one of back8.png/back8.jpg"
            )
        self.static_background_source = static_source
        video_cfg = config.get("video", {}) if isinstance(config, dict) else {}
        target_width = int(video_cfg.get("width", 1920))
        target_height = int(video_cfg.get("height", 1080))
        if target_width % 2:
            target_width -= 1
        if target_height % 2:
            target_height -= 1
        self.target_resolution = (max(2, target_width), max(2, target_height))
        self.cached_path: Optional[Path] = None

    def get(self, group_index: int, prompt: Optional[str]) -> Path:  # noqa: ARG002
        if self.cached_path and self.cached_path.exists():
            return self.cached_path

        target = self.background_dir / self.static_background_source.name
        if not target.exists():
            self._prepare_background(target)
        self.cached_path = target
        return target

    def _prepare_background(self, target: Path) -> None:
        width, height = self.target_resolution
        with Image.open(self.static_background_source) as src:
            try:
                resample = Image.Resampling.LANCZOS  # Pillow >=9
            except AttributeError:  # pragma: no cover - older Pillow fallback
                resample = Image.LANCZOS
            fitted = ImageOps.fit(
                src.convert("RGB"),
                (width, height),
                method=resample,
                centering=(0.5, 0.5),
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            fitted.save(target, format="JPEG", quality=95)


class PresentationAssetPipeline:
    """Generate narration, panel overlays, backgrounds, and subtitles."""

    def __init__(
        self,
        *,
        run_dir: Path,
        config: Dict[str, object],
    ) -> None:
        self.run_dir = run_dir
        self.config = config
        self.audio_dir = run_dir / "audio"
        self.panel_dir = run_dir / "panel_layers"
        self.subtitles_dir = run_dir / "subtitles"

        for directory in (self.audio_dir, self.panel_dir, self.subtitles_dir):
            directory.mkdir(parents=True, exist_ok=True)

        text_cfg = config.get("text", {}) if isinstance(config, dict) else {}
        colors = text_cfg.get("colors", {}) if isinstance(text_cfg, dict) else {}
        title_size = int(text_cfg.get("title_size_override", 72)) if isinstance(text_cfg, dict) else 72
        body_size = int(text_cfg.get("body_size_override", 52)) if isinstance(text_cfg, dict) else 52
        conclusion_size = int(text_cfg.get("conclusion_size_override", 58)) if isinstance(text_cfg, dict) else 58

        template_default = Path(__file__).resolve().parent / "assets" / "panel_base.png"
        panel_cfg = config.get("presentation_panel", {}) if isinstance(config, dict) else {}
        theme_cfg = panel_cfg.get("theme", {}) if isinstance(panel_cfg, dict) else {}
        theme = PanelTheme.from_dict(theme_cfg)

        use_template = True
        if isinstance(panel_cfg, dict) and "use_template" in panel_cfg:
            use_template = bool(panel_cfg.get("use_template", True))
        template_path = template_default if use_template and template_default.exists() else None

        panel_text_color = (
            hex_to_rgb(panel_cfg.get("text_color"), (40, 40, 40))
            if isinstance(panel_cfg, dict)
            else (40, 40, 40)
        )
        panel_accent_color = (
            hex_to_rgb(panel_cfg.get("accent_color"), (255, 80, 160))
            if isinstance(panel_cfg, dict)
            else (255, 80, 160)
        )

        video_width, video_height = self._video_resolution()
        panel_width = max(1, int(round(video_width * 0.65)))
        panel_height = max(1, int(round(video_height * 0.82)))
        panel_layout = scale_layout(DEFAULT_LAYOUT, panel_width, panel_height)

        self.panel_renderer = PanelRenderer(
            template_path=template_path,
            layout=panel_layout,
            font_path=text_cfg.get("font_path"),
            title_size=title_size,
            body_size=body_size,
            conclusion_size=conclusion_size,
            text_color=panel_text_color,
            accent_color=panel_accent_color,
            theme=theme,
        )

        self.sub_font_name = str(text_cfg.get("font_family", "Noto Sans JP"))
        base_subtitle_size = int(text_cfg.get("subtitle_size_override", 84))
        scaled_subtitle_size = int(round(base_subtitle_size * 1.3))
        self.sub_font_size = max(36, scaled_subtitle_size)

        self.voice_client = PresentationVoicevoxClient(config)
        self.backgrounds = BackgroundManager(run_dir=run_dir, config=config)

    def prepare(self, script: PresentationScript) -> List[SceneAssets]:
        assets: List[SceneAssets] = []
        cumulative_time = 0.0

        for index, scene in enumerate(script.scenes):
            logger.info("Generating assets for scene %s", scene.scene_id)
            display_lines = self._resolve_subtitle_lines(scene)
            audio_path, duration, segments, timing_info, speech_duration = self._synthesize_scene_audio(
                scene, display_lines
            )

            subtitles_path = self._build_subtitles(scene, segments, resolution=self._video_resolution())
            panel_path = self._render_panel(scene, index)

            interval = script.change_interval()
            group_index = int(cumulative_time // interval)
            bg_prompt = script.background_prompt_for_index(index)
            background_path = self.backgrounds.get(group_index, bg_prompt)

            scene_assets = SceneAssets(
                scene=scene,
                audio_path=audio_path,
                duration=duration,
                subtitles_path=subtitles_path,
                subtitle_lines=segments,
                panel_image_path=panel_path,
                background_path=background_path,
                start_time=cumulative_time,
                pre_padding=timing_info.pre_padding,
                post_padding=timing_info.post_padding,
                speech_duration=speech_duration,
            )
            cumulative_time += duration
            assets.append(scene_assets)

        return assets

    # ------------------------------------------------------------------

    def _video_resolution(self) -> Tuple[int, int]:
        video_cfg = self.config.get("video", {}) if isinstance(self.config, dict) else {}
        return (
            int(video_cfg.get("width", 1920)),
            int(video_cfg.get("height", 1080)),
        )

    def _resolve_subtitle_lines(self, scene: PresentationScene) -> List[str]:
        if scene.subtitle_lines:
            lines = [line.strip() for line in scene.subtitle_lines if line and line.strip()]
            if lines:
                return lines
        source = scene.subtitle_override or scene.narration
        return self._segment_text(source)

    def _segment_text(self, source: str) -> List[str]:
        normalized = source.strip()
        if not normalized:
            return []
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if lines:
            return lines
        segments: List[str] = []
        buffer = ""
        delimiters = "。！？!?"
        soft_delims = "、,"
        max_len = 28

        for char in normalized:
            buffer += char
            if char in delimiters:
                segments.append(buffer.strip())
                buffer = ""
                continue
            if char in soft_delims and len(buffer) >= max_len:
                segments.append(buffer.strip())
                buffer = ""
                continue
            if len(buffer) >= max_len * 1.6:
                segments.append(buffer.strip())
                buffer = ""

        if buffer.strip():
            segments.append(buffer.strip())
        return [seg for seg in segments if seg]

    def _synthesize_scene_audio(
        self,
        scene: PresentationScene,
        subtitle_texts: Sequence[str],
    ) -> Tuple[Path, float, Tuple[SubtitleLine, ...], QueryTiming, float]:
        output_path = self.audio_dir / f"{scene.scene_id}.wav"
        narration_text = scene.narration.strip() or "。"
        sanitized_narration = sanitize_for_voicevox(narration_text)
        if not sanitized_narration.strip():
            sanitized_narration = "。"

        primary_query = self.voice_client.create_audio_query(sanitized_narration)
        timing_info = self.voice_client.analyze_query_timing(primary_query)

        if primary_query:
            audio_path, actual_duration = self.voice_client.synthesize_from_query(primary_query, output_path)
        else:
            audio_path, actual_duration = self.voice_client.synthesize(sanitized_narration, output_path)

        base_offset = timing_info.pre_padding
        speech_window_end = max(actual_duration - timing_info.post_padding, base_offset)
        speech_target_duration = max(speech_window_end - base_offset, 0.0)

        duration_estimates = self._estimate_line_durations(subtitle_texts)
        if subtitle_texts:
            durations = self._fit_durations(duration_estimates, speech_target_duration, subtitle_texts)
        else:
            durations = []

        subtitle_lines: List[SubtitleLine] = []
        if not subtitle_texts:
            fallback_start = base_offset
            fallback_duration = speech_target_duration
            if fallback_duration <= 0.0:
                fallback_duration = max(actual_duration - fallback_start, actual_duration or 1.0)
            fallback_duration = max(fallback_duration, 0.1)
            subtitle_lines.append(
                SubtitleLine(
                    index=1,
                    start=fallback_start,
                    duration=fallback_duration,
                    text=scene.narration.strip(),
                )
            )
        else:
            current_start = base_offset
            total_lines = len(subtitle_texts)
            target_end = max(speech_window_end, current_start)
            durations_list = list(durations)

            for idx, line in enumerate(subtitle_texts, start=1):
                clean_line = line.strip()
                raw_duration = (
                    durations_list[idx - 1]
                    if idx - 1 < len(durations_list)
                    else speech_target_duration / total_lines if total_lines else 0.0
                )
                remaining = total_lines - idx
                if remaining == 0:
                    desired_end = max(target_end, current_start)
                    duration_value = max(desired_end - current_start, 0.05)
                else:
                    duration_value = max(raw_duration, 0.05)
                subtitle_lines.append(
                    SubtitleLine(
                        index=idx,
                        start=current_start,
                        duration=duration_value,
                        text=clean_line,
                    )
                )
                current_start += duration_value

            if subtitle_lines:
                last = subtitle_lines[-1]
                final_end = last.end
                desired_end = max(target_end, min(speech_window_end, actual_duration))
                if desired_end > final_end + 1e-3 or desired_end < final_end - 1e-3:
                    adjusted_duration = max(desired_end - last.start, 0.05)
                    subtitle_lines[-1] = SubtitleLine(
                        index=last.index,
                        start=last.start,
                        duration=adjusted_duration,
                        text=last.text,
                    )

        total_duration = max(actual_duration, speech_window_end)
        if subtitle_lines:
            total_duration = max(total_duration, subtitle_lines[-1].end)

        return audio_path, total_duration, tuple(subtitle_lines), timing_info, speech_target_duration

    def _build_subtitles(
        self,
        scene: PresentationScene,
        segments: Sequence[SubtitleLine],
        *,
        resolution: Tuple[int, int],
    ) -> Path:
        output_path = self.subtitles_dir / f"{scene.scene_id}.ass"
        return write_ass_subtitles(
            lines=segments,
            output_path=output_path,
            font_name=self.sub_font_name,
            font_size=self.sub_font_size,
            resolution=resolution,
        )

    def _render_panel(self, scene: PresentationScene, index: int) -> Path:
        output_path = self.panel_dir / f"{index:03d}_{scene.scene_id}.png"
        return self.panel_renderer.render(scene.panel, output_path)

    # ------------------------------------------------------------------

    def _estimate_line_durations(self, lines: Sequence[str]) -> Optional[List[float]]:
        if not lines:
            return None
        estimates: List[float] = []
        for line in lines:
            sanitized = sanitize_for_voicevox(line)
            if not sanitized.strip():
                estimates.append(0.2)
                continue
            query = self.voice_client.create_audio_query(sanitized)
            if not query:
                return None
            duration = self.voice_client.estimate_duration_from_query(query, include_padding=False)
            if duration <= 0.0:
                duration = max(len(sanitized) * 0.05, 0.3)
            estimates.append(duration)
        return estimates

    def _fit_durations(
        self,
        raw_durations: Optional[Sequence[float]],
        total_duration: float,
        lines: Sequence[str],
    ) -> List[float]:
        if not lines:
            return []
        if raw_durations:
            total_estimate = sum(raw_durations)
            if total_estimate > 0:
                scaled = [max(d, 0.05) for d in raw_durations]
                return self._normalize_duration_sum(scaled, total_duration)
        return self._allocate_by_ratio(lines, total_duration)

    def _allocate_by_ratio(self, lines: Sequence[str], total_duration: float) -> List[float]:
        if not lines:
            return []
        sanitized = [sanitize_for_voicevox(line) for line in lines]
        char_counts = [len(s) if len(s) > 0 else 1 for s in sanitized]
        total_chars = sum(char_counts)
        if total_chars <= 0:
            share = total_duration / len(lines) if lines else 0.0
            return [share for _ in lines]

        durations: List[float] = []
        for count in char_counts:
            portion = (count / total_chars) * total_duration
            durations.append(max(portion, 0.05))
        return self._normalize_duration_sum(durations, total_duration)

    def _normalize_duration_sum(self, durations: Sequence[float], total_duration: float) -> List[float]:
        if not durations:
            return []
        total = sum(durations)
        if total <= 0:
            share = total_duration / len(durations) if durations else 0.0
            return [share for _ in durations]
        scale = total_duration / total
        normalized = [d * scale for d in durations]
        normalized = [max(d, 0.01) for d in normalized]
        adjusted_total = sum(normalized)
        if adjusted_total <= 0:
            share = total_duration / len(normalized) if normalized else 0.0
            return [share for _ in normalized]
        secondary_scale = total_duration / adjusted_total
        return [d * secondary_scale for d in normalized]
