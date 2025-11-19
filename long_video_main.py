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
from sns_shorts_posts.shorts_orchestrator import generate_shorts_for_run

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
    parser.add_argument(
        "--thumbnail-style",
        choices=["style1", "style2"],
        help="Select thumbnail design style (style1=classic, style2=bold pop).",
    )
    parser.add_argument(
        "--with-shorts",
        action="store_true",
        help=(
            "After rendering the long video, automatically build vertical shorts for all %%START/%%END blocks in the script."
        ),
    )
    parser.add_argument(
        "--voicevox-speaker",
        type=int,
        help="Override VOICEVOX speaker ID (example: 3 for Zundamon).",
    )
    parser.add_argument(
        "--voicevox-profile",
        help=(
            "Select a VOICEVOX profile defined under apis.voicevox_profiles in config.yaml. "
            "Falls back to the profile configured in config or 'default'."
        ),
    )
    parser.add_argument(
        "--background-music",
        help=(
            "Select background music filename. You can omit the .mp3 extension; "
            "defaults to the configured value (Fulero.mp3)."
        ),
    )
    parser.add_argument(
        "--image-provider",
        choices=["pollinations", "deepinfra"],
        help=(
            "Select image generation provider. Defaults to the value in config.yaml (pollinations if omitted)."
        ),
    )
    parser.add_argument(
        "--image-base-prompt",
        help=(
            "Override the default Pollinations prompt (config.simple_mode.default_image_prompt)."
        ),
    )
    parser.add_argument(
        "--youtube-channel",
        help=(
            "Select YouTube channel profile to use for credentials. "
            "Defaults to the profile configured in config.yaml or 'default'."
        ),
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


def _apply_youtube_channel_profile(
    *, config: AppConfig, requested_channel: Optional[str]
) -> str:
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

    return channel_name


def _build_channel_profiles(youtube_cfg: dict) -> dict:
    profiles = {key: dict(value) for key, value in DEFAULT_YOUTUBE_CHANNEL_PROFILES.items()}

    user_profiles = youtube_cfg.get("channel_profiles") if isinstance(youtube_cfg, dict) else {}
    if isinstance(user_profiles, dict):
        for name, profile in user_profiles.items():
            if not isinstance(profile, dict):
                continue
            base = profiles.get(name, {})
            merged = dict(base)
            merged.update({k: v for k, v in profile.items() if v is not None})
            profiles[name] = merged

    return profiles


def _normalise_bgm_name(value: str) -> str:
    trimmed = str(value).strip()
    if not trimmed:
        raise ValueError("Background music name cannot be empty.")
    if not trimmed.lower().endswith(".mp3"):
        trimmed = f"{trimmed}.mp3"
    return trimmed


def _apply_background_music(*, config: AppConfig, override: Optional[str]) -> str:
    if not isinstance(config.raw, dict):
        raise ValueError("Configuration root must be a mapping to select background music.")

    bgm_cfg = config.raw.setdefault("bgm", {})
    if not isinstance(bgm_cfg, dict):
        bgm_cfg = {}
        config.raw["bgm"] = bgm_cfg

    directory = str(bgm_cfg.get("directory") or "background_music").strip()
    if not directory:
        directory = "background_music"
    bgm_cfg["directory"] = directory

    if override:
        selected = _normalise_bgm_name(override)
    else:
        selected_raw = bgm_cfg.get("selected") or "Fulero.mp3"
        selected = _normalise_bgm_name(str(selected_raw))

    bgm_cfg["selected"] = selected
    return selected


def _apply_image_provider(*, config: AppConfig, override: Optional[str]) -> str:
    if not isinstance(config.raw, dict):
        raise ValueError("Configuration root must be a mapping to select image provider.")

    apis_cfg = config.raw.setdefault("apis", {})
    if not isinstance(apis_cfg, dict):
        apis_cfg = {}
        config.raw["apis"] = apis_cfg

    allowed = {"pollinations", "deepinfra"}
    provider_raw = override or apis_cfg.get("image_provider") or "pollinations"
    provider = str(provider_raw).strip().lower()
    if provider not in allowed:
        known = ", ".join(sorted(allowed))
        raise ValueError(
            f"Unknown image provider '{provider}'. Supported providers: {known}."
        )

    apis_cfg["image_provider"] = provider
    return provider


def _apply_image_prompt_override(*, config: AppConfig, prompt_text: str) -> str:
    if not isinstance(config.raw, dict):
        raise ValueError("Configuration root must be a mapping to override image prompt.")

    trimmed = str(prompt_text).strip()
    if not trimmed:
        raise ValueError("--image-base-prompt cannot be empty.")

    simple_cfg = config.raw.setdefault("simple_mode", {})
    if not isinstance(simple_cfg, dict):
        simple_cfg = {}
        config.raw["simple_mode"] = simple_cfg

    simple_cfg["default_image_prompt"] = trimmed

    constants = simple_cfg.setdefault("prompt_constants", {})
    if not isinstance(constants, dict):
        constants = {}
        simple_cfg["prompt_constants"] = constants
    constants["style"] = trimmed

    return trimmed


def _apply_voicevox_profile(
    *, config: AppConfig, requested_profile: Optional[str], speaker_override: Optional[int]
) -> tuple[str, int]:
    if not isinstance(config.raw, dict):
        raise ValueError("Configuration root must be a mapping to select VOICEVOX profile.")

    apis_cfg = config.raw.setdefault("apis", {})
    if not isinstance(apis_cfg, dict):
        raise ValueError("'apis' section must be a mapping to configure VOICEVOX.")

    voice_cfg = apis_cfg.setdefault("voicevox", {})
    if not isinstance(voice_cfg, dict):
        voice_cfg = {}
        apis_cfg["voicevox"] = voice_cfg

    base_profile = {
        key: value
        for key, value in voice_cfg.items()
        if key not in {"profile"}
    }

    profiles: dict[str, dict] = {"default": dict(base_profile)}
    profiles_cfg = apis_cfg.get("voicevox_profiles")
    if isinstance(profiles_cfg, dict):
        for name, profile in profiles_cfg.items():
            if not isinstance(profile, dict):
                continue
            merged = dict(base_profile)
            merged.update({k: v for k, v in profile.items() if v is not None})
            profiles[name] = merged

    profile_name = (requested_profile or voice_cfg.get("profile") or "default").strip()
    if not profile_name:
        profile_name = "default"

    if profile_name not in profiles:
        known = ", ".join(sorted(profiles.keys())) or "(none)"
        raise ValueError(
            f"Unknown VOICEVOX profile '{profile_name}'. Known profiles: {known}. "
            "Define profiles under apis.voicevox_profiles in config.yaml or use a supported name."
        )

    selected_profile = profiles[profile_name]
    voice_cfg.update(selected_profile)
    voice_cfg["profile"] = profile_name

    if speaker_override is not None:
        try:
            voice_cfg["speaker_id"] = int(speaker_override)
        except (TypeError, ValueError) as exc:
            raise ValueError("--voicevox-speaker must be an integer speaker ID") from exc

    if "speaker_id" not in voice_cfg:
        raise ValueError(f"VOICEVOX profile '{profile_name}' must define speaker_id.")

    speaker_id = int(voice_cfg["speaker_id"])
    return profile_name, speaker_id


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    bgm_override: Optional[str] = None
    if getattr(args, "background_music", None):
        try:
            bgm_override = _normalise_bgm_name(args.background_music)
        except ValueError as exc:
            parser.error(str(exc))


    # fireチャンネル指定時のデフォルト値自動適用
    if getattr(args, "youtube_channel", None) == "fire":
        if getattr(args, "voicevox_speaker", None) is None:
            args.voicevox_speaker = 8
        if not hasattr(args, "overlay_type") or args.overlay_type is None or args.overlay_type == "static":
            args.overlay_type = "typing"
        if getattr(args, "thumbnail_style", None) is None:
            args.thumbnail_style = "style2"
        if bgm_override is None:
            bgm_override = "Alge.mp3"

    config = load_config(args.config, project_root=Path.cwd())

    try:
        selected_channel = _apply_youtube_channel_profile(
            config=config,
            requested_channel=args.youtube_channel,
        )
        logger.info("YouTube channel profile: %s", selected_channel)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        voice_profile, speaker_id = _apply_voicevox_profile(
            config=config,
            requested_profile=args.voicevox_profile,
            speaker_override=args.voicevox_speaker,
        )
        logger.info("VOICEVOX profile resolved: %s (speaker_id=%s)", voice_profile, speaker_id)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        selected_bgm = _apply_background_music(
            config=config,
            override=bgm_override,
        )
        logger.info("Background music selected: %s", selected_bgm)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        provider_name = _apply_image_provider(
            config=config,
            override=getattr(args, "image_provider", None),
        )
        logger.info("Image provider selected: %s", provider_name)
    except ValueError as exc:
        parser.error(str(exc))

    if getattr(args, "image_base_prompt", None) is not None:
        try:
            applied_prompt = _apply_image_prompt_override(
                config=config,
                prompt_text=args.image_base_prompt,
            )
            logger.info("Image base prompt override applied: %s", applied_prompt)
        except ValueError as exc:
            parser.error(str(exc))

    if args.thumbnail_style:
        try:
            thumb_cfg = dict(config.raw.get("thumbnail", {})) if isinstance(config.raw, dict) else {}
            thumb_cfg["style"] = args.thumbnail_style
            config.raw.setdefault("thumbnail", {})
            config.raw["thumbnail"].update(thumb_cfg)
        except Exception:
            logger.warning("Failed to apply --thumbnail-style override", exc_info=True)

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

    shorts_summary = None
    if getattr(args, "with_shorts", False):
        try:
            shorts_summary = generate_shorts_for_run(
                script_path=Path(args.script).expanduser().resolve(),
                run_dir=result.output_dir,
                layout_path=Path("sns_shorts_posts/layouts/vertical_v1.json"),
                output_dir=Path("output/shorts/ready"),
                work_dir=Path("output/shorts/work"),
                execute=True,
            )
            logger.info(
                "Shorts generated: %s items (manifest: %s)",
                shorts_summary.get("count"),
                shorts_summary.get("manifest"),
            )
        except Exception:
            logger.exception("Short generation failed; continuing without shorts.")

    summary = {
        "run_id": result.run_id,
        "output_dir": str(result.output_dir),
        "video_path": str(result.video_path),
        "total_duration_seconds": result.total_duration,
        "scene_count": len(result.scenes),
    }
    if shorts_summary:
        summary["shorts_manifest"] = shorts_summary.get("manifest")
        summary["shorts_count"] = shorts_summary.get("count")
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
