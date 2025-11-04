from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import requests

from logging_utils import get_logger
from voicevox_client import VoicevoxClient, VoicevoxError

logger = get_logger(__name__)


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class PresentationVoicevoxClient(VoicevoxClient):
    """Presentation-mode specific helper built on top of the shared VOICEVOX client."""

    def create_audio_query(self, text: str) -> Optional[Dict[str, Any]]:
        text = text or ""
        if not text.strip():
            logger.debug("Skipping audio query for empty narration")
            return None
        try:
            self.ensure_ready()
        except VoicevoxError as exc:
            logger.error("VOICEVOX unavailable during audio query (%s)", exc)
            return None
        try:
            return self._create_audio_query(text)
        except requests.RequestException as exc:
            logger.error("Failed to obtain audio query (%s)", exc)
            return None

    def synthesize_from_query(self, audio_query: Dict[str, Any], output_path: Path) -> tuple[Path, float]:
        if not audio_query:
            logger.warning("Empty audio query supplied; falling back to plain synthesis")
            return self.synthesize("", output_path)
        try:
            self.ensure_ready()
        except VoicevoxError as exc:
            logger.error("VOICEVOX unavailable during synthesis (%s)", exc)
            return self.synthesize("", output_path)
        try:
            self._synthesize_audio(audio_query, output_path)
            duration = self._read_duration(output_path)
            return output_path, duration
        except requests.RequestException as exc:
            logger.error("VOICEVOX synthesis from query failed (%s)", exc)
            return self.synthesize("", output_path)

    def estimate_duration_from_query(self, audio_query: Optional[Dict[str, Any]]) -> float:
        if not audio_query:
            return 0.0

        total = 0.0
        total += _to_float(audio_query.get("prePhonemeLength"))

        accent_phrases: Sequence[Dict[str, Any]] = (
            audio_query.get("accent_phrases")
            or audio_query.get("accentPhrases")
            or []
        )
        for phrase in accent_phrases:
            for mora in phrase.get("moras", []):
                total += _to_float(mora.get("consonant_length"))
                total += _to_float(mora.get("vowel_length"))
            pause_mora = phrase.get("pause_mora") or {}
            total += _to_float(pause_mora.get("consonant_length"))
            total += _to_float(pause_mora.get("vowel_length"))
            total += _to_float(phrase.get("pause_length"))

        total += _to_float(audio_query.get("postPhonemeLength"))

        speed = _to_float(audio_query.get("speedScale"))
        if speed <= 0:
            speed = 1.0
        total /= speed

        return max(total, 0.0)
