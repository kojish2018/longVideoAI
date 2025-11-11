from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from script_parser import parse_script
from .highlight_extractor import read_marker_blocks, align_blocks
from .typing_ass_builder import build_ass


@dataclass
class ShortItem:
    id: str
    scene_id: str
    start: float
    end: float
    output: Path
    ffmpeg_cmd: List[str]
    image: Optional[Path]
    audio: Path
    ass: Path


def _select_title(script_path: Path) -> str:
    doc = parse_script(script_path)
    if doc.thumbnail_title:
        return doc.thumbnail_title.strip()[:60]
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
    image_y = int(layout.get("image_area", {}).get("y", 260))
    cap = layout.get("caption_area", {})
    cap_w = int(cap.get("width", 960))
    cap_h = int(cap.get("height", 420))
    cap_y = int(cap.get("y", 1260))
    cap_op = float(cap.get("panel_opacity", 0.85))
    dur = max(duration, 0.1)

    filters: List[str] = []
    if image_path:
        filters.append(
            "[1:v]scale=960:-2:force_original_aspect_ratio=decrease,pad=960:960:(ow-iw)/2:(oh-ih)/2:white@0.0[imgf]"
        )
        filters.append(f"[2:v][imgf]overlay=(W-w)/2:{image_y}[bgimg]")
        last = "[bgimg]"
    else:
        last = "[2:v]"
    filters.append(
        f"{last}drawbox=x=(w-{cap_w})/2:y={cap_y}:w={cap_w}:h={cap_h}:color=white@{cap_op}:t=fill,format=yuv420p[subpanel]"
    )
    filters.append(f"[subpanel]subtitles={ass_path.as_posix()}[v]")

    cmd: List[str] = [
        "ffmpeg",
        "-y",
        "-ss", f"{start_rel:.2f}", "-t", f"{dur:.2f}", "-i", str(audio_path),
    ]
    if image_path:
        cmd += ["-loop", "1", "-t", f"{dur:.2f}", "-i", str(image_path)]
    else:
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


def generate_shorts_for_run(
    *,
    script_path: Path,
    run_dir: Path,
    layout_path: Path = Path("sns_shorts_posts/layouts/vertical_v1.json"),
    output_dir: Path = Path("output/shorts/ready"),
    work_dir: Path = Path("output/shorts/work"),
    execute: bool = True,
) -> Dict:
    """Build shorts for every %%START/%%END block in the script.

    Returns a manifest dict containing outputs and commands.
    """
    blocks = read_marker_blocks(script_path)
    title = _select_title(script_path)
    highlights = align_blocks(blocks, run_dir)
    items: List[ShortItem] = []

    tl = json.loads((run_dir / "timeline.json").read_text(encoding="utf-8"))
    scene_map = {s["scene_id"]: s for s in tl.get("scenes", [])}

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    for h in highlights:
        if h.get("text_only"):
            continue
        scene_id = h.get("scene_id_start") or h["scene_id"]
        start = float(h["start"])
        end = float(h["end"])
        duration = max(end - start, 0.1)
        # use the full longform mp4 as audio source so multi-scene highlights work
        default_mp4 = run_dir / f"{run_dir.name}.mp4"
        if default_mp4.exists():
            audio_path = default_mp4
        else:
            mp4s = sorted(run_dir.glob("*.mp4"))
            audio_path = mp4s[0] if mp4s else default_mp4
        image_path = _find_image(run_dir, scene_id)

        ass_path = work_dir / f"typing_{ts}_{h['id']}.ass"
        build_ass(
            script_title=title,
            run_dir=run_dir,
            hl_start=start,
            hl_end=end,
            layout_path=layout_path,
            out_path=ass_path,
        )

        out_path = output_dir / f"short_{Path(run_dir).name}_{h['id']}.mp4"
        cmd = _ffmpeg_command(
            audio_path=audio_path,
            image_path=image_path,
            layout_path=layout_path,
            ass_path=ass_path,
            start_rel=start,
            duration=duration,
            out_path=out_path,
        )

        if execute:
            subprocess.run(cmd, check=False)

        items.append(
            ShortItem(
                id=h["id"],
                scene_id=scene_id,
                start=start,
                end=end,
                output=out_path,
                ffmpeg_cmd=cmd,
                image=image_path,
                audio=audio_path,
                ass=ass_path,
            )
        )

    manifest = {
        "script": str(script_path),
        "run_dir": str(run_dir),
        "layout": str(layout_path),
        "items": [
            {
                "id": it.id,
                "scene_id": it.scene_id,
                "start": it.start,
                "end": it.end,
                "output": str(it.output),
                "ffmpeg": " ".join(shlex.quote(p) for p in it.ffmpeg_cmd),
                "image": str(it.image) if it.image else None,
                "audio": str(it.audio),
                "ass": str(it.ass),
            }
            for it in items
        ],
    }
    manifest_path = output_dir / f"manifest_{Path(run_dir).name}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"manifest": str(manifest_path), "count": len(items)}
