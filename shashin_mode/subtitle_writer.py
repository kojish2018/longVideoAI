"""Subtitle helpers for shashin_mode."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class SubtitleEntry:
    index: int
    start: float
    end: float
    lines: List[str]


def _format_timestamp(value: float) -> str:
    ms_total = int(round(value * 1000))
    hours = ms_total // 3_600_000
    minutes = (ms_total % 3_600_000) // 60_000
    seconds = (ms_total % 60_000) // 1_000
    millis = ms_total % 1_000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def write_srt(entries: List[SubtitleEntry], output_path: Path) -> Path:
    lines: List[str] = []
    for entry in entries:
        start = _format_timestamp(entry.start)
        end = _format_timestamp(entry.end)
        lines.append(str(entry.index))
        lines.append(f"{start} --> {end}")
        lines.extend(entry.lines)
        lines.append("")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path

