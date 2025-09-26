"""Utilities to parse long-form script files."""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import List

from logging_utils import get_logger

logger = get_logger(__name__)


@dataclass
class ScriptSection:
    index: int
    raw_text: str
    lines: List[str]

    @property
    def word_count(self) -> int:
        tokens = [line.split() for line in self.lines]
        word_based = sum(len(parts) for parts in tokens)
        if word_based >= 3:
            return word_based

        joined = "".join(line.strip() for line in self.lines)
        char_count = len(joined)
        if char_count == 0:
            return 0

        # 日本語など空白を含まないテキストは概ね3文字で1語換算
        estimated_words = ceil(char_count / 3)
        return max(word_based, estimated_words)


@dataclass
class ScriptDocument:
    thumbnail_title: str
    sections: List[ScriptSection]

    def total_word_count(self) -> int:
        return sum(section.word_count for section in self.sections)


def parse_script(path: Path | str) -> ScriptDocument:
    """Parse a script file following the long-form spec."""
    script_path = Path(path).expanduser().resolve()
    if not script_path.exists():
        raise FileNotFoundError(f"Script file not found: {script_path}")

    raw_text = script_path.read_text(encoding="utf-8").strip()
    if not raw_text:
        raise ValueError("Script file is empty")

    lines = raw_text.splitlines()
    thumbnail_title = ""
    body_lines: List[str] = []

    # Extract thumbnail title if the first line follows s"title" format
    if lines and lines[0].startswith('s"') and lines[0].endswith('"'):
        thumbnail_title = lines[0][2:-1].strip()
        body_lines = lines[1:]
    else:
        body_lines = lines
        logger.warning("Thumbnail title line (s\"...\") not found; using fallback title")

    # Split sections by blank line
    sections_raw: List[str] = []
    current: List[str] = []
    for line in body_lines:
        if line.strip():
            current.append(line.rstrip())
        else:
            if current:
                sections_raw.append("\n".join(current).strip())
                current = []
    if current:
        sections_raw.append("\n".join(current).strip())

    if not sections_raw:
        raise ValueError("No content sections detected in script")

    sections: List[ScriptSection] = []
    for idx, block in enumerate(sections_raw, start=1):
        block_lines = [line.strip() for line in block.split("\n") if line.strip()]
        sections.append(
            ScriptSection(
                index=idx,
                raw_text=block,
                lines=block_lines,
            )
        )
    logger.info("Parsed script into %d sections (thumbnail: %s)", len(sections), thumbnail_title or "N/A")
    return ScriptDocument(thumbnail_title=thumbnail_title, sections=sections)
