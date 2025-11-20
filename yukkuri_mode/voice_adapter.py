"""Voice adapter for yukkuri dialogue (VOICEVOX-first with offline fallback)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from logging_utils import get_logger
from voicevox_client import VoicevoxClient

logger = get_logger(__name__)


@dataclass(frozen=True)
class VoiceProfile:
    speaker_id: int
    speed_scale: float
    name: str


@dataclass
class VoiceResult:
    path: Path
    duration: float
    used_voicevox: bool
    speaker_key: str


class YukkuriVoiceAdapter:
    """Thin wrapper that maps speaker aliases to VOICEVOX speaker ids."""

    def __init__(self, config: Dict[str, Any], *, enable_voice: bool = True) -> None:
        voice_cfg = config.get("voice", {}) if isinstance(config, dict) else {}
        self.use_voicevox = enable_voice and bool(voice_cfg.get("use_voicevox", True))
        self.chars_per_second = float(voice_cfg.get("chars_per_second", 6.0) or 6.0)
        self.min_duration = float(voice_cfg.get("min_duration", 1.6) or 1.6)
        self.padding_seconds = float(voice_cfg.get("padding_seconds", 0.35) or 0.35)

        speakers_cfg = voice_cfg.get("speakers", {}) if isinstance(voice_cfg, dict) else {}
        self.profiles: Dict[str, VoiceProfile] = {}
        for key, raw in speakers_cfg.items():
            if not isinstance(raw, dict):
                continue
            speaker_id = int(raw.get("speaker_id", 3) or 3)
            speed = float(raw.get("speed_scale", 1.0) or 1.0)
            self.profiles[key] = VoiceProfile(
                speaker_id=speaker_id,
                speed_scale=speed,
                name=key,
            )

        api_cfg = voice_cfg.get("apis", {}) if isinstance(voice_cfg, dict) else {}
        voicevox_cfg = api_cfg.get("voicevox", {}) if isinstance(api_cfg, dict) else {}
        self._client: Optional[VoicevoxClient] = VoicevoxClient({"apis": {"voicevox": voicevox_cfg}}) if self.use_voicevox else None

    @staticmethod
    def canonical_speaker(raw_name: str, aliases: Dict[str, str]) -> str:
        raw_name = (raw_name or "").strip()
        if raw_name in aliases:
            return str(aliases[raw_name])
        lowered = raw_name.lower()
        for alias, target in aliases.items():
            if alias.lower() == lowered:
                return str(target)
        return raw_name

    def _profile_for(self, speaker_key: str) -> VoiceProfile:
        if speaker_key in self.profiles:
            return self.profiles[speaker_key]
        if "narrator" in self.profiles:
            return self.profiles["narrator"]
        if self.profiles:
            return next(iter(self.profiles.values()))
        return VoiceProfile(speaker_id=3, speed_scale=1.0, name=speaker_key or "speaker")

    def synthesize(self, text: str, speaker_key: str, output_path: Path) -> VoiceResult:
        profile = self._profile_for(speaker_key)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.use_voicevox or self._client is None:
            duration = self._write_silence_with_estimate(text)
            VoicevoxClient._write_silent_wav(output_path, duration)
            return VoiceResult(path=output_path, duration=duration, used_voicevox=False, speaker_key=speaker_key)

        try:
            # Update profile-specific knobs before synthesis
            self._client.speaker_id = profile.speaker_id
            self._client.speed_scale = profile.speed_scale
            audio_path, duration = self._client.synthesize(text, output_path)
            if duration <= 0:
                duration = self._write_silence_with_estimate(text)
            return VoiceResult(
                path=audio_path,
                duration=duration,
                used_voicevox=True,
                speaker_key=speaker_key,
            )
        except Exception as exc:  # pragma: no cover - network/IO guard
            logger.error("VOICEVOX synthesis failed (%s); writing silent fallback", exc)
            duration = self._write_silence_with_estimate(text)
            VoicevoxClient._write_silent_wav(output_path, duration)
            return VoiceResult(path=output_path, duration=duration, used_voicevox=False, speaker_key=speaker_key)

    def estimate_duration(self, text: str, speaker_key: str | None = None) -> float:
        if not text.strip():
            return self.min_duration
        base = max(len(text) / max(self.chars_per_second, 0.1), self.min_duration)
        profile = self._profile_for(speaker_key or "")
        if profile.speed_scale > 0:
            base /= profile.speed_scale
        return float(base + self.padding_seconds)

    def _write_silence_with_estimate(self, text: str) -> float:
        estimated = self.estimate_duration(text)
        return max(estimated, self.min_duration)
