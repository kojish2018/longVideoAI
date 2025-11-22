"""YouTube upload helpers for shashin_mode."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from config_loader import AppConfig
from logging_utils import get_logger

from .script_loader import ScriptDocument

logger = get_logger(__name__)

VOICE_CREDIT_LINE = "VOICEVOX: 青山龍星"


DEFAULT_YOUTUBE_CHANNEL_PROFILES = {
    "default": {
        "credentials_dir": "credentials",
        "credentials_file": "youtube_credentials.json",
        "token_file": "youtube_token.json",
    },
    "fire": {
        "credentials_dir": "credentials_fire",
        "credentials_file": "credentials-fire.json",
        "token_file": "youtube_token_fire.json",
    },
}


@dataclass
class YouTubeMetadata:
    title: str
    description: str
    tags: List[str]


def apply_youtube_channel_profile(*, config: AppConfig, requested_channel: Optional[str]) -> str:
    if not isinstance(config.raw, dict):
        raise ValueError("Configuration root must be a mapping to select YouTube channel.")

    youtube_cfg = config.raw.setdefault("youtube", {})
    if not isinstance(youtube_cfg, dict):
        youtube_cfg = {}
        config.raw["youtube"] = youtube_cfg

    profiles = _build_channel_profiles(youtube_cfg)

    fallback_channel = youtube_cfg.get("channel") or youtube_cfg.get("default_channel") or "default"
    channel_name = (requested_channel or fallback_channel or "default").strip() or "default"

    profile = profiles.get(channel_name)
    if not profile:
        known = ", ".join(sorted(profiles.keys())) or "(none)"
        raise ValueError(
            f"Unknown YouTube channel profile '{channel_name}'. Known profiles: {known}. "
            "Define profiles under youtube.channel_profiles in config.yaml or use a supported name."
        )

    credentials_dir_name = profile.get("credentials_dir")
    if not credentials_dir_name:
        raise ValueError(f"Profile '{channel_name}' is missing 'credentials_dir'.")

    credentials_dir = (config.project_root / credentials_dir_name).resolve()
    config.credentials_dir = credentials_dir

    credentials_file = profile.get("credentials_file") or DEFAULT_YOUTUBE_CHANNEL_PROFILES["default"][
        "credentials_file"
    ]
    token_file = profile.get("token_file") or DEFAULT_YOUTUBE_CHANNEL_PROFILES["default"]["token_file"]

    youtube_cfg["credentials_file"] = credentials_file
    youtube_cfg["token_file"] = token_file
    youtube_cfg["channel"] = channel_name

    logger.info("YouTube channel profile resolved: %s", channel_name)
    return channel_name


def _build_channel_profiles(youtube_cfg: dict) -> dict:
    profiles = {key: dict(value) for key, value in DEFAULT_YOUTUBE_CHANNEL_PROFILES.items()}
    user_profiles = youtube_cfg.get("channel_profiles") if isinstance(youtube_cfg, dict) else {}
    if isinstance(user_profiles, dict):
        for name, profile in user_profiles.items():
            if not isinstance(profile, dict):
                continue
            merged = dict(profiles.get(name, {}))
            merged.update({k: v for k, v in profile.items() if v is not None})
            profiles[name] = merged
    return profiles


def resolve_publish_at_string(raw_value: Optional[str], config: AppConfig) -> Optional[str]:
    if raw_value is None:
        return None

    text = raw_value.strip()
    if not text:
        return None

    timezone_name: Optional[str] = None
    youtube_cfg = config.raw.get("youtube", {}) if isinstance(config.raw, dict) else {}
    if isinstance(youtube_cfg, dict):
        timezone_name = youtube_cfg.get("default_timezone")

    tzinfo = _resolve_timezone(timezone_name)

    iso_candidate = _parse_iso_datetime(text, tzinfo)
    if iso_candidate:
        return iso_candidate

    try:
        naive = datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        logger.error("Unsupported --publish-at format: %s", raw_value)
        logger.error("Use RFC3339 (2025-10-01T21:30:00-04:00) or local 'YYYY-MM-DD HH:MM'.")
        return None

    localised = naive.replace(tzinfo=tzinfo)
    return _to_rfc3339(localised)


def _parse_iso_datetime(value: str, fallback_tz):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=fallback_tz)
    return _to_rfc3339(dt)


def _resolve_timezone(name: Optional[str]):
    try:
        from zoneinfo import ZoneInfo  # type: ignore
    except ImportError:  # pragma: no cover
        logger.warning("zoneinfo module unavailable; falling back to local timezone.")
        return datetime.now().astimezone().tzinfo

    if name:
        try:
            return ZoneInfo(str(name))
        except Exception:
            logger.warning("Invalid timezone name in youtube.default_timezone: %s", name)

    try:
        return ZoneInfo("Asia/Tokyo")
    except Exception:  # pragma: no cover
        local = datetime.now().astimezone().tzinfo
        if local is not None:
            return local
        return ZoneInfo("UTC")


def _to_rfc3339(dt: datetime) -> str:
    try:
        from zoneinfo import ZoneInfo  # type: ignore
    except ImportError:  # pragma: no cover
        if dt.tzinfo is None:
            return dt.isoformat(timespec="seconds")
        return dt.astimezone().isoformat(timespec="seconds")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))

    dt_utc = dt.astimezone(ZoneInfo("UTC"))
    return dt_utc.isoformat(timespec="seconds").replace("+00:00", "Z")


def build_youtube_metadata(
    *,
    script_doc: ScriptDocument,
    config: AppConfig,
    total_duration: float,
    title_override: Optional[str],
    description_override: Optional[str],
    tags_override: Optional[str],
) -> YouTubeMetadata:
    title = (title_override or _extract_title_from_document(script_doc)).strip() or "AI Generated Video"

    if description_override:
        description = description_override.strip()
    else:
        description = _build_description(config, title, total_duration)

    tags = _resolve_tags_override(tags_override)
    return YouTubeMetadata(title=title, description=description, tags=tags)


def _extract_title_from_document(document: ScriptDocument) -> str:
    for chunk in document.chunks:
        for line in chunk.lines:
            cleaned = line.strip()
            if cleaned:
                return cleaned[:100]
    return "AI Generated Video"


def _build_description(config: AppConfig, title: str, total_duration: float) -> str:
    youtube_cfg = config.raw.get("youtube", {}) if isinstance(config.raw, dict) else {}
    duration_minutes = int(total_duration // 60)
    duration_seconds = int(total_duration % 60)
    base_description = title

    template = youtube_cfg.get("description_template") if isinstance(youtube_cfg, dict) else None
    if not template:
        if VOICE_CREDIT_LINE not in base_description:
            return f"{base_description}\n\n{VOICE_CREDIT_LINE}"
        return base_description

    try:
        rendered = template.format(
            title=title,
            description=base_description,
            duration_seconds=int(total_duration),
            duration_minutes=duration_minutes,
            duration_hhmm=f"{duration_minutes:02d}:{duration_seconds:02d}",
        )
    except KeyError as exc:
        logger.warning("description_template の展開に必要なキーが不足しています: %s", exc)
        rendered = template

    result = rendered.strip() or base_description
    if VOICE_CREDIT_LINE not in result:
        result = f"{result}\n\n{VOICE_CREDIT_LINE}"
    return result


def _resolve_tags_override(raw_value: Optional[str]) -> List[str]:
    if not raw_value:
        return []

    tokens = raw_value.replace("#", "").replace("\n", ",").split(",")
    tags: List[str] = []
    for token in tokens:
        cleaned = token.strip()
        if cleaned:
            tags.append(cleaned[:30])

    seen = set()
    unique: List[str] = []
    for tag in tags:
        lowered = tag.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(tag)
    return unique[:30]