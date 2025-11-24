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
_ICRAWLER_DOUBLE_PATTERN = re.compile(r"icrawler\"\"([^\"]+)\"\"", re.IGNORECASE)
_ICRAWLER_SINGLE_PATTERN = re.compile(r"icrawler\"([^\"]+)\"", re.IGNORECASE)
_THUMBNAIL_PATTERN = re.compile(r"(s1|s2|subs|mains)\"{1,2}([^\"]+)\"+", re.IGNORECASE)


@dataclass
class SubtitleChunk:
    index: int
    lines: List[str]
    openverse_query: Optional[str] = None
    icrawler_query: Optional[str] = None

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
    shared_icrawler_query: Optional[str] = None
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
    pending_icrawler: Optional[str] = None
    shared_openverse_query: Optional[str] = None
    shared_icrawler_query: Optional[str] = None
    for idx, block in enumerate(blocks, start=1):
        normalized: List[str] = []
        openverse_query: Optional[str] = pending_openverse
        icrawler_query: Optional[str] = pending_icrawler
        pending_openverse = None
        pending_icrawler = None
        for raw_line in block:
            line = raw_line.strip()
            if not line:
                continue
            line, extracted_query, provider = _strip_image_marker(line)
            if extracted_query:
                if provider == "openverse":
                    openverse_query = extracted_query
                elif provider == "icrawler":
                    icrawler_query = extracted_query
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
            if icrawler_query:
                shared_icrawler_query = icrawler_query
                pending_icrawler = icrawler_query
            continue
        chunks.append(SubtitleChunk(index=idx, lines=normalized, openverse_query=openverse_query, icrawler_query=icrawler_query))

    logger.info("Loaded script: %d subtitle chunks", len(chunks))
    return ScriptDocument(
        chunks=chunks,
        shared_openverse_query=shared_openverse_query,
        shared_icrawler_query=shared_icrawler_query,
        thumbnail_title=thumbnail_title,
        thumbnail_banner=thumbnail_banner,
    )


def _strip_image_marker(line: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Remove openverse"" or icrawler"" style markers from a line and return (clean_line, query, provider)."""
    text = line
    extracted: Optional[str] = None
    provider: Optional[str] = None

    # Check for icrawler markers first, then openverse markers
    # Latest marker wins (closest to the spoken text)
    all_patterns = [
        (_ICRAWLER_DOUBLE_PATTERN, "icrawler"),
        (_ICRAWLER_SINGLE_PATTERN, "icrawler"),
        (_OPENVERSE_DOUBLE_PATTERN, "openverse"),
        (_OPENVERSE_SINGLE_PATTERN, "openverse"),
    ]
    
    while True:
        matched = False
        for pattern, prov in all_patterns:
            match = pattern.search(text)
            if match:
                candidate = match.group(1).strip()
                if candidate:
                    extracted = candidate
                    provider = prov
                text = pattern.sub("", text, count=1).strip()
                matched = True
                break
        if not matched:
            break

    return text.strip(), extracted, provider

