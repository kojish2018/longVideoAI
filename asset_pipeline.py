"""Asset generation for long-form pipeline."""
from __future__ import annotations

import json
import wave
from dataclasses import dataclass
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
        self.base_prompt = config.get("simple_mode", {}).get("default_image_prompt", "cinematic documentary scene, 16:9") if isinstance(config, dict) else "cinematic documentary scene, 16:9"
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
        if scene.scene_type is SceneType.CONTENT:
            prompt_text = self._compose_prompt(scene.image_prompt)
            if prompt_text:
                image_path = self._get_or_create_image(scene.scene_id, prompt_text)
                prompt_path = self._write_prompt_metadata(scene.scene_id, prompt_text, scene.image_prompt)

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

    def _compose_prompt(self, focus_text: Optional[str]) -> Optional[str]:
        if focus_text is None:
            return self.base_prompt
        translated = self.translator.translate(focus_text)
        if translated:
            return f"{self.base_prompt} :: focus on '{translated}'"
        return self.base_prompt

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

    def _write_prompt_metadata(self, scene_id: str, prompt: str, original_focus: Optional[str]) -> Path:
        payload = {
            "scene_id": scene_id,
            "prompt": prompt,
            "original_focus_text": original_focus,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }
        prompt_path = self.prompt_dir / f"{scene_id}_prompt.json"
        prompt_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return prompt_path
