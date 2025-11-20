"""Loader for Yukkuri dialogue JSON/JSONL scripts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from logging_utils import get_logger

from .dialogue_types import YukkuriScript, YukkuriUtterance

logger = get_logger(__name__)


def load_yukkuri_json(path: Path | str) -> YukkuriScript:
    """Read a JSON or JSONL file and convert it into a YukkuriScript."""

    script_path = Path(path).expanduser().resolve()
    if not script_path.exists():
        raise FileNotFoundError(f"Script file not found: {script_path}")

    raw_text = script_path.read_text(encoding="utf-8").strip()
    if not raw_text:
        raise ValueError("Script file is empty")

    data, is_jsonl = _parse_json_or_jsonl(raw_text)
    metadata, utterance_blocks = _separate_metadata_and_blocks(data)

    utterances: List[YukkuriUtterance] = []
    for idx, raw_utt in enumerate(utterance_blocks, start=1):
        utt = _normalise_utterance(raw_utt, idx)
        utterances.append(utt)

    if not utterances:
        raise ValueError("No utterances found in JSON script")

    title = _derive_title(metadata, utterances)
    tags = _normalise_tags(metadata.get("tags"))
    description = _normalize_optional_str(metadata.get("description"))
    thumb_prompt = _normalize_optional_str(
        metadata.get("thumbnail_image_prompt") or metadata.get("image_prompt")
    )

    logger.info(
        "Loaded yukkuri JSON%s: %s (utterances=%d, title=%s)",
        "L" if is_jsonl else "",
        script_path.name,
        len(utterances),
        title or "(empty)",
    )
    return YukkuriScript(
        source_path=script_path,
        title=title,
        utterances=utterances,
        tags=tags,
        description=description,
        thumbnail_image_prompt=thumb_prompt,
        raw_metadata=metadata,
    )


def _parse_json_or_jsonl(raw_text: str) -> Tuple[Any, bool]:
    """Try JSON first; if it fails, treat as JSONL."""

    try:
        return json.loads(raw_text), False
    except json.JSONDecodeError:
        pass

    blocks: List[Any] = []
    for line_no, line in enumerate(raw_text.splitlines(), start=1):
        striped = line.strip()
        if not striped:
            continue
        try:
            blocks.append(json.loads(striped))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL on line {line_no}: {exc}") from exc
    return blocks, True


def _separate_metadata_and_blocks(data: Any) -> Tuple[Dict[str, Any], List[Any]]:
    """Accept both array root and object root; extract utterance blocks."""

    if isinstance(data, list):
        return {}, data

    if not isinstance(data, dict):
        raise ValueError("Top-level JSON must be an array or object")

    metadata: Dict[str, Any] = {
        key: value
        for key, value in data.items()
        if key
        not in {"utterances", "dialogue", "lines", "scenes", "entries", "script"}
    }

    # Preferred keys in order
    for key in ("utterances", "dialogue", "lines", "entries", "script"):
        value = data.get(key)
        if isinstance(value, list):
            return metadata, value

    scenes = data.get("scenes")
    if isinstance(scenes, list):
        flattened: List[Any] = []
        for scene in scenes:
            if not isinstance(scene, dict):
                continue
            scene_meta = {k: v for k, v in scene.items() if k not in {"utterances", "dialogue", "lines"}}
            blocks = None
            for key in ("utterances", "dialogue", "lines"):
                candidate = scene.get(key)
                if isinstance(candidate, list):
                    blocks = candidate
                    break
            if blocks is None:
                # If the scene itself is an utterance-like mapping, keep it
                if "speaker" in scene and "text" in scene:
                    blocks = [scene]
                else:
                    continue
            for block in blocks:
                if isinstance(block, dict):
                    # Merge scene-level metadata into each utterance
                    merged = dict(scene_meta)
                    merged.update(block)
                    flattened.append(merged)
                else:
                    flattened.append(block)
        return metadata, flattened

    raise ValueError("No utterance array found. Expected keys: utterances/dialogue/lines/entries/script/scenes.")


def _normalise_utterance(raw: Any, idx: int) -> YukkuriUtterance:
    if not isinstance(raw, dict):
        raise ValueError(f"Utterance #{idx} must be an object, got {type(raw).__name__}")

    speaker = _normalize_optional_str(raw.get("speaker")) or raw.get("character")
    if speaker is None:
        raise ValueError(f"Utterance #{idx} is missing 'speaker'")
    text = _normalize_optional_str(raw.get("text")) or raw.get("line")
    if text is None:
        raise ValueError(f"Utterance #{idx} is missing 'text'")

    def _cast_float(value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    duration = _cast_float(raw.get("duration") or raw.get("duration_seconds"))
    start = _cast_float(raw.get("start"))
    end = _cast_float(raw.get("end"))

    emotion = _normalize_optional_str(raw.get("emotion"))
    bg_image = _normalize_optional_str(
        raw.get("bg_image") or raw.get("background") or raw.get("bg")
    )
    bg_prompt = _normalize_optional_str(raw.get("bg_prompt") or raw.get("prompt"))
    bg_reference = _normalize_optional_str(raw.get("bg_reference") or raw.get("background_reference"))
    bgm_cue = _normalize_optional_str(raw.get("bgm") or raw.get("bgm_cue"))
    se = _normalize_optional_str(raw.get("se") or raw.get("sound_effect"))
    layout_hint = _normalize_optional_str(raw.get("layout") or raw.get("layout_hint"))
    overlay_style = _normalize_optional_str(raw.get("style") or raw.get("overlay_style"))

    known_keys = {
        "speaker",
        "character",
        "text",
        "line",
        "emotion",
        "duration",
        "duration_seconds",
        "start",
        "end",
        "bg_image",
        "background",
        "bg_prompt",
        "prompt",
        "bg_reference",
        "background_reference",
        "bg",
        "bgm",
        "bgm_cue",
        "se",
        "sound_effect",
        "layout",
        "layout_hint",
        "style",
        "overlay_style",
    }
    extras = {k: v for k, v in raw.items() if k not in known_keys}

    return YukkuriUtterance(
        speaker=str(speaker),
        text=str(text),
        emotion=emotion,
        duration_seconds=duration,
        start=start,
        end=end,
        bg_image=bg_image,
        bg_prompt=bg_prompt,
        bg_reference=bg_reference,
        bgm_cue=bgm_cue,
        se=se,
        layout_hint=layout_hint,
        overlay_style=overlay_style,
        extras=extras,
    )


def _normalize_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _derive_title(metadata: Dict[str, Any], utterances: List[YukkuriUtterance]) -> str:
    explicit = metadata.get("title")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if utterances:
        head = utterances[0].text.strip()
        return head[:40]
    return ""


def _normalise_tags(raw_tags: Any) -> Optional[List[str]]:
    if raw_tags is None:
        return None
    if isinstance(raw_tags, str):
        parts = [part.strip() for part in raw_tags.replace("\n", ",").split(",")]
        tags = [p for p in parts if p]
        return tags or None
    if isinstance(raw_tags, list):
        tags = [str(item).strip() for item in raw_tags if str(item).strip()]
        return tags or None
    return None
