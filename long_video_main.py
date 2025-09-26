"""Command line entry for the long-form video pipeline."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from config_loader import load_config
from logging_utils import configure_logging, get_logger
from long_pipeline import LongFormPipeline
from script_parser import parse_script

logger = get_logger(__name__)


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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(args.config, project_root=Path.cwd())

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
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
