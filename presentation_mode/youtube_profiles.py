from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from config_loader import AppConfig
from logging_utils import get_logger

logger = get_logger(__name__)

DEFAULT_YOUTUBE_CHANNEL_PROFILES: Dict[str, Dict[str, str]] = {
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


def apply_youtube_channel_profile(*, config: AppConfig, requested_channel: Optional[str]) -> str:
    """Resolve YouTube channel profile and update config to point to correct credentials."""

    if not isinstance(config.raw, dict):
        raise ValueError("Configuration root must be a mapping to select YouTube channel.")

    youtube_cfg = config.raw.setdefault("youtube", {})
    if not isinstance(youtube_cfg, dict):
        youtube_cfg = {}
        config.raw["youtube"] = youtube_cfg

    channel_profiles = _build_channel_profiles(youtube_cfg)

    fallback_channel = youtube_cfg.get("channel") or youtube_cfg.get("default_channel") or "default"
    channel_name = (requested_channel or fallback_channel or "default").strip()
    if not channel_name:
        channel_name = "default"

    profile = channel_profiles.get(channel_name)
    if not profile:
        known = ", ".join(sorted(channel_profiles.keys())) or "(none)"
        raise ValueError(
            f"Unknown YouTube channel profile '{channel_name}'. Known profiles: {known}. "
            "Update config.yaml youtube.channel_profiles to include this profile."
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

    youtube_cfg["credentials_dir"] = credentials_dir_name
    youtube_cfg["credentials_file"] = credentials_file
    youtube_cfg["token_file"] = token_file
    youtube_cfg["channel"] = channel_name

    logger.debug(
        "YouTube channel profile resolved: channel=%s, credentials_dir=%s, credentials_file=%s, token_file=%s",
        channel_name,
        credentials_dir,
        credentials_file,
        token_file,
    )
    return channel_name


def resolve_publish_at_string(raw_value: Optional[str], *, config: AppConfig) -> Optional[str]:
    """Convert CLI-input schedule strings into RFC3339 (UTC) or return None."""

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
        logger.error("Use RFC3339 (2025-10-01T21:30:00+09:00) or local 'YYYY-MM-DD HH:MM'.")
        return None

    localised = naive.replace(tzinfo=tzinfo)
    return _to_rfc3339(localised)


def _build_channel_profiles(youtube_cfg: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    profiles: Dict[str, Dict[str, str]] = {
        key: dict(value) for key, value in DEFAULT_YOUTUBE_CHANNEL_PROFILES.items()
    }

    user_profiles = youtube_cfg.get("channel_profiles") if isinstance(youtube_cfg, dict) else {}
    if isinstance(user_profiles, dict):
        for name, profile in user_profiles.items():
            if not isinstance(profile, dict):
                continue
            merged = dict(profiles.get(name, {}))
            merged.update({k: v for k, v in profile.items() if v is not None})
            profiles[name] = merged

    return profiles


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
    except Exception:  # pragma: no cover - fallback safety
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
