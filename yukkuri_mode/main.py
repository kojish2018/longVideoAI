"""CLI entrypoint for yukkuri_mode."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence

import yaml

from logging_utils import configure_logging, get_logger

from .json_script_loader import load_yukkuri_json
from .styles import YukkuriStyle, load_style_config
from .timeline_builder import TimelinePlan, build_timeline
from .video_renderer import RenderUnit, VideoRenderer
from .voice_adapter import VoiceResult, YukkuriVoiceAdapter

logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render yukkuri dialogue video from JSON/JSONL script.")
    parser.add_argument("--script", required=True, help="Path to script JSON or JSONL.")
    parser.add_argument("--config", default="yukkuri_mode/config.yaml", help="Path to yukkuri_mode config YAML.")
    parser.add_argument("--output", help="Output video path (.mp4). Defaults to output/yukkuri_mode/<timestamp>.mp4")
    parser.add_argument("--no-voice", action="store_true", help="Skip VOICEVOX synthesis and use silent durations.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and plan without rendering.")
    parser.add_argument("--dump-plan", action="store_true", help="Print timeline plan JSON for debugging.")
    return parser.parse_args()


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def ensure_output_dirs(config: Dict[str, Any]) -> Dict[str, Path]:
    output_cfg = config.get("output", {}) if isinstance(config, dict) else {}
    output_dir = Path(output_cfg.get("directory", "output/yukkuri_mode")).resolve()
    temp_dir = Path(output_cfg.get("temp_directory", "temp/yukkuri_mode")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    log_file = Path(config.get("logging", {}).get("file", "logs/yukkuri_mode.log")).resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    return {"output_dir": output_dir, "temp_dir": temp_dir, "log_file": log_file}


def safe_slug(text: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z一-龠ぁ-んァ-ヶー]+", "_", text).strip("_")
    return slug or "yukkuri_video"


def recalc_with_audio(
    plan: TimelinePlan,
    voice_results: Sequence[VoiceResult],
    *,
    inter_gap: float,
    padding: float,
    renderer: VideoRenderer,
) -> List[RenderUnit]:
    units: List[RenderUnit] = []
    cursor = 0.0
    for shot_plan, voice in zip(plan.shots, voice_results):
        final_duration = max(voice.duration + padding, shot_plan.duration, 0.01)
        shot_plan.start = cursor
        bgm_path = renderer.resolve_bgm_path(shot_plan)
        units.append(
            RenderUnit(
                plan=shot_plan,
                audio_path=voice.path,
                audio_duration=voice.duration,
                duration=final_duration,
                start=cursor,
                bgm_path=bgm_path,
            )
        )
        cursor += final_duration + inter_gap
    return units


def write_subtitles_srt(units: Sequence[RenderUnit], path: Path, style: YukkuriStyle) -> None:
    lines: List[str] = []
    for idx, unit in enumerate(units, start=1):
        start_ts = _fmt_ts(unit.start)
        end_ts = _fmt_ts(unit.start + unit.duration)
        speaker = style.characters.get(unit.plan.speaker_key)
        speaker_label = speaker.display_name if speaker else unit.plan.speaker_key
        content = f"{speaker_label}: {unit.plan.text}"
        lines.append(str(idx))
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(content)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_subtitles_vtt(units: Sequence[RenderUnit], path: Path, style: YukkuriStyle) -> None:
    lines: List[str] = ["WEBVTT", ""]
    for unit in units:
        start_ts = _fmt_ts(unit.start, sep=".")
        end_ts = _fmt_ts(unit.start + unit.duration, sep=".")
        speaker = style.characters.get(unit.plan.speaker_key)
        speaker_label = speaker.display_name if speaker else unit.plan.speaker_key
        content = f"{speaker_label}: {unit.plan.text}"
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(content)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _fmt_ts(seconds: float, sep: str = ",") -> str:
    millis = int(round(seconds * 1000))
    hours, rem = divmod(millis, 3600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"


def plan_to_dict(plan: TimelinePlan) -> Dict[str, Any]:
    def _shot_dict(shot) -> Dict[str, Any]:
        return {
            "index": shot.index,
            "speaker": shot.speaker_key,
            "text": shot.text,
            "duration": shot.duration,
            "start": shot.start,
            "bg_image": str(shot.bg_image) if shot.bg_image else None,
            "bg_prompt": shot.bg_prompt,
            "bg_reference": shot.bg_reference,
            "bgm_cue": shot.bgm_cue,
            "se": shot.se,
            "layout_hint": shot.layout_hint,
            "overlay_style": shot.overlay_style,
            "emotion": shot.emotion,
            "extras": shot.extras,
        }

    return {"total_duration": plan.total_duration, "shots": [_shot_dict(s) for s in plan.shots]}


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).expanduser()
    config = load_config(config_path)
    dirs = ensure_output_dirs(config)

    configure_logging(config.get("logging", {}).get("level", "INFO"), dirs["log_file"])
    logger.info("yukkuri_mode start | script=%s", args.script)

    script = load_yukkuri_json(args.script)
    style = load_style_config(config, config_path.parent)
    voice_adapter = YukkuriVoiceAdapter(config, enable_voice=not args.no_voice)

    timing_cfg = config.get("timing", {}) if isinstance(config, dict) else {}
    inter_gap = float(timing_cfg.get("inter_shot_gap", 0.18) or 0.18)
    caption_padding = float(timing_cfg.get("caption_padding", 0.25) or 0.25)

    plan = build_timeline(script, style=style, voice=voice_adapter, inter_shot_gap=inter_gap)
    if args.dump_plan:
        print(json.dumps(plan_to_dict(plan), ensure_ascii=False, indent=2))

    # Prepare synthesis
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = dirs["temp_dir"] / f"run_{run_stamp}"
    audio_dir = run_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    voice_results: List[VoiceResult] = []
    for shot in plan.shots:
        audio_path = audio_dir / f"line_{shot.index:03d}.wav"
        voice = voice_adapter.synthesize(shot.text, shot.speaker_key, audio_path)
        voice_results.append(voice)

    renderer = VideoRenderer(style, config)
    units = recalc_with_audio(
        plan,
        voice_results,
        inter_gap=inter_gap,
        padding=caption_padding,
        renderer=renderer,
    )

    # Destination path
    if args.output:
        output_path = Path(args.output).expanduser()
    else:
        slug = safe_slug(script.title)
        output_path = dirs["output_dir"] / f"{slug}.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        logger.info("Dry run complete. Plan length=%.1fs clips=%d", units[-1].start + units[-1].duration if units else 0, len(units))
        return 0

    video_path = renderer.render(units, output_path=output_path)
    logger.info("Video rendered: %s", video_path)

    subtitles_cfg = config.get("subtitles", {}) if isinstance(config, dict) else {}
    if subtitles_cfg.get("write_srt", True):
        srt_path = output_path.with_suffix(".srt")
        write_subtitles_srt(units, srt_path, style)
        logger.info("SRT written: %s", srt_path)
    if subtitles_cfg.get("write_vtt", True):
        vtt_path = output_path.with_suffix(".vtt")
        write_subtitles_vtt(units, vtt_path, style)
        logger.info("VTT written: %s", vtt_path)

    logger.info("yukkuri_mode done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
