"""Convert YukkuriScript into renderable shot plans."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from logging_utils import get_logger

from .dialogue_types import YukkuriScript, YukkuriUtterance
from .styles import YukkuriStyle
from .voice_adapter import YukkuriVoiceAdapter

logger = get_logger(__name__)


@dataclass
class ShotPlan:
    index: int
    speaker_key: str
    text: str
    duration: float
    start: float
    bg_image: Optional[Path]
    bg_prompt: Optional[str]
    bg_reference: Optional[str]
    bgm_cue: Optional[str]
    se: Optional[str]
    layout_hint: Optional[str]
    overlay_style: Optional[str]
    emotion: Optional[str]
    extras: Dict[str, object]


@dataclass
class TimelinePlan:
    shots: List[ShotPlan]
    total_duration: float


def _resolve_background(
    candidate: Optional[str],
    *,
    script_dir: Path,
    search_dirs: Iterable[Path],
) -> Optional[Path]:
    if not candidate:
        return None
    raw = Path(candidate)
    if raw.is_absolute() and raw.exists():
        return raw

    # Try script-local first
    local = script_dir / raw
    if local.exists():
        return local

    for base in search_dirs:
        alt = base / raw
        if alt.exists():
            return alt
    return None


def build_timeline(
    script: YukkuriScript,
    *,
    style: YukkuriStyle,
    voice: YukkuriVoiceAdapter,
    inter_shot_gap: float = 0.18,
) -> TimelinePlan:
    shots: List[ShotPlan] = []
    cursor = 0.0
    for idx, utt in enumerate(script.utterances, start=1):
        speaker_key = YukkuriVoiceAdapter.canonical_speaker(utt.speaker, style.aliases)
        duration = _decide_duration(utt, voice, speaker_key)
        bg_path = _resolve_background(
            utt.bg_image,
            script_dir=script.source_path.parent,
            search_dirs=style.layout.backgrounds,
        )
        shots.append(
            ShotPlan(
                index=idx,
                speaker_key=speaker_key,
                text=utt.text,
                duration=duration,
                start=cursor,
                bg_image=bg_path,
                bg_prompt=utt.bg_prompt,
                bg_reference=utt.bg_reference,
                bgm_cue=utt.bgm_cue,
                se=utt.se,
                layout_hint=utt.layout_hint,
                overlay_style=utt.overlay_style,
                emotion=utt.emotion,
                extras=utt.extras,
            )
        )
        cursor += duration + inter_shot_gap

    total = cursor if shots else 0.0
    logger.info("Timeline built: %d shots, est %.1fs", len(shots), total)
    return TimelinePlan(shots=shots, total_duration=total)


def _decide_duration(utterance: YukkuriUtterance, voice: YukkuriVoiceAdapter, speaker_key: str) -> float:
    if utterance.duration_seconds is not None:
        return max(float(utterance.duration_seconds), voice.min_duration)
    if utterance.start is not None and utterance.end is not None:
        return max(float(utterance.end) - float(utterance.start), voice.min_duration)
    return voice.estimate_duration(utterance.text, speaker_key)
