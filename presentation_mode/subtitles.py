from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple


@dataclass(frozen=True)
class SubtitleLine:
    index: int
    start: float
    duration: float
    text: str

    @property
    def end(self) -> float:
        return self.start + self.duration


def _format_timestamp(seconds: float) -> str:
    total_centiseconds = int(round(seconds * 100))
    cs = total_centiseconds % 100
    total_seconds = total_centiseconds // 100
    s = total_seconds % 60
    total_minutes = total_seconds // 60
    m = total_minutes % 60
    h = total_minutes // 60
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", r"\\").replace("\n", r"\N").replace("{", r"\{").replace("}", r"\}")


def write_ass_subtitles(
    *,
    lines: Iterable[SubtitleLine],
    output_path: Path,
    font_name: str,
    font_size: int,
    resolution: Tuple[int, int],
) -> Path:
    width, height = resolution
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font_name},{font_size},&H00FFFFFF,&H00FFFFFF,&H00202020,&H00000000,"
        "0,0,0,0,100,100,0,0,1,3,0,2,60,60,70,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    body: List[str] = []
    for line in lines:
        start = _format_timestamp(max(0.0, line.start))
        end = _format_timestamp(max(line.end, line.start + 0.01))
        escaped = _escape_ass_text(line.text)
        body.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{escaped}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for entry in header:
            f.write(entry + "\n")
        for entry in body:
            f.write(entry + "\n")
    return output_path

