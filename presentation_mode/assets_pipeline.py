from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image

from logging_utils import get_logger
from pollinations_client import PollinationsClient
from prompt_translator import PromptTranslator
from speech_sanitizer import sanitize_for_voicevox
from voicevox_client import VoicevoxClient

from .models import PresentationScene, PresentationScript
from .panel_renderer import PanelRenderer
from .subtitles import SubtitleLine, write_ass_subtitles
from .utils import hex_to_rgb, stable_hash

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


class BackgroundManager:
    """Fetch and cache background images from Pollinations."""

    def __init__(
        self,
        *,
        run_dir: Path,
        config: Dict[str, object],
        translator: PromptTranslator,
    ) -> None:
        self.run_dir = run_dir
        self.background_dir = run_dir / "backgrounds"
        self.background_dir.mkdir(parents=True, exist_ok=True)
        self.pollinations = PollinationsClient(config)
        self.translator = translator
        self.cache: Dict[int, Path] = {}
        simple_cfg = config.get("simple_mode", {}) if isinstance(config, dict) else {}
        default_prompt = simple_cfg.get("default_image_prompt", "cozy living room illustration")
        self.default_prompt = str(default_prompt) if default_prompt else "cozy living room illustration"

    def get(self, group_index: int, prompt: Optional[str]) -> Path:
        if group_index in self.cache:
            return self.cache[group_index]

        prompt_text = prompt or self.default_prompt
        translated = self.translator.translate(prompt_text)
        hash_id = stable_hash([str(group_index), translated or prompt_text])
        output_path = self.background_dir / f"bg_{group_index:02d}_{hash_id}.png"
        if output_path.exists():
            logger.info("Background cache hit: %s", output_path.name)
            self.cache[group_index] = output_path
            return output_path

        fetched = self.pollinations.fetch(translated or prompt_text, output_path)
        if fetched:
            self.cache[group_index] = fetched
            return fetched

        # Fallback: solid colour background to avoid failure.
        fallback = self._create_placeholder(output_path, group_index)
        self.cache[group_index] = fallback
        return fallback

    def _create_placeholder(self, output_path: Path, group_index: int) -> Path:
        width = self.pollinations.width or 1920
        height = self.pollinations.height or 1080
        palette = [
            (244, 236, 255),
            (233, 244, 255),
            (255, 241, 233),
            (240, 255, 240),
        ]
        colour = palette[group_index % len(palette)]
        image = Image.new("RGB", (width, height), colour)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)
        return output_path


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
        self.chunk_dir = self.audio_dir / "chunks"
        self.panel_dir = run_dir / "panel_layers"
        self.subtitles_dir = run_dir / "subtitles"

        for directory in (self.audio_dir, self.chunk_dir, self.panel_dir, self.subtitles_dir):
            directory.mkdir(parents=True, exist_ok=True)

        text_cfg = config.get("text", {}) if isinstance(config, dict) else {}
        colors = text_cfg.get("colors", {}) if isinstance(text_cfg, dict) else {}
        title_size = int(text_cfg.get("title_size_override", 72)) if isinstance(text_cfg, dict) else 72
        body_size = int(text_cfg.get("body_size_override", 52)) if isinstance(text_cfg, dict) else 52
        conclusion_size = int(text_cfg.get("conclusion_size_override", 58)) if isinstance(text_cfg, dict) else 58

        template_path = Path(__file__).resolve().parent / "assets" / "panel_base.png"

        self.panel_renderer = PanelRenderer(
            template_path=template_path if template_path.exists() else None,
            font_path=text_cfg.get("font_path"),
            title_size=title_size,
            body_size=body_size,
            conclusion_size=conclusion_size,
            text_color=hex_to_rgb(colors.get("default"), (40, 40, 40)),
            accent_color=hex_to_rgb(colors.get("highlight"), (255, 80, 160)),
        )

        self.sub_font_name = str(text_cfg.get("font_family", "Noto Sans JP"))
        self.sub_font_size = max(36, int(text_cfg.get("subtitle_size_override", 48)))

        self.voice_client = VoicevoxClient(config)
        self.translator = PromptTranslator(config)
        self.backgrounds = BackgroundManager(run_dir=run_dir, config=config, translator=self.translator)

    def prepare(self, script: PresentationScript) -> List[SceneAssets]:
        assets: List[SceneAssets] = []
        cumulative_time = 0.0

        for index, scene in enumerate(script.scenes):
            logger.info("Generating assets for scene %s", scene.scene_id)
            subtitle_lines = self._segment_text(scene)
            if not subtitle_lines:
                subtitle_lines = [scene.narration.strip()]

            audio_path, duration, segments = self._synthesize_audio(scene, subtitle_lines)

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

    def _segment_text(self, scene: PresentationScene) -> List[str]:
        source = scene.subtitle_override or scene.narration
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

    def _synthesize_audio(
        self,
        scene: PresentationScene,
        segments_text: Sequence[str],
    ) -> Tuple[Path, float, Tuple[SubtitleLine, ...]]:
        chunk_paths: List[Path] = []
        chunk_durations: List[float] = []
        subtitle_lines: List[SubtitleLine] = []
        current_offset = 0.0

        for idx, line in enumerate(segments_text, start=1):
            sanitized = sanitize_for_voicevox(line)
            chunk_path = self.chunk_dir / f"{scene.scene_id}_{idx:02d}.wav"
            audio_path, duration = self.voice_client.synthesize(sanitized, chunk_path)
            chunk_paths.append(audio_path)
            chunk_durations.append(duration)
            subtitle_lines.append(
                SubtitleLine(
                    index=idx,
                    start=current_offset,
                    duration=duration,
                    text=line.strip(),
                )
            )
            current_offset += duration

        if not chunk_paths:
            # Fallback: write silence
            empty_path = self.audio_dir / f"{scene.scene_id}.wav"
            _, duration = self.voice_client.synthesize("。", empty_path)  # minimal audio
            fallback_duration = duration or 1.0
            return empty_path, fallback_duration, tuple(
                [
                    SubtitleLine(
                        index=1,
                        start=0.0,
                        duration=fallback_duration,
                        text="",
                    )
                ]
            )

        output_path = self.audio_dir / f"{scene.scene_id}.wav"
        self._concatenate_wavs(chunk_paths, output_path)
        total_duration = sum(chunk_durations)
        return output_path, total_duration, tuple(subtitle_lines)

    def _concatenate_wavs(self, chunk_paths: Sequence[Path], output_path: Path) -> None:
        import wave

        output_path.parent.mkdir(parents=True, exist_ok=True)
        params = None
        frames: List[bytes] = []
        for path in chunk_paths:
            with wave.open(str(path), "rb") as wav_file:
                params = wav_file.getparams()
                frames.append(wav_file.readframes(wav_file.getnframes()))
        if params is None:
            raise RuntimeError("No WAV parameters found during concatenation")

        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setparams(params)
            for frame in frames:
                wav_file.writeframes(frame)

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
