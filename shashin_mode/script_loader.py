"""Script loader for shashin_mode."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from textwrap import wrap
from typing import List, Optional, Tuple

from logging_utils import get_logger

logger = get_logger(__name__)


_OPENVERSE_DOUBLE_PATTERN = re.compile(r"openverse\"\"([^\"]+)\"\"", re.IGNORECASE)
_OPENVERSE_SINGLE_PATTERN = re.compile(r"openverse\"([^\"]+)\"", re.IGNORECASE)
_THUMBNAIL_PATTERN = re.compile(r"(s1|s2|subs|mains)\"{1,2}([^\"]+)\"+", re.IGNORECASE)


@dataclass
class SubtitleChunk:
    index: int
    lines: List[str]
    openverse_query: Optional[str] = None

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def query_text(self) -> str:
        return self.lines[0] if self.lines else ""


@dataclass
class ScriptDocument:
    chunks: List[SubtitleChunk]
    shared_openverse_query: Optional[str] = None
    thumbnail_title: Optional[str] = None
    thumbnail_banner: Optional[str] = None


def load_script(path: Path | str, *, wrap_chars: Optional[int] = None) -> ScriptDocument:
    script_path = Path(path).expanduser().resolve()
    if not script_path.exists():
        raise FileNotFoundError(f"Script file not found: {script_path}")

    raw_text = script_path.read_text(encoding="utf-8").strip()
    if not raw_text:
        raise ValueError("Script file is empty")

    blocks: List[List[str]] = []
    current: List[str] = []
    thumbnail_title: Optional[str] = None
    thumbnail_banner: Optional[str] = None
    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped:
            match = _THUMBNAIL_PATTERN.match(stripped)
            if match:
                token_label, token_text = match.groups()
                normalized_label = token_label.lower()
                clean_text = token_text.strip()
                if not clean_text:
                    continue
                if normalized_label in ("s1", "subs") and not thumbnail_title:
                    thumbnail_title = clean_text
                elif normalized_label in ("s2", "mains") and not thumbnail_banner:
                    thumbnail_banner = clean_text
                continue
            current.append(line.rstrip())
        else:
            if current:
                blocks.append(current)
                current = []
    if current:
        blocks.append(current)

    chunks: List[SubtitleChunk] = []
    pending_openverse: Optional[str] = None
    shared_openverse_query: Optional[str] = None
    for idx, block in enumerate(blocks, start=1):
        normalized: List[str] = []
        openverse_query: Optional[str] = pending_openverse
        pending_openverse = None
        for raw_line in block:
            line = raw_line.strip()
            if not line:
                continue
            line, extracted_query = _strip_openverse_marker(line)
            if extracted_query:
                openverse_query = extracted_query
            if not line:
                continue
            if wrap_chars and len(line) > wrap_chars:
                normalized.extend([wrapped for wrapped in wrap(line, wrap_chars) if wrapped.strip()])
            else:
                normalized.append(line)
        if not normalized:
            if openverse_query:
                shared_openverse_query = openverse_query
                pending_openverse = openverse_query
            continue
        chunks.append(SubtitleChunk(index=idx, lines=normalized, openverse_query=openverse_query))

    logger.info("Loaded script: %d subtitle chunks", len(chunks))
    return ScriptDocument(
        chunks=chunks,
        shared_openverse_query=shared_openverse_query,
        thumbnail_title=thumbnail_title,
        thumbnail_banner=thumbnail_banner,
    )


def _strip_openverse_marker(line: str) -> Tuple[str, Optional[str]]:
    """Remove openverse"" style markers from a line and return (clean_line, query)."""
    text = line
    extracted: Optional[str] = None

    patterns = (_OPENVERSE_DOUBLE_PATTERN, _OPENVERSE_SINGLE_PATTERN)
    # Remove multiple markers if present, latest one wins (closest to the spoken text)
    while True:
        matched = False
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                candidate = match.group(1).strip()
                if candidate:
                    extracted = candidate
                text = pattern.sub("", text, count=1).strip()
                matched = True
                break
        if not matched:
            break

    return text.strip(), extracted

