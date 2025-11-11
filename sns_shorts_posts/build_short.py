from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from script_parser import parse_script
from sns_shorts_posts.highlight_extractor import (
    read_marker_blocks,
    align_blocks,
    _latest_longform_dir,
)
from sns_shorts_posts.typing_ass_builder import build_ass


def _load_highlights(highlights_path: Optional[Path]) -> Optional[Dict]:
    if not highlights_path:
        return None
    p = Path(highlights_path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def _select_title(script_path: Path) -> str:
    doc = parse_script(script_path)
    if doc.thumbnail_title:
        return doc.thumbnail_title.strip()[:60]
    # fallback: first non-empty line of first section
    for sec in doc.sections:
        for ln in sec.lines:
            s = ln.strip()
            if s:
                return s[:60]
    return ""


def _find_image(run_dir: Path, scene_id: str) -> Optional[Path]:
    p = run_dir / "images" / f"{scene_id}.jpg"
    if p.exists():
        return p
    # fallback: any jpg
    cands = sorted((run_dir / "images").glob("*.jpg"))
    return cands[0] if cands else None


def _ffmpeg_command(*,
    audio_path: Path,
    image_path: Optional[Path],
    layout_path: Path,
    ass_path: Path,
    start_rel: float,
    duration: float,
    out_path: Path,
) -> List[str]:
    layout = json.loads(Path(layout_path).read_text(encoding="utf-8"))
    image_area = layout.get("image_area", {})
    image_y = int(image_area.get("y", 260))
    image_w = int(image_area.get("width", 960))
    image_h = int(image_area.get("height", image_w))
    cap = layout.get("caption_area", {})
    cap_x = int(cap.get("x", 60))
    cap_y = int(cap.get("y", 1260))
    cap_w = int(cap.get("width", 960))
    cap_h = int(cap.get("height", 420))
    cap_op = float(cap.get("panel_opacity", 0.85))

    dur = max(duration, 0.1)

    filters: List[str] = []
    # Base white canvas
    # input 2 is color video, input 1 is image, input 0 is audio
    if image_path:
        filters.append(
            f"[1:v]scale={image_w}:-2:force_original_aspect_ratio=decrease,pad={image_w}:{image_h}:(ow-iw)/2:(oh-ih)/2:white@0.0[imgf]"
        )
        filters.append(
            f"[2:v][imgf]overlay=(W-w)/2:{image_y}[bgimg]"
        )
        last = "[bgimg]"
    else:
        last = "[2:v]"

    if cap_op > 0:
        filters.append(
            f"{last}drawbox=x={cap_x}:y={cap_y}:w={cap_w}:h={cap_h}:color=white@{cap_op}:t=fill[panel]"
        )
        panel_in = "[panel]"
    else:
        panel_in = last
    filters.append(
        f"{panel_in}format=yuv420p[subpanel]"
    )
    # Subtitles (ASS contains both title and typing captions)
    filters.append(
        f"[subpanel]subtitles={ass_path.as_posix()}[v]"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-ss", f"{start_rel:.2f}", "-t", f"{dur:.2f}", "-i", str(audio_path),
    ]
    if image_path:
        cmd += ["-loop", "1", "-t", f"{dur:.2f}", "-i", str(image_path)]
    else:
        # dummy 1x1 black if no image (won't be used)
        cmd += ["-f", "lavfi", "-t", f"{dur:.2f}", "-i", "color=c=black:s=1x1"]

    cmd += [
        "-f", "lavfi", "-t", f"{dur:.2f}", "-i", "color=c=white:s=1080x1920",
        "-filter_complex", ",".join(filters),
        "-map", "[v]", "-map", "0:a",
        "-c:v", "libx264", "-crf", "19", "-preset", "fast", "-r", "30", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
        str(out_path),
    ]
    return cmd


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build vertical short(s) with typing subtitles and fixed title.")
    parser.add_argument("--script", required=True, help="Path to script file")
    parser.add_argument("--highlights", help="Path to highlights.json (if omitted, derive from script)")
    parser.add_argument("--hl-id", help="Highlight id to build (default: all)")
    parser.add_argument("--layout", default="sns_shorts_posts/layouts/vertical_v1.json", help="Layout JSON path")
    parser.add_argument("--run-dir", help="Override longform run directory")
    parser.add_argument("--output-dir", default="output/shorts/ready", help="Output directory for mp4")
    parser.add_argument("--work-dir", default="output/shorts/work", help="Work directory for temp files")
    parser.add_argument("--execute", action="store_true", help="Actually run ffmpeg (otherwise print command)")
    args = parser.parse_args(argv)

    script_path = Path(args.script).expanduser().resolve()
    if not script_path.exists():
        parser.error(f"Script not found: {script_path}")

    title = _select_title(script_path) or ""

    highlights_data = _load_highlights(Path(args.highlights)) if args.highlights else None
    run_dir = Path(args.run_dir).expanduser().resolve() if args.run_dir else None

    if highlights_data is None:
        # derive from markers in the script
        blocks = read_marker_blocks(script_path)
        if not blocks:
            parser.error("No %%START/%%END blocks found in the script. Run highlight_extractor first or add markers.")
        if not run_dir:
            run_dir = _latest_longform_dir(Path("output"))
            if run_dir is None:
                parser.error("No output/longform_* found. Provide --run-dir.")
        highlights = align_blocks(blocks, run_dir)
    else:
        run_dir = Path(highlights_data.get("run_dir") or args.run_dir or "").expanduser()
        if not run_dir or not run_dir.exists():
            # try latest
            run_dir = _latest_longform_dir(Path("output"))
            if run_dir is None:
                parser.error("Cannot resolve run_dir from highlights and no output/longform_* found.")
        highlights = highlights_data.get("highlights", [])

    out_dir = Path(args.output_dir)
    work_dir = Path(args.work_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    selected: List[Dict] = []
    if args.hl_id:
        for h in highlights:
            if h.get("id") == args.hl_id:
                selected.append(h)
                break
        if not selected:
            parser.error(f"Highlight id not found: {args.hl_id}")
    else:
        selected = [h for h in highlights if not h.get("text_only")]
        if not selected:
            parser.error("No time-aligned highlights available (text_only).")

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    layout_path = Path(args.layout)
    manifest: List[Dict] = []

    for h in selected:
        scene_id = h.get("scene_id_start") or h["scene_id"]
        start = float(h["start"])
        end = float(h["end"])
        duration = max(end - start, 0.1)
        # use longform mp4 as audio source so cross-scene highlights work
        default_mp4 = run_dir / f"{run_dir.name}.mp4"
        if default_mp4.exists():
            audio_path = default_mp4
        else:
            mp4s = sorted(run_dir.glob("*.mp4"))
            audio_path = mp4s[0] if mp4s else default_mp4
        image_path = _find_image(run_dir, scene_id)

        ass_path = work_dir / f"typing_{ts}_{h['id']}.ass"
        ass_info = build_ass(
            script_title=title,
            run_dir=run_dir,
            hl_start=start,
            hl_end=end,
            layout_path=layout_path,
            out_path=ass_path,
        )

        out_path = out_dir / f"short_{ts}_{h['id']}.mp4"
        cmd = _ffmpeg_command(
            audio_path=audio_path,
            image_path=image_path,
            layout_path=layout_path,
            ass_path=ass_path,
            start_rel=start,
            duration=ass_info["duration"],
            out_path=out_path,
        )

        manifest.append({
            "highlight": h,
            "ass": ass_info,
            "audio": str(audio_path),
            "image": str(image_path) if image_path else None,
            "ffmpeg": " ".join(shlex.quote(p) for p in cmd),
            "output": str(out_path),
        })

        if args.execute:
            print("Running:", manifest[-1]["ffmpeg"])  # noqa: T201
            subprocess.run(cmd, check=False)
        else:
            print(manifest[-1]["ffmpeg"])  # noqa: T201

    meta_path = out_dir / f"manifest_{ts}.json"
    meta_path.write_text(json.dumps({"items": manifest}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(meta_path))  # noqa: T201
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
