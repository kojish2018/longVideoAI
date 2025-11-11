from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config_loader import load_config
from logging_utils import configure_logging, get_logger

from .pipeline import PresentationPipeline
from .script_loader import load_presentation_script
from .youtube_profiles import apply_youtube_channel_profile, resolve_publish_at_string
from .youtube_uploader_adapter import upload_presentation_video

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Presentation-mode video generator")
    parser.add_argument("script", help="Path to presentation JSON script")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML configuration (default: config.yaml)",
    )
    parser.add_argument(
        "--output-dir",
        help="Override output directory defined in config.yaml",
    )
    parser.add_argument(
        "--print-plan",
        action="store_true",
        help="Print generated plan.json to stdout after completion",
    )
    parser.add_argument(
        "--thumbnail-path",
        help="Path to an existing thumbnail image to reuse (copied into run directory)",
    )
    parser.add_argument(
        "--thumbnail-copy-name",
        help="Filename to use for the copied thumbnail (default: thumbnail_<run_id>.png)",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload the generated video to YouTube",
    )
    parser.add_argument(
        "--publish-at",
        help=(
            "Schedule publish time (RFC3339 like 2025-10-01T12:30:00+09:00 or local 'YYYY-MM-DD HH:MM')"
        ),
    )
    parser.add_argument(
        "--youtube-channel",
        default="fire",
        help="YouTube channel profile to use for credentials (default: fire)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.output_dir:
        output_override = Path(args.output_dir).expanduser().resolve()
        output_override.mkdir(parents=True, exist_ok=True)
        config.output_dir = output_override

    channel_profile = apply_youtube_channel_profile(config=config, requested_channel=args.youtube_channel)
    logger.info("Using YouTube channel profile: %s", channel_profile)

    configure_logging(level=config.logging_level, log_file=config.log_file)

    script = load_presentation_script(args.script)
    pipeline = PresentationPipeline(config)
    result = pipeline.run(
        script,
        thumbnail_source=args.thumbnail_path,
        thumbnail_name=args.thumbnail_copy_name,
    )

    logger.info("Presentation video created: %s", result.video_path)

    if args.upload:
        publish_at = resolve_publish_at_string(args.publish_at, config=config)
        upload_presentation_video(
            config=config,
            script=script,
            result=result,
            publish_at=publish_at,
        )

    if args.print_plan:
        try:
            print(result.plan_path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - user convenience
            logger.error("Failed to read plan file: %s", exc)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

