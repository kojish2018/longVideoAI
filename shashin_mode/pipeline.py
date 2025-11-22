"""Orchestrator for shashin_mode pipeline."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, asdict, field
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Any, List, Optional, Type

from logging_utils import get_logger
from speech_sanitizer import sanitize_for_voicevox
from voicevox_client import VoicevoxClient

from .config import LayoutConfig, ModePaths, RendererSettings, TimingConfig
from .image_fetcher import ImageFetcher
from .ffmpeg_renderer import FFmpegShashinRenderer
from .renderer import RenderChunk, ShashinRenderer
from .script_loader import ScriptDocument, SubtitleChunk, load_script
from .subtitle_writer import SubtitleEntry, write_srt

logger = get_logger(__name__)


@dataclass
class PipelineResult:
    run_id: str
    video_path: Path
    subtitle_path: Path
    plan_path: Path
    run_dir: Path
    total_duration: float
    chunk_image_paths: List[Path] = field(default_factory=list)
    thumbnail_path: Optional[Path] = None


class ShashinPipeline:
    def __init__(
        self,
        *,
        layout: LayoutConfig,
        timing: TimingConfig,
        paths: ModePaths,
        image_provider: str = "openverse",
        image_query_prefix: str = "",
        fallback_image: Optional[Path] = None,
        voicevox_config: Optional[dict] = None,
        renderer_settings: Optional[RendererSettings] = None,
    ) -> None:
        self.layout = layout
        self.timing = timing
        self.paths = paths
        voice_profile = voicevox_config if voicevox_config is not None else {"apis": {"voicevox": {}}}
        self.voice_client = VoicevoxClient(voice_profile)
        self.image_fetcher = ImageFetcher(
            provider=image_provider,
            fallback_image=fallback_image,
        )
        self.renderer_settings = renderer_settings or RendererSettings()
        self.renderer = self._build_renderer(layout, paths)
        self.image_query_prefix = image_query_prefix.strip()
        self.shared_images: List[Path] = []
        self._shared_image_index = 0
        self._shared_batch_size = 10

    def run(
        self,
        *,
        script_path: Path,
        background_path: Path,
        wrap_chars: Optional[int] = None,
        output_path: Optional[Path] = None,
        script_doc: Optional[ScriptDocument] = None,
    ) -> PipelineResult:
        if script_doc is None:
            script_doc = load_script(script_path, wrap_chars=wrap_chars)
        chunks = script_doc.chunks
        logger.info("Preparing assets for %d chunks", len(chunks))

        if script_doc.shared_openverse_query:
            self._prepare_shared_openverse_images(script_doc.shared_openverse_query)

        render_chunks: List[RenderChunk] = []
        subtitles: List[SubtitleEntry] = []
        current_start = 0.0
        chunk_image_paths: List[Path] = []

        for chunk in chunks:
            audio_path, duration = self._synthesize_chunk_audio(chunk)
            duration = max(duration + self.timing.padding_seconds, self.timing.min_chunk_duration)
            image_path = self._fetch_image_for_chunk(chunk)

            render_chunk = RenderChunk(
                chunk=chunk,
                audio_path=audio_path,
                image_path=image_path,
                duration=duration,
                start=current_start,
            )
            render_chunks.append(render_chunk)
            if render_chunk.image_path and Path(render_chunk.image_path).exists():
                chunk_image_paths.append(Path(render_chunk.image_path))

            subtitle_entry = SubtitleEntry(
                index=chunk.index,
                start=current_start,
                end=current_start + duration,
                lines=chunk.lines,
            )
            subtitles.append(subtitle_entry)
            current_start += duration

        logger.info("Rendering video (total duration ~%.2f sec)", current_start)
        video_output = self._resolve_output_path(output_path)
        rendered_path = self.renderer.render(
            background=background_path,
            chunks=render_chunks,
            output_path=video_output,
            temp_dir=self.paths.temp_dir,
        )

        subtitle_path = write_srt(subtitles, self.paths.subtitle_path)
        plan_path = self._write_plan(render_chunks, rendered_path, subtitle_path, total_duration=current_start)

        return PipelineResult(
            run_id=self.paths.run_dir.name,
            video_path=rendered_path,
            subtitle_path=subtitle_path,
            plan_path=plan_path,
            run_dir=self.paths.run_dir,
            total_duration=current_start,
            chunk_image_paths=chunk_image_paths,
        )

    # ------------------------------------------------------------------ #
    # Renderer wiring
    # ------------------------------------------------------------------ #

    def _build_renderer(self, layout: LayoutConfig, paths: ModePaths):
        settings = self.renderer_settings
        name = settings.normalized_name()

        if settings.class_path:
            renderer_cls = self._import_renderer_class(settings.class_path)
            kwargs = dict(settings.options)
            kwargs.setdefault("layout", layout)
            kwargs.setdefault("paths", paths)
            kwargs.setdefault("overlay_dir", paths.overlay_dir)
            try:
                renderer = renderer_cls(**kwargs)
            except TypeError as exc:
                raise TypeError(
                    f"Failed to instantiate custom renderer '{settings.class_path}': {exc}"
                ) from exc
            logger.info(
                "Using custom shashin renderer %s (name=%s)", settings.class_path, settings.name
            )
            return renderer

        if name in {"moviepy"}:
            if settings.options:
                logger.info(
                    "Using moviepy renderer with overrides from %s",
                    settings.config_path or "inline settings",
                )
            return ShashinRenderer(layout, overlay_dir=paths.overlay_dir, write_options=settings.options)

        if name in {"ffmpeg", "", "default"}:
            ffmpeg_options = dict(settings.options)
            ffmpeg_path = str(ffmpeg_options.pop("ffmpeg_path", "ffmpeg"))
            if settings.options:
                logger.info(
                    "Using ffmpeg renderer (config=%s)", settings.config_path or "inline"
                )
            return FFmpegShashinRenderer(
                layout,
                overlay_dir=paths.overlay_dir,
                ffmpeg_path=ffmpeg_path,
                options=ffmpeg_options,
            )

        raise ValueError(
            f"Unsupported renderer '{settings.name}'. Provide `class` in renderer_override.yaml or use 'ffmpeg'/'moviepy'."
        )

    @staticmethod
    def _import_renderer_class(qualified_name: str) -> Type[Any]:
        module_name, _, class_name = qualified_name.rpartition(".")
        if not module_name or not class_name:
            raise ValueError(
                f"Renderer class must be a fully qualified path (e.g. module.Class). Got: {qualified_name}"
            )
        module = import_module(module_name)
        try:
            return getattr(module, class_name)
        except AttributeError as exc:
            raise ImportError(
                f"Renderer class '{class_name}' not found in module '{module_name}'."
            ) from exc

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _synthesize_chunk_audio(self, chunk: SubtitleChunk) -> tuple[Path, float]:
        sanitized = sanitize_for_voicevox(chunk.text)
        target_path = self.paths.audio_dir / f"chunk_{chunk.index:03d}.wav"
        audio_path, duration = self.voice_client.synthesize(sanitized, target_path)
        logger.info("Chunk %03d audio duration %.2f sec", chunk.index, duration)
        return audio_path, duration

    def _fetch_image_for_chunk(self, chunk: SubtitleChunk) -> Optional[Path]:
        base_text = chunk.query_text
        query_source = chunk.openverse_query or base_text
        if chunk.openverse_query:
            logger.info("Using Openverse marker for chunk %03d: %s", chunk.index, chunk.openverse_query)
        target_path = self.paths.image_dir / f"chunk_{chunk.index:03d}.jpg"
        if not chunk.openverse_query and self.shared_images:
            shared_source = self._next_shared_image_path()
            if shared_source:
                copied = self._copy_shared_image(shared_source, target_path)
                if copied:
                    return copied

        query = f"{self.image_query_prefix} {query_source}".strip() if self.image_query_prefix else query_source
        return self.image_fetcher.fetch(query, target_path)

    def _prepare_shared_openverse_images(self, query: str) -> None:
        shared_dir = self.paths.image_dir / "shared"
        images = self.image_fetcher.fetch_batch(query, shared_dir, limit=self._shared_batch_size)
        if images:
            self.shared_images = images
            self._shared_image_index = 0
            logger.info("Prepared %d shared images from Openverse query: %s", len(images), query)
        else:
            self.shared_images = []
            logger.warning("Shared Openverse query produced no images; falling back to per-chunk search")

    def _next_shared_image_path(self) -> Optional[Path]:
        if not self.shared_images:
            return None
        path = self.shared_images[self._shared_image_index % len(self.shared_images)]
        self._shared_image_index += 1
        return path

    def _copy_shared_image(self, source: Path, target: Path) -> Optional[Path]:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            logger.info("Reusing shared image %s for %s", source.name, target.name)
            return target
        except Exception as exc:
            logger.error("Failed to reuse shared image %s -> %s: %s", source, target, exc)
            return None

    def _write_plan(
        self,
        render_chunks: List[RenderChunk],
        video_path: Path,
        subtitle_path: Path,
        *,
        total_duration: float,
    ) -> Path:
        payload = {
            "run_id": self.paths.run_dir.name,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "video_path": str(video_path),
            "subtitle_path": str(subtitle_path),
            "total_duration_seconds": total_duration,
            "chunks": [
                {
                    "index": chunk.chunk.index,
                    "start": chunk.start,
                    "duration": chunk.duration,
                    "lines": chunk.chunk.lines,
                    "subtitle_text": chunk.chunk.text,
                    "audio_path": str(chunk.audio_path),
                    "image_path": str(chunk.image_path) if chunk.image_path else None,
                }
                for chunk in render_chunks
            ],
            "layout": asdict(self.layout),
            "timing": asdict(self.timing),
        }
        self.paths.plan_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.paths.plan_path

    def _resolve_output_path(self, output_path: Optional[Path]) -> Path:
        """Decide final video path, appending run_id to avoid overwrite."""
        run_id = self.paths.run_dir.name
        if output_path is None:
            return self.paths.run_dir / f"{run_id}.mp4"

        output_path = output_path.expanduser()
        suffix = output_path.suffix

        # If it looks like a directory (no suffix), put run_id.mp4 inside it.
        if not suffix:
            output_path.mkdir(parents=True, exist_ok=True)
            return output_path / f"{run_id}.mp4"

        # Otherwise treat as file path and append run_id before suffix.
        parent = output_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        return parent / f"{output_path.stem}_{run_id}{suffix}"
