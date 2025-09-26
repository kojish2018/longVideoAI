"""Minimal VOICEVOX HTTP client for local synthesis."""
from __future__ import annotations

import json
import logging
import wave
from array import array
from pathlib import Path
from typing import Any, Dict, Tuple

import requests

logger = logging.getLogger(__name__)


class VoicevoxError(RuntimeError):
    """Raised when VOICEVOX returns an unexpected response."""


class VoicevoxClient:
    """Thin wrapper around a locally running VOICEVOX engine."""

    def __init__(self, config: Dict[str, Any]) -> None:
        voice_cfg = config.get("apis", {}).get("voicevox", {})
        self.host = voice_cfg.get("host", "127.0.0.1")
        self.port = voice_cfg.get("port", 50021)
        self.speaker_id = voice_cfg.get("speaker_id", 3)
        self.speed_scale = voice_cfg.get("speed_scale", 1.0)
        self.volume_scale = voice_cfg.get("volume_scale", 1.0)
        self.intonation_scale = voice_cfg.get("intonation_scale", 1.0)
        self.pitch_scale = voice_cfg.get("pitch_scale", 1.0)

        self.base_url = f"http://{self.host}:{self.port}"
        self._connection_verified = False

    def ensure_ready(self) -> None:
        if self._connection_verified:
            return
        try:
            response = requests.get(f"{self.base_url}/version", timeout=5)
            response.raise_for_status()
            version = response.json()
            logger.info("VOICEVOX connected: %s", version)
            self._connection_verified = True
        except requests.RequestException as exc:  # pragma: no cover - network
            raise VoicevoxError(f"VOICEVOX server unavailable: {exc}") from exc

    def synthesize(self, text: str, output_path: Path) -> Tuple[Path, float]:
        """Generate a WAV file for the given text. Returns (path, duration)."""
        try:
            self.ensure_ready()
        except VoicevoxError as exc:
            logger.error("VOICEVOX unavailable (%s); writing silent audio", exc)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            duration = self._write_silent_wav(output_path, 3.0)
            return output_path, duration

        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not text.strip():
            logger.warning("Empty text supplied to VOICEVOX; writing silent audio")
            duration = self._write_silent_wav(output_path, 1.0)
            return output_path, duration

        try:
            query = self._create_audio_query(text)
            self._synthesize_audio(query, output_path)
            duration = self._read_duration(output_path)
            logger.debug("VOICEVOX synthesis success: %s (%.2f s)", output_path, duration)
            return output_path, duration
        except (VoicevoxError, requests.RequestException) as exc:
            logger.error("VOICEVOX synthesis failed (%s). Falling back to silence.", exc)
            duration = self._write_silent_wav(output_path, 3.0)
            return output_path, duration

    def _create_audio_query(self, text: str) -> Dict[str, Any]:
        url = f"{self.base_url}/audio_query"
        params = {"text": text, "speaker": self.speaker_id}
        response = requests.post(url, params=params, timeout=30)
        response.raise_for_status()
        audio_query: Dict[str, Any] = response.json()

        audio_query["speedScale"] = self.speed_scale
        audio_query["volumeScale"] = self.volume_scale
        audio_query["intonationScale"] = self.intonation_scale
        audio_query["pitchScale"] = self.pitch_scale
        return audio_query

    def _synthesize_audio(self, audio_query: Dict[str, Any], output_path: Path) -> None:
        url = f"{self.base_url}/synthesis"
        params = {"speaker": self.speaker_id}
        headers = {"Content-Type": "application/json"}
        response = requests.post(
            url,
            params=params,
            data=json.dumps(audio_query),
            headers=headers,
            timeout=120,
        )
        response.raise_for_status()
        output_path.write_bytes(response.content)

    @staticmethod
    def _read_duration(path: Path) -> float:
        with wave.open(str(path), "rb") as wav_file:
            frames = wav_file.getnframes()
            sample_rate = wav_file.getframerate()
            return frames / float(sample_rate)

    @staticmethod
    def _write_silent_wav(path: Path, duration: float) -> float:
        sample_rate = 44100
        sample_count = int(sample_rate * duration)
        silent = array("h", [0]) * sample_count
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(silent.tobytes())
        return duration
