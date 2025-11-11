from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from config_loader import AppConfig
from logging_utils import get_logger
from youtube_uploader import YouTubeUploader

from .models import PresentationScript

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from .pipeline import PresentationResult

logger = get_logger(__name__)


def upload_presentation_video(
    *,
    config: AppConfig,
    script: PresentationScript,
    result: "PresentationResult",
    publish_at: Optional[str],
) -> Optional[str]:
    """Upload a rendered presentation video to YouTube."""

    uploader = YouTubeUploader(config=config.raw, credentials_dir=config.credentials_dir)
    if not uploader.authenticate():
        logger.error("YouTube authentication failed; skipping upload.")
        return None

    title = _resolve_title(script, result)
    description, tags = _prepare_description_and_tags(config=config, script=script, result=result, title=title)

    thumbnail_path = result.thumbnail_path
    video_id = uploader.upload(
        video_path=result.video_path,
        title=title,
        description=description,
        tags=tags if tags else None,
        publish_at=publish_at,
        thumbnail_path=thumbnail_path,
    )

    if video_id:
        logger.info("YouTube upload successful: https://www.youtube.com/watch?v=%s", video_id)
    else:
        logger.error("YouTube upload failed. Check logs for details.")
    return video_id


def _resolve_title(script: PresentationScript, result: "PresentationResult") -> str:
    if script.title and script.title.strip():
        return script.title.strip()[:100]
    return result.run_id


def _prepare_description_and_tags(
    *,
    config: AppConfig,
    script: PresentationScript,
    result: "PresentationResult",
    title: str,
) -> tuple[str, List[str]]:
    base_description = (script.description or title).strip()

    youtube_cfg = config.raw.get("youtube", {}) if isinstance(config.raw, dict) else {}
    description_text = base_description

    if isinstance(youtube_cfg, dict):
        template = youtube_cfg.get("description_template")
        if template:
            try:
                rendered = template.format(
                    title=title,
                    description=base_description,
                    duration_seconds=int(result.total_duration),
                )
                if rendered.strip():
                    description_text = rendered.strip()
            except KeyError as exc:
                logger.warning("description_template is missing keys: %s", exc)

    tags = _normalise_tags(script.tags)

    sections = [description_text]
    if tags:
        hashtag_line = " ".join(f"#{tag}" for tag in tags if tag)
        if hashtag_line:
            sections.append(hashtag_line)

    description = "\n\n".join(section for section in sections if section)
    return description, tags


def _normalise_tags(tags) -> List[str]:
    normalised: List[str] = []
    if not tags:
        return normalised
    for tag in tags:
        if not isinstance(tag, str):
            continue
        cleaned = tag.strip().lstrip("#")
        if cleaned:
            normalised.append(cleaned[:60])
    return normalised
