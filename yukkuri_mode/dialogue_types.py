"""Data structures for Yukkuri dialogue scripts (JSON/JSONL-first)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class YukkuriUtterance:
    """Single spoken line with optional rich metadata."""

    speaker: str
    text: str
    emotion: Optional[str] = None
    duration_seconds: Optional[float] = None
    start: Optional[float] = None
    end: Optional[float] = None
    bg_image: Optional[str] = None
    bg_prompt: Optional[str] = None
    bg_reference: Optional[str] = None
    bgm_cue: Optional[str] = None
    se: Optional[str] = None
    layout_hint: Optional[str] = None
    overlay_style: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class YukkuriScript:
    """Dialog-style script that keeps utterances plus top-level metadata."""

    source_path: Path
    title: str
    utterances: List[YukkuriUtterance]
    tags: Optional[List[str]] = None
    description: Optional[str] = None
    thumbnail_image_prompt: Optional[str] = None
    raw_metadata: Dict[str, Any] = field(default_factory=dict)

    def total_duration_hint(self) -> Optional[float]:
        """Sum explicit durations when available; otherwise None."""

        durations: List[float] = []
        for utt in self.utterances:
            if utt.duration_seconds is not None:
                durations.append(float(utt.duration_seconds))
            elif utt.start is not None and utt.end is not None:
                durations.append(float(utt.end) - float(utt.start))
        if not durations:
            return None
        return sum(durations)
