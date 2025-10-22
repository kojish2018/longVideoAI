#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Typing overlay generator for long-form videos (ASS + ffmpeg)

- Default: static (no typing animation). Shows the full text at once.
- With `--type typing`: generates a character-by-character reveal.
- Overlays via ffmpeg's `subtitles` filter (libass required).

Usage example:
  # Static (default)
  python long_form/typing_overlay.py \
    -i input.mp4 -o output/out.mp4 -t "こんにちは、世界！" \
    --start 1.0 --hold 3.0 --fontsdir fonts --font "Noto Sans CJK JP" --fontsize 72 \
    --align center --valign bottom

  # Typing animation
  python long_form/typing_overlay.py \
    -i input.mp4 -o output/out.mp4 -t "こんにちは、世界！" \
    --type typing --cps 5 --start 1.0 --hold 1.0 \
    --fontsdir fonts --font "Noto Sans CJK JP" --fontsize 72 \
    --align center --valign bottom

Notes:
  - Does not modify files under repo_clone/ (per AGENTS.md).
  - Logs are written to logs/; outputs to output/.
  - If libass is missing in ffmpeg, the subtitles filter may fail.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import shlex
import subprocess
import sys
from typing import Optional, Tuple


def _now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _ensure_dir(path: str) -> None:
    if path and not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)


def _fmt_time(sec: float) -> str:
    # ASS time format: H:MM:SS.cs (centiseconds)
    if sec < 0:
        sec = 0
    cs_total = int(round(sec * 100))
    h, rem = divmod(cs_total, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc_text(s: str) -> str:
    """Escape text for ASS.
    ASS has no official escape for {} so replace with full-width braces.
    Also convert newlines to ASS line breaks.
    """
    return (
        s.replace("{", "｛").replace("}", "｝").replace("\\", "＼").replace("\n", r"\N")
    )


def _map_alignment(align: str, valign: str) -> int:
    align = (align or "center").lower()
    valign = (valign or "bottom").lower()
    h_index = {"left": 0, "center": 1, "right": 2}.get(align, 1)
    base = {"top": 7, "middle": 4, "bottom": 1}.get(valign, 1)
    return base + h_index


def _probe_resolution(path: str) -> Optional[Tuple[int, int]]:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=s=x:p=0",
                path,
            ],
            stderr=subprocess.STDOUT,
        ).decode("utf-8", "replace").strip()
        if "x" in out:
            w_str, h_str = out.split("x", 1)
            return int(w_str), int(h_str)
    except Exception:
        return None
    return None


def build_ass(
    text: str,
    width: int,
    height: int,
    fontname: str,
    fontsize: int,
    align_num: int,
    margin_v: int,
    start: float,
    cps: float,
    hold: float,
    primary: str = "&H00FFFFFF",
    outline: str = "&H00222222",
    back: str = "&H64000000",
    outline_w: int = 3,
    shadow: int = 0,
    effect: str = "static",
) -> str:
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "ScaledBorderAndShadow: yes\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Typing,{fontname},{fontsize},{primary},&H000000FF,{outline},{back},"
        f"0,0,0,0,100,100,0,0,1,{outline_w},{shadow},{align_num},120,120,{margin_v},1\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )

    txt = _esc_text(text)
    n = len(txt)

    # Static effect: one event showing the full text for `hold` seconds after `start`.
    if (effect or "static").lower() == "static":
        dur = max(hold, 0.01)
        single = (
            f"Dialogue: 0,{_fmt_time(start)},{_fmt_time(start + dur)},Typing,,0,0,0,,{txt}\n"
        )
        return header + single

    # Typing effect: character-by-character events.
    if cps <= 0:
        cps = 1.0
    if n == 0:
        single = (
            f"Dialogue: 0,{_fmt_time(start)},{_fmt_time(start + max(hold, 0.01))},Typing,,0,0,0,,\n"
        )
        return header + single

    lines = []
    for i in range(1, n + 1):
        t0 = start + (i - 1) / cps
        t1 = start + i / cps if i < n else start + n / cps + hold
        snippet = txt[:i]
        lines.append(
            f"Dialogue: 0,{_fmt_time(t0)},{_fmt_time(t1)},Typing,,0,0,0,,{snippet}"
        )
    return header + "\n".join(lines) + "\n"


def _build_subtitles_filter(ass_path: str, fontsdir: Optional[str]) -> str:
    # Use explicit named options and quote paths.
    # subtitles=filename='...':fontsdir='...'
    esc_ass = ass_path.replace("'", "'\\''")
    if fontsdir:
        esc_fonts = fontsdir.replace("'", "'\\''")
        return f"subtitles=filename='{esc_ass}':fontsdir='{esc_fonts}'"
    return f"subtitles=filename='{esc_ass}'"


def run_ffmpeg(
    input_path: str,
    output_path: str,
    ass_path: str,
    fontsdir: Optional[str],
    vcodec: str = "libx264",
    crf: int = 18,
    preset: str = "medium",
    pix_fmt: str = "yuv420p",
    overwrite: bool = False,
    loglevel: str = "info",
    log_path: Optional[str] = None,
) -> int:
    vf = _build_subtitles_filter(ass_path, fontsdir)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        loglevel,
        "-i",
        input_path,
        "-vf",
        vf,
        "-c:v",
        vcodec,
        "-crf",
        str(crf),
        "-preset",
        preset,
        "-pix_fmt",
        pix_fmt,
        "-movflags",
        "+faststart",
        output_path,
    ]
    if overwrite:
        cmd.insert(1, "-y")

    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if log_path:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("# Command\n")
            f.write(" ".join(shlex.quote(c) for c in cmd) + "\n\n")
            f.write("# STDOUT\n")
            f.write(proc.stdout or "" + "\n\n")
            f.write("# STDERR\n")
            f.write(proc.stderr or "")
    return proc.returncode


def _read_text(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    if args.textfile:
        with open(args.textfile, "r", encoding="utf-8") as f:
            return f.read()
    raise SystemExit("Provide --text or --textfile")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Overlay typing effect text using ASS + ffmpeg",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("-i", "--input", required=True, help="Input video path")
    p.add_argument("-o", "--output", default=None, help="Output video path")
    gtxt = p.add_mutually_exclusive_group(required=True)
    gtxt.add_argument("-t", "--text", help="Text to type (UTF-8)")
    gtxt.add_argument("--textfile", help="Read text from file (UTF-8)")
    p.add_argument("--start", type=float, default=0.0, help="Start time in seconds")
    p.add_argument("--cps", type=float, default=5.0, help="Characters per second")
    p.add_argument("--hold", type=float, default=1.0, help="Hold time after last char")
    p.add_argument("--width", type=int, default=None, help="PlayResX (autodetect if omitted)")
    p.add_argument("--height", type=int, default=None, help="PlayResY (autodetect if omitted)")
    p.add_argument("--fontsize", type=int, default=72, help="Font size (ASS units)")
    p.add_argument("--font", default="Noto Sans CJK JP", help="Font name in ASS style")
    p.add_argument("--fontsdir", default="fonts", help="Directory that contains font files")
    p.add_argument("--align", choices=["left", "center", "right"], default="center")
    p.add_argument("--valign", choices=["top", "middle", "bottom"], default="bottom")
    p.add_argument("--margin-v", type=int, default=80, help="Vertical margin (ASS) from edge")
    p.add_argument("--outline", type=int, default=3, help="Outline width")
    p.add_argument("--shadow", type=int, default=0, help="Shadow size")
    p.add_argument("--primary-color", default="&H00FFFFFF", help="ASS PrimaryColour")
    p.add_argument("--outline-color", default="&H00222222", help="ASS OutlineColour")
    p.add_argument("--back-color", default="&H64000000", help="ASS BackColour")
    p.add_argument("--ass-only", action="store_true", help="Only write ASS file and exit")
    p.add_argument("--ass-path", default=None, help="Output ASS path; defaults into output/")
    p.add_argument("--type", dest="effect", choices=["static", "typing"], default="static", help="Effect mode: static (default) or typing")
    p.add_argument("--crf", type=int, default=18, help="x264 CRF")
    p.add_argument("--preset", default="medium", help="x264 preset")
    p.add_argument("--pix-fmt", default="yuv420p", help="Pixel format")
    p.add_argument("--overwrite", action="store_true", help="Overwrite output file if exists")
    p.add_argument("--ffmpeg-loglevel", default="info", help="ffmpeg loglevel")
    p.add_argument("--log", default=None, help="Log path; defaults to logs/typing_*.log")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    # Prepare folders
    out_dir = os.path.join("output")
    log_dir = os.path.join("logs")
    _ensure_dir(out_dir)
    _ensure_dir(log_dir)

    text = _read_text(args)

    # Output names
    stamp = _now_stamp()
    if args.output:
        output_path = args.output
        _ensure_dir(os.path.dirname(output_path) or ".")
    else:
        output_path = os.path.join(out_dir, f"typed_{stamp}.mp4")

    if args.ass_path:
        ass_path = args.ass_path
        _ensure_dir(os.path.dirname(ass_path) or ".")
    else:
        ass_path = os.path.join(out_dir, f"typing_{stamp}.ass")

    # Resolution
    width, height = args.width, args.height
    if width is None or height is None:
        probed = _probe_resolution(args.input)
        if probed:
            width, height = probed
        else:
            # Fallback to a common PlayRes; this only affects text layout scale.
            width, height = 1920, 1080

    align_num = _map_alignment(args.align, args.valign)

    ass_text = build_ass(
        text=text,
        width=width,
        height=height,
        fontname=args.font,
        fontsize=args.fontsize,
        align_num=align_num,
        margin_v=args.margin_v,
        start=args.start,
        cps=args.cps,
        hold=args.hold,
        primary=args.primary_color,
        outline=args.outline_color,
        back=args.back_color,
        outline_w=args.outline,
        shadow=args.shadow,
        effect=args.effect,
    )

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_text)

    # Default log path
    log_path = args.log or os.path.join(log_dir, f"typing_{stamp}.log")

    if args.ass_only:
        print(f"Wrote ASS: {ass_path}")
        print("(ass-only mode; no ffmpeg run)")
        return 0

    # Run ffmpeg
    rc = run_ffmpeg(
        input_path=args.input,
        output_path=output_path,
        ass_path=ass_path,
        fontsdir=args.fontsdir,
        vcodec="libx264",
        crf=args.crf,
        preset=args.preset,
        pix_fmt=args.pix_fmt,
        overwrite=args.overwrite,
        loglevel=args.ffmpeg_loglevel,
        log_path=log_path,
    )
    if rc == 0:
        print(f"OK: {output_path}")
        print(f"ASS: {ass_path}")
        print(f"Log: {log_path}")
        return 0
    else:
        print("ffmpeg failed. See log:", log_path, file=sys.stderr)
        return rc


if __name__ == "__main__":
    raise SystemExit(main())
