"""Asset generation for long-form pipeline."""
from __future__ import annotations

import json
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from logging_utils import get_logger
from pollinations_client import PollinationsClient
from prompt_translator import PromptTranslator
from timeline_builder import Scene, SceneChunk, SceneType
from voicevox_client import VoicevoxClient

logger = get_logger(__name__)


@dataclass
class NarrationSegment:
    segment_index: int
    start_offset: float
    duration: float
    lines: List[str]


@dataclass
class GeneratedAssets:
    narration_path: Path
    narration_duration: float
    narration_metadata_path: Path
    image_path: Optional[Path]
    image_prompt_path: Optional[Path]
    image_prompt_text: Optional[str]
    segments: List[NarrationSegment]


@dataclass
class PromptBuildResult:
    """Intermediate data for Pollinations prompt generation."""

    prompt: str
    subject_original: Optional[str]
    subject_translated: Optional[str]
    template: Optional[str]
    constants: Dict[str, str] = field(default_factory=dict)


class AssetPipeline:
    """Generate audio/image assets for grouped scenes."""

    def __init__(
        self,
        *,
        run_dir: Path,
        config: Dict,
    ) -> None:
        self.run_dir = run_dir
        self.config = config
        self.voice_client = VoicevoxClient(config)
        self.image_client = PollinationsClient(config)
        self.translator = PromptTranslator(config)

        simple_cfg = config.get("simple_mode", {}) if isinstance(config, dict) else {}
        if isinstance(simple_cfg, dict):
            base_prompt_candidate = simple_cfg.get("default_image_prompt", "cinematic documentary scene, 16:9")
            template_candidate = simple_cfg.get("default_image_prompt_template")
            constants_raw = simple_cfg.get("prompt_constants", {})
        else:
            base_prompt_candidate = "cinematic documentary scene, 16:9"
            template_candidate = None
            constants_raw = {}

        self.base_prompt = str(base_prompt_candidate) if base_prompt_candidate else "cinematic documentary scene, 16:9"

        if template_candidate:
            template_text = str(template_candidate).strip()
            self.prompt_template = template_text if template_text else None
        else:
            self.prompt_template = None

        if isinstance(constants_raw, dict):
            self.prompt_constants = {
                str(key): str(value).strip()
                for key, value in constants_raw.items()
                if value is not None and str(value).strip()
            }
        else:
            self.prompt_constants = {}

        self.audio_dir = run_dir / "audio"
        self.image_dir = run_dir / "images"
        self.prompt_dir = self.image_dir
        self.chunk_dir = self.audio_dir / "chunks"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.chunk_dir.mkdir(parents=True, exist_ok=True)

        self._image_cache: Dict[str, Path] = {}

    def prepare_scene_assets(self, scene: Scene) -> GeneratedAssets:
        narration_path = self.audio_dir / f"{scene.scene_id}.wav"
        segments = self._synthesize_scene_audio(scene, narration_path)

        image_path: Optional[Path] = None
        prompt_path: Optional[Path] = None
        prompt_text: Optional[str] = None
        prompt_result: Optional[PromptBuildResult] = None
        if scene.scene_type is SceneType.CONTENT:
            prompt_result = self._compose_prompt(scene.image_prompt)
            if prompt_result:
                prompt_text = prompt_result.prompt
                image_path = self._get_or_create_image(scene.scene_id, prompt_text)
                prompt_path = self._write_prompt_metadata(
                    scene.scene_id,
                    prompt_result,
                    scene.image_prompt,
                )

        total_duration = segments[-1].start_offset + segments[-1].duration if segments else 0.0
        narration_metadata = {
            "scene_id": scene.scene_id,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "total_duration_seconds": total_duration,
            "segments": [
                {
                    "segment_index": segment.segment_index,
                    "start_offset": segment.start_offset,
                    "duration": segment.duration,
                    "lines": segment.lines,
                }
                for segment in segments
            ],
        }
        metadata_path = self.audio_dir / f"{scene.scene_id}.json"
        metadata_path.write_text(json.dumps(narration_metadata, ensure_ascii=False, indent=2), encoding="utf-8")

        return GeneratedAssets(
            narration_path=narration_path,
            narration_duration=total_duration,
            narration_metadata_path=metadata_path,
            image_path=image_path,
            image_prompt_path=prompt_path,
            image_prompt_text=prompt_text,
            segments=segments,
        )

    # ------------------------------------------------------------------
    # Audio helpers
    # ------------------------------------------------------------------

    def _synthesize_scene_audio(self, scene: Scene, output_path: Path) -> List[NarrationSegment]:
        chunk_files: List[Path] = []
        chunk_durations: List[float] = []

        for idx, chunk in enumerate(scene.chunks, start=1):
            text = chunk.text
            chunk_path = self.chunk_dir / f"{scene.scene_id}_{idx:02d}.wav"
            audio_path, duration = self.voice_client.synthesize(text, chunk_path)
            chunk_files.append(audio_path)
            chunk_durations.append(duration)

        if not chunk_files:
            output_path.write_bytes(b"")
            return []

        with wave.open(str(chunk_files[0]), "rb") as first_wave:
            params = first_wave.getparams()
            frames = [first_wave.readframes(first_wave.getnframes())]

        for chunk_file in chunk_files[1:]:
            with wave.open(str(chunk_file), "rb") as wav_file:
                frames.append(wav_file.readframes(wav_file.getnframes()))

        with wave.open(str(output_path), "wb") as out_wave:
            out_wave.setparams(params)
            for frame in frames:
                out_wave.writeframes(frame)

        segments: List[NarrationSegment] = []
        current_offset = 0.0
        for idx, (chunk, duration) in enumerate(zip(scene.chunks, chunk_durations), start=1):
            segments.append(
                NarrationSegment(
                    segment_index=idx,
                    start_offset=round(current_offset, 3),
                    duration=duration,
                    lines=chunk.lines,
                )
            )
            current_offset += duration

        return segments

    # ------------------------------------------------------------------
    # Image helpers
    # ------------------------------------------------------------------

    def _compose_prompt(self, focus_text: Optional[str]) -> Optional[PromptBuildResult]:
        original = (focus_text or "").strip()

        translated = ""
        if original:
            translated = self.translator.translate(original).strip()

        normalized_subject = self._normalize_subject(translated) if translated else ""

        if self.prompt_template:
            template_data: Dict[str, str] = dict(self.prompt_constants)
            template_data.setdefault("subject", normalized_subject or self.base_prompt)
            try:
                prompt_text = self.prompt_template.format(**template_data)
            except KeyError as exc:
                logger.error("Prompt template missing key %s; falling back to base prompt", exc)
                prompt_text = self.base_prompt
            return PromptBuildResult(
                prompt=prompt_text,
                subject_original=original or None,
                subject_translated=normalized_subject or None,
                template=self.prompt_template,
                constants=dict(self.prompt_constants),
            )

        if normalized_subject:
            prompt_text = f"{self.base_prompt} :: focus on '{normalized_subject}'"
        else:
            prompt_text = self.base_prompt

        return PromptBuildResult(
            prompt=prompt_text,
            subject_original=original or None,
            subject_translated=normalized_subject or None,
            template=None,
            constants=dict(self.prompt_constants),
        )

    @staticmethod
    def _normalize_subject(text: str) -> str:
        fragments = [part.strip() for part in text.splitlines()]
        return " ".join(fragment for fragment in fragments if fragment)

    def _get_or_create_image(self, scene_id: str, prompt: str) -> Path:
        if scene_id in self._image_cache:
            return self._image_cache[scene_id]

        output_path = self.image_dir / f"{scene_id}.jpg"
        existing = self.image_client.fetch(prompt, output_path)
        if existing:
            self._image_cache[scene_id] = existing
            return existing
        self._image_cache[scene_id] = output_path
        return output_path

    def _write_prompt_metadata(
        self,
        scene_id: str,
        prompt_result: PromptBuildResult,
        original_focus: Optional[str],
    ) -> Path:
        payload = {
            "scene_id": scene_id,
            "prompt": prompt_result.prompt,
            "subject_original": prompt_result.subject_original,
            "subject_translated": prompt_result.subject_translated,
            "template": prompt_result.template,
            "constants": prompt_result.constants,
            "original_focus_text": original_focus,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }
        prompt_path = self.prompt_dir / f"{scene_id}_prompt.json"
        prompt_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return prompt_path
