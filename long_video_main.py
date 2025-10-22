"""Command line entry for the long-form video pipeline."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from config_loader import AppConfig, load_config
from logging_utils import configure_logging, get_logger
from long_pipeline import LongFormPipeline, PipelineResult
from script_parser import ScriptDocument, parse_script
from youtube_uploader import YouTubeUploader

logger = get_logger(__name__)


VOICE_CREDIT_LINE = "VOICEVOX: 青山龍星"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Long-form video generation pipeline")
    parser.add_argument("script", help="Path to the input script file")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--output-dir",
        help="Override output directory (if omitted uses config setting)",
    )
    parser.add_argument(
        "--print-plan",
        action="store_true",
        help="Print the generated plan.json payload to stdout",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload the rendered video to YouTube",
    )
    parser.add_argument(
        "--publish-at",
        help=(
            "Schedule publish time. Accepts RFC3339 (e.g. 2025-10-01T12:30:00-04:00) "
            "or local time 'YYYY-MM-DD HH:MM'."
        ),
    )
    parser.add_argument(
        "--type",
        dest="overlay_type",
        choices=["static", "typing"],
        default="static",
        help="Text overlay style: static (default) or typing",
    )
    parser.add_argument(
        "--typing-speed",
        dest="typing_speed",
        type=float,
        default=2.0,
        help="Typing speed multiplier (>1.0 is faster). Default 2.0 when --type typing",
    )
    return parser


def _maybe_upload_to_youtube(
    *,
    config: AppConfig,
    document: ScriptDocument,
    result: PipelineResult,
    publish_at: Optional[str],
) -> Optional[str]:
    """Render 結果を元に YouTube アップロードを実行する。"""

    uploader = YouTubeUploader(config=config.raw, credentials_dir=config.credentials_dir)
    if not uploader.authenticate():
        logger.error("YouTube 認証に失敗したためアップロードをスキップします。")
        return None

    title = _extract_title_from_result(result, document)
    description, youtube_tags = _prepare_description_and_tags(
        config=config,
        document=document,
        result=result,
        title=title,
    )

    thumbnail_path = result.thumbnail_path
    if thumbnail_path is None:
        logger.warning("生成されたサムネイルが見つかりませんでした。サムネイル設定をスキップします。")

    if publish_at:
        logger.info("YouTube スケジュール投稿 (UTC): %s", publish_at)

    video_id = uploader.upload(
        video_path=result.video_path,
        title=title,
        description=description,
        tags=youtube_tags,
        publish_at=publish_at,
        thumbnail_path=thumbnail_path,
    )

    if video_id:
        logger.info("YouTube へのアップロードが完了しました: https://www.youtube.com/watch?v=%s", video_id)
    else:
        logger.error("YouTube へのアップロードに失敗しました。")
    return video_id


def _prepare_description_and_tags(
    *,
    config: AppConfig,
    document: ScriptDocument,
    result: PipelineResult,
    title: str,
) -> tuple[str, List[str]]:
    """Compose YouTube description and tags from script metadata with fallbacks."""

    script_tags = []
    if document.tags:
        for tag in document.tags:
            cleaned = tag.strip().lstrip('#')
            if cleaned:
                script_tags.append(cleaned)

    youtube_tags = script_tags

    script_description = (document.description or '').strip()
    if script_description:
        base_description = script_description
    else:
        base_description = _build_description(config, title, result)

    sections: List[str] = [base_description]
    if youtube_tags:
        hashtag_line = " ".join(f"#{tag}" for tag in youtube_tags)
        if hashtag_line:
            sections.append(hashtag_line)

    if VOICE_CREDIT_LINE not in base_description:
        sections.append(VOICE_CREDIT_LINE)

    description = "\n\n".join(part for part in sections if part)
    return description, youtube_tags


def _extract_title_from_result(
    result: PipelineResult, document: ScriptDocument
) -> str:
    """最初の字幕テキストを抽出しタイトルとして返す。"""

    for scene in result.scenes:
        for segment in scene.text_segments:
            caption = " ".join(line.strip() for line in segment.lines if line.strip())
            if caption:
                return caption[:100]

    if document.sections:
        for line in document.sections[0].lines:
            line = line.strip()
            if line:
                return line[:100]

    if document.thumbnail_title:
        return document.thumbnail_title[:100]

    return "AI Generated Video"


def _build_description(
    config: AppConfig, title: str, result: PipelineResult
) -> str:
    """設定テンプレートを用いて説明文を組み立てる。"""

    youtube_cfg = config.raw.get("youtube", {}) if isinstance(config.raw, dict) else {}
    duration_minutes = int(result.total_duration // 60)
    duration_seconds = int(result.total_duration % 60)
    base_description = title

    template = youtube_cfg.get("description_template")
    if not template:
        return base_description

    try:
        rendered = template.format(
            title=title,
            description=base_description,
            duration_seconds=int(result.total_duration),
        )
    except KeyError as exc:
        logger.warning("description_template の展開に必要なキーが不足しています: %s", exc)
        return template

    return rendered.strip() or base_description


def _resolve_publish_at_string(raw_value: Optional[str], config: AppConfig) -> Optional[str]:
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
        logger.error("Use RFC3339 (2025-10-01T21:30:00-04:00) or local 'YYYY-MM-DD HH:MM'.")
        return None

    localised = naive.replace(tzinfo=tzinfo)
    return _to_rfc3339(localised)


def _parse_iso_datetime(value: str, fallback_tz) -> Optional[str]:
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(args.config, project_root=Path.cwd())

    # Runtime override: expose overlay type to renderer via config
    try:
        overlay_cfg = dict(config.raw.get("overlay", {})) if isinstance(config.raw, dict) else {}
        overlay_cfg["type"] = args.overlay_type
        overlay_cfg["typing_speed"] = float(args.typing_speed or 1.0)
        config.raw["overlay"] = overlay_cfg
    except Exception:
        # Fallback defensively; keep pipeline running even if config is not a dict
        pass

    if args.output_dir:
        override = Path(args.output_dir).expanduser().resolve()
        override.mkdir(parents=True, exist_ok=True)
        config.output_dir = override
        config.raw.setdefault("output", {})["directory"] = str(override)

    configure_logging(config.logging_level, config.log_file)

    logger.info("Loading script: %s", args.script)
    document = parse_script(args.script)

    pipeline = LongFormPipeline(config)
    result = pipeline.run(document)

    youtube_video_id: Optional[str] = None

    resolved_publish_at: Optional[str] = None
    if args.upload:
        resolved_publish_at = _resolve_publish_at_string(args.publish_at, config)
        youtube_video_id = _maybe_upload_to_youtube(
            config=config,
            document=document,
            result=result,
            publish_at=resolved_publish_at,
        )

    logger.info("Plan saved to: %s", result.plan_file)
    logger.info("Timeline saved to: %s", result.timeline_file)

    if args.print_plan:
        print(result.plan_file.read_text(encoding="utf-8"))

    summary = {
        "run_id": result.run_id,
        "output_dir": str(result.output_dir),
        "video_path": str(result.video_path),
        "total_duration_seconds": result.total_duration,
        "scene_count": len(result.scenes),
    }
    if youtube_video_id:
        summary["youtube_video_id"] = youtube_video_id
        summary["youtube_url"] = f"https://www.youtube.com/watch?v={youtube_video_id}"
    if args.upload and args.publish_at:
        summary["requested_publish_at"] = args.publish_at
    if args.upload and resolved_publish_at:
        summary["scheduled_publish_at_utc"] = resolved_publish_at
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
