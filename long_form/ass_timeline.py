#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple


def _fmt_time(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    cs_total = int(round(sec * 100))
    h, rem = divmod(cs_total, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc_text(text: str) -> str:
    return (
        text.replace("{", "｛")
        .replace("}", "｝")
        .replace("\\", "＼")
        .replace("\t", "    ")
        .replace("\r", "")
        .replace("\n", r"\N")
    )


@dataclass
class Segment:
    start: float
    duration: float
    lines: List[str]


def build_ass_for_scene(
    *,
    width: int,
    height: int,
    fontname: str,
    fontsize: int,
    align_num: int = 2,  # bottom-center
    margin_v: int = 80,
    primary: str = "&H00FFFFFF",
    outline: str = "&H00222222",
    back: str = "&H64000000",
    outline_w: int = 3,
    shadow: int = 0,
    effect: str = "static",
    segments: Sequence[Segment] = (),
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

    lines_out: List[str] = []
    is_typing = (effect or "static").lower() == "typing"

    for seg in segments:
        if seg.duration <= 0:
            continue
        raw_text = "\n".join(seg.lines)
        txt = _esc_text(raw_text)
        if not txt:
            continue
        if not is_typing:
            start = seg.start
            end = seg.start + seg.duration
            lines_out.append(
                f"Dialogue: 0,{_fmt_time(start)},{_fmt_time(end)},Typing,,0,0,0,,{txt}"
            )
            continue

        n = len(txt)
        if n <= 0:
            continue
        # Auto-fit cps to segment duration so the last char lands at the end.
        cps = max(n / max(seg.duration, 0.01), 1.0)
        for i in range(1, n + 1):
            t0 = seg.start + (i - 1) / cps
            t1 = seg.start + i / cps
            if i == n:
                t1 = seg.start + seg.duration
            snippet = txt[:i]
            lines_out.append(
                f"Dialogue: 0,{_fmt_time(t0)},{_fmt_time(t1)},Typing,,0,0,0,,{snippet}"
            )

    return header + "\n".join(lines_out) + "\n"


def build_ass_for_content_scene(
    *,
    width: int,
    height: int,
    fontname: str,
    fontsize: int,
    effect: str,
    overlay_margin_v: int,
    segments: Sequence[Tuple[float, float, List[str]]],
) -> str:
    mapped = [Segment(start=s, duration=d, lines=list(lines)) for (s, d, lines) in segments]
    return build_ass_for_scene(
        width=width,
        height=height,
        fontname=fontname,
        fontsize=fontsize,
        align_num=2,
        margin_v=overlay_margin_v,
        effect=effect,
        segments=mapped,
    )


# Fixed-position variant (uses {\pos(x,y)} with top-left anchoring)
@dataclass
class SegmentPos:
    start: float
    duration: float
    lines: List[str]
    pos_x: int
    pos_y: int


def build_ass_for_content_scene_pos(
    *,
    width: int,
    height: int,
    fontname: str,
    fontsize: int,
    effect: str,
    segments: Sequence[SegmentPos],
    speed: float = 1.0,
    bold: bool = False,
    primary: str = "&H00FFFFFF",
    outline: str = "&H00222222",
    back: str = "&H64000000",
    outline_w: int = 3,
    shadow: int = 0,
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
        # Alignment=7 (top-left) so that pos(x,y) places top-left corner of the text box
        f"Style: Typing,{fontname},{fontsize},{primary},&H000000FF,{outline},{back},"
        f"{1 if bold else 0},0,0,0,100,100,0,0,1,{outline_w},{shadow},7,0,0,0,1\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )

    is_typing = (effect or "static").lower() == "typing"
    speed = float(speed) if isinstance(speed, (int, float)) else 1.0
    if speed <= 0:
        speed = 1.0
    out_lines: List[str] = []
    for seg in segments:
        if seg.duration <= 0:
            continue
        raw = "\n".join(seg.lines)
        txt = _esc_text(raw)
        if not txt:
            continue
        pos_tag = f"{{\\pos({seg.pos_x},{seg.pos_y})}}"
        if not is_typing:
            st = _fmt_time(seg.start)
            en = _fmt_time(seg.start + seg.duration)
            out_lines.append(
                f"Dialogue: 0,{st},{en},Typing,,0,0,0,,{pos_tag}{txt}"
            )
            continue

        n = len(txt)
        cps_base = n / max(seg.duration, 0.01)
        cps = max(cps_base * speed, 1.0)
        for i in range(1, n + 1):
            t0 = seg.start + (i - 1) / cps
            t1 = seg.start + i / cps
            if i == n:
                t1 = seg.start + seg.duration
            snippet = txt[:i]
            out_lines.append(
                f"Dialogue: 0,{_fmt_time(t0)},{_fmt_time(t1)},Typing,,0,0,0,,{pos_tag}{snippet}"
            )

    return header + "\n".join(out_lines) + "\n"


# Per-line typing with fixed left edge computed to achieve final centered layout.
@dataclass
class LineTypingSpec:
    t0: float
    seg_end: float
    cps: float
    text: str
    pos_x: int
    pos_y: int


def build_ass_centered_lines_typing(
    *,
    width: int,
    height: int,
    fontname: str,
    fontsize: int,
    lines: Sequence[LineTypingSpec],
    bold: bool = False,
    primary: str = "&H00FFFFFF",
    outline: str = "&H00222222",
    back: str = "&H64000000",
    outline_w: int = 3,
    shadow: int = 0,
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
        # Alignment=7 (top-left), coordinates via \pos()
        f"Style: Typing,{fontname},{fontsize},{primary},&H000000FF,{outline},{back},"
        f"{1 if bold else 0},0,0,0,100,100,0,0,1,{outline_w},{shadow},7,0,0,0,1\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    out: List[str] = []
    for spec in lines:
        txt = _esc_text(spec.text)
        if not txt:
            continue
        pos_tag = f"{{\\pos({spec.pos_x},{spec.pos_y})}}"
        n = len(txt)
        cps = max(float(spec.cps), 1.0)
        for i in range(1, n + 1):
            t0 = spec.t0 + (i - 1) / cps
            t1 = spec.t0 + i / cps
            if i == n:
                t1 = spec.seg_end
            snippet = txt[:i]
            out.append(
                f"Dialogue: 0,{_fmt_time(t0)},{_fmt_time(t1)},Typing,,0,0,0,,{pos_tag}{snippet}"
            )
    return header + "\n".join(out) + "\n"


# Karaoke-based typing: layout is fixed by libass, reveal left->right within the event
@dataclass
class KaraokeLineSpec:
    t0: float
    seg_end: float
    cps: float
    text: str
    pos_cx: int  # center x (Alignment=8)
    pos_y: int   # top y


def build_ass_karaoke_centered(
    *,
    width: int,
    height: int,
    fontname: str,
    fontsize: int,
    lines: Sequence[KaraokeLineSpec],
    bold: bool = False,
    primary: str = "&H00FFFFFF",
    outline: str = "&H00222222",
    back: str = "&H64000000",
    outline_w: int = 3,
    shadow: int = 0,
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
        # Alignment=8 (top-center). SecondaryColour is ignored by forcing \2a&HFF& per line.
        f"Style: Typing,{fontname},{fontsize},{primary},&H00FFFFFF,{outline},{back},"
        f"{1 if bold else 0},0,0,0,100,100,0,0,1,{outline_w},{shadow},8,0,0,0,1\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )

    out_lines: List[str] = []
    for spec in lines:
        txt = _esc_text(spec.text)
        if not txt:
            continue
        # Determine total highlight duration for this line (n/cps), capped by seg_end
        n = len(txt)
        cps = max(float(spec.cps), 1.0)
        highlight = min((n / cps), max(spec.seg_end - spec.t0, 0.01))
        total_ticks = max(int(round(highlight * 100)), n)  # at least 1 tick per char

        # Distribute ticks roughly uniformly, adjust last to fit
        base = max(total_ticks // n, 1)
        ticks = [base] * n
        rem = total_ticks - base * n
        for i in range(rem):
            ticks[i] += 1

        # Build karaoke text
        pos_tag = f"{{\\an8\\pos({spec.pos_cx},{spec.pos_y})\\q2\\2a&HFF&}}"
        # Optional: hide outline until highlighted – \ko0 is sufficient in many renderers
        # Note: some renderers treat \ko differently; keeping minimal here.
        fragments: List[str] = [pos_tag]
        for k, ch in zip(ticks, txt):
            fragments.append(f"{{\\kf{k}}}{ch}")

        line_text = "".join(fragments)
        out_lines.append(
            f"Dialogue: 0,{_fmt_time(spec.t0)},{_fmt_time(spec.seg_end)},Typing,,0,0,0,,{line_text}"
        )

    return header + "\n".join(out_lines) + "\n"
