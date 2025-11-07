from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class QueryTiming:
    pre_padding: float
    post_padding: float
    speed_scale: float
    speech_duration: float
    total_duration: float


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

    def estimate_duration_from_query(
        self,
        audio_query: Optional[Dict[str, Any]],
        *,
        include_padding: bool = True,
    ) -> float:
        if not audio_query:
            return 0.0

        total = 0.0
        pre = _to_float(audio_query.get("prePhonemeLength"))
        if include_padding:
            total += pre

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

        post = _to_float(audio_query.get("postPhonemeLength"))
        if include_padding:
            total += post

        speed = _to_float(audio_query.get("speedScale"))
        if speed <= 0:
            speed = 1.0
        total /= speed

        if not include_padding:
            # Ensure we never return negative even if pauses exceed speech.
            total = max(total, 0.0)
            # Small guard to avoid zero durations which later get clamped.
            if total == 0.0 and (pre > 0.0 or post > 0.0):
                total = pre + post

        return max(total, 0.0)

    def analyze_query_timing(self, audio_query: Optional[Dict[str, Any]]) -> QueryTiming:
        speed = _to_float(audio_query.get("speedScale")) if audio_query else 1.0
        if speed <= 0.0:
            speed = 1.0

        pre_raw = _to_float(audio_query.get("prePhonemeLength")) if audio_query else 0.0
        post_raw = _to_float(audio_query.get("postPhonemeLength")) if audio_query else 0.0
        pre = max(pre_raw / speed, 0.0)
        post = max(post_raw / speed, 0.0)

        speech_duration = self.estimate_duration_from_query(audio_query, include_padding=False)
        total_duration = speech_duration + pre + post
        if total_duration <= 0.0:
            total_duration = self.estimate_duration_from_query(audio_query, include_padding=True)

        return QueryTiming(
            pre_padding=pre,
            post_padding=post,
            speed_scale=speed,
            speech_duration=max(speech_duration, 0.0),
            total_duration=max(total_duration, 0.0),
        )
