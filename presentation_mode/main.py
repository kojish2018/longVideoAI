from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from config_loader import load_config
from logging_utils import configure_logging, get_logger

from .pipeline import PresentationPipeline
from .script_loader import load_presentation_script

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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.output_dir:
        output_override = Path(args.output_dir).expanduser().resolve()
        output_override.mkdir(parents=True, exist_ok=True)
        config.output_dir = output_override

    configure_logging(level=config.logging_level, log_file=config.log_file)

    script = load_presentation_script(args.script)
    pipeline = PresentationPipeline(config)
    result = pipeline.run(script)

    logger.info("Presentation video created: %s", result.video_path)

    if args.print_plan:
        try:
            print(result.plan_path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - user convenience
            logger.error("Failed to read plan file: %s", exc)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

