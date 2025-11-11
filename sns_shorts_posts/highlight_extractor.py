from __future__ import annotations

"""
Highlight extractor for vertical shorts.

- Parses %%START ... %%END blocks from a script file (txt/md)
- Aligns them to a long-form run under output/longform_*
  using audio/S###.json segments and timeline.json
- Emits output/shorts/meta/highlights.json

Usage:
  python sns_shorts_posts/highlight_extractor.py \
      --script longscripts_txt/motivation1.md \
      [--run-dir output/longform_20251105_144949] \
      [--out output/shorts/meta/highlights.json]

Notes:
  - Only standard library is used; no external deps.
  - If end anchor is not found, falls back to start+120s or the last scene end.
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class MarkerBlock:
    index: int
    lines: List[str]

    @property
    def title(self) -> str:
        for ln in self.lines:
            s = str(ln).strip()
            if s:
                return s[:40]
        return f"Highlight {self.index:03d}"


def read_marker_blocks(script_path: Path) -> List[MarkerBlock]:
    text = script_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    blocks: List[MarkerBlock] = []
    collecting = False
    buf: List[str] = []
    for ln in lines:
        s = ln.strip()
        if s == "%%START":
            if collecting and buf:
                blocks.append(MarkerBlock(index=len(blocks) + 1, lines=list(buf)))
                buf.clear()
            collecting = True
            continue
        if s == "%%END":
            if collecting:
                blocks.append(MarkerBlock(index=len(blocks) + 1, lines=list(buf)))
                buf.clear()
                collecting = False
            continue
        if collecting:
            buf.append(ln)

    if collecting and buf:
        blocks.append(MarkerBlock(index=len(blocks) + 1, lines=list(buf)))

    return blocks


def _latest_longform_dir(output_root: Path) -> Optional[Path]:
    if not output_root.exists():
        return None
    cands = [p for p in output_root.iterdir() if p.is_dir() and p.name.startswith("longform_")]
    if not cands:
        return None
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0]


def _load_timeline(run_dir: Path) -> dict:
    timeline_path = run_dir / "timeline.json"
    data = json.loads(timeline_path.read_text(encoding="utf-8"))
    by_id = {}
    for scene in data.get("scenes", []):
        by_id[scene["scene_id"]] = {
            "start": float(scene.get("start_time", 0.0)),
            "duration": float(scene.get("duration", 0.0)),
        }
    return by_id


def _load_segments(run_dir: Path) -> dict:
    audio_dir = run_dir / "audio"
    mapping = {}
    for j in sorted(audio_dir.glob("S*.json")):
        data = json.loads(j.read_text(encoding="utf-8"))
        scene_id = data.get("scene_id") or j.stem
        segs = []
        for seg in data.get("segments", []):
            lines = [str(ln).strip() for ln in seg.get("lines", []) if str(ln).strip()]
            joined = "\n".join(lines)
            segs.append(
                {
                    "idx": int(seg.get("segment_index", 0)),
                    "start": float(seg.get("start_offset", 0.0)),
                    "dur": float(seg.get("duration", 0.0)),
                    "lines": lines,
                    "joined": joined,
                }
            )
        mapping[scene_id] = segs
    return mapping


def _first_last_nonempty(lines: List[str]) -> Tuple[Optional[str], Optional[str]]:
    head = None
    for ln in lines:
        s = str(ln).strip()
        if s:
            head = s
            break
    tail = None
    for ln in reversed(lines):
        s = str(ln).strip()
        if s:
            tail = s
            break
    return head, tail


def _match_line(line: str, segments: List[dict]) -> Optional[int]:
    if not line:
        return None
    for i, seg in enumerate(segments):
        if line in seg["lines"]:
            return i
    for i, seg in enumerate(segments):
        if line in seg["joined"]:
            return i
    prefix = line[:10]
    for i, seg in enumerate(segments):
        if any(s.startswith(prefix) for s in seg["lines"]):
            return i
    return None


def align_blocks(blocks: List[MarkerBlock], run_dir: Path) -> List[dict]:
    """Align blocks across scenes so long blocks spanning multiple scenes are covered.

    - Start: first match by timeline order.
    - End: search forward across subsequent scenes; if not found, scan block
      lines in reverse until a match. Fallback to start+120s or last scene end.
    """
    timeline = _load_timeline(run_dir)
    segs_by_scene = _load_segments(run_dir)
    ordered = sorted(((sid, info["start"]) for sid, info in timeline.items()), key=lambda x: x[1])
    scene_index = {sid: i for i, (sid, _) in enumerate(ordered)}
    highlights: List[dict] = []

    for idx, block in enumerate(blocks, start=1):
        head, tail = _first_last_nonempty(block.lines)

        # Find start anchor by timeline order
        start_scene_id = None
        start_seg_idx = None
        start_abs: Optional[float] = None
        for sid, _ in ordered:
            segs = segs_by_scene.get(sid, [])
            pos = _match_line(head or "", segs)
            if pos is not None:
                start_scene_id = sid
                start_seg_idx = pos
                start_abs = timeline[sid]["start"] + segs[pos]["start"]
                break

        if start_scene_id is None or start_abs is None:
            highlights.append(
                {
                    "id": f"hl_{idx:03d}",
                    "title": block.title,
                    "text_only": True,
                    "lines": [str(ln).strip() for ln in block.lines],
                }
            )
            continue

        # Find end anchor across scenes
        end_abs: Optional[float] = None
        end_scene_id: Optional[str] = None
        if tail:
            si = scene_index[start_scene_id]
            for ii in range(si, len(ordered)):
                sid = ordered[ii][0]
                segs = segs_by_scene.get(sid, [])
                start_j = start_seg_idx if ii == si else 0
                for j in range(start_j, len(segs)):
                    if tail in segs[j]["lines"] or tail in segs[j]["joined"]:
                        end_abs = timeline[sid]["start"] + segs[j]["start"] + segs[j]["dur"]
                        end_scene_id = sid
                        break
                if end_abs is not None:
                    break

            # Fallback: try any last matching line of the block (reverse search)
            if end_abs is None:
                nonempty = [ln.strip() for ln in block.lines if str(ln).strip()]
                for cand in reversed(nonempty):
                    for ii in range(si, len(ordered)):
                        sid = ordered[ii][0]
                        segs = segs_by_scene.get(sid, [])
                        start_j = start_seg_idx if ii == si else 0
                        for j in range(start_j, len(segs)):
                            if cand in segs[j]["lines"] or cand in segs[j]["joined"]:
                                end_abs = (
                                    timeline[sid]["start"] + segs[j]["start"] + segs[j]["dur"]
                                )
                                end_scene_id = sid
                                break
                        if end_abs is not None:
                            break
                    if end_abs is not None:
                        break

        if end_abs is None:
            last_scene_end = max(
                (timeline[sid]["start"] + timeline[sid]["duration"] for sid, _ in ordered),
                default=start_abs + 120.0,
            )
            end_abs = min(start_abs + 120.0, last_scene_end)
            end_scene_id = start_scene_id

        highlights.append(
            {
                "id": f"hl_{idx:03d}",
                "title": block.title,
                "start": round(start_abs, 2),
                "end": round(end_abs, 2),
                "scene_id": start_scene_id,
                "scene_id_start": start_scene_id,
                "scene_id_end": end_scene_id,
            }
        )

    return highlights


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Extract %%START/%%END highlights and align to latest longform run.")
    parser.add_argument("--script", required=True, help="Path to script file (txt/md)")
    parser.add_argument("--run-dir", help="Path to specific longform run directory (defaults to latest)")
    parser.add_argument("--out", help="Output JSON path (defaults to output/shorts/meta/highlights.json)")
    args = parser.parse_args(argv)

    script_path = Path(args.script).expanduser().resolve()
    if not script_path.exists():
        parser.error(f"Script not found: {script_path}")

    blocks = read_marker_blocks(script_path)
    if not blocks:
        payload = {"source_script": str(script_path), "highlights": []}
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(str(out))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.run_dir:
        run_dir = Path(args.run_dir).expanduser().resolve()
    else:
        run_dir = _latest_longform_dir(Path("output"))
        if run_dir is None:
            parser.error("No output/longform_* directory found. Run the long-form pipeline first or pass --run-dir.")

    highlights = align_blocks(blocks, run_dir)
    result = {"source_script": str(script_path), "run_dir": str(run_dir), "highlights": highlights}

    out_path = Path(args.out) if args.out else Path("output/shorts/meta/highlights.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out_path))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
