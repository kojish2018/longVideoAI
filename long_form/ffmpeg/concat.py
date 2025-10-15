from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from logging_utils import get_logger
from .runner import run_ffmpeg

logger = get_logger(__name__)


def concat_mp4_streamcopy(inputs: Iterable[Path], output: Path) -> Path:
    """Concat identically-encoded MP4 segments using concat demuxer with copy.

    - Validates input files (existence, size>0)
    - If only one input: fast path with stream copy
    - Uses ffconcat list with header for robustness
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    files = [Path(p).resolve() for p in inputs]

    if not files:
        raise RuntimeError("concat: no input segments provided")

    # Validate inputs
    missing = [str(p) for p in files if not p.exists()]
    zero = [str(p) for p in files if p.exists() and p.stat().st_size == 0]
    if missing or zero:
        logger.error("concat: invalid inputs | missing=%d zero=%d", len(missing), len(zero))
        if missing:
            for p in missing[:10]:
                logger.error("missing: %s", p)
            if len(missing) > 10:
                logger.error("missing: ... (%d more)", len(missing) - 10)
        if zero:
            for p in zero[:10]:
                logger.error("zero-size: %s", p)
            if len(zero) > 10:
                logger.error("zero-size: ... (%d more)", len(zero) - 10)
        raise RuntimeError("concat: some segments are missing or empty")

    # Fast path: single input => copy
    if len(files) == 1:
        logger.info("concat: single segment, stream-copying to final")
        args: List[str] = [
            "-i",
            str(files[0]),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-y",
            str(output),
        ]
        run_ffmpeg(args)
        return output

    # Write ffconcat list with header for stability
    list_file = output.with_suffix(".concat.txt")
    lines = ["ffconcat version 1.0"] + [f"file '{p}'" for p in files]
    payload = "\n".join(lines) + "\n"
    list_file.write_text(payload, encoding="utf-8")

    logger.debug("concat: list file => %s (%d segments)", list_file, len(files))
    # Attempt concat with copy
    args = [
        "-safe",
        "0",
        "-f",
        "concat",
        "-i",
        str(list_file),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-y",
        str(output),
    ]
    try:
        run_ffmpeg(args)
    except Exception:
        # Dump a small context for debugging
        try:
            content = list_file.read_text(encoding="utf-8").splitlines()
            head = " | ".join(content[:5])
            tail = " | ".join(content[-5:])
            logger.error("concat list head: %s", head)
            logger.error("concat list tail: %s", tail)
        except Exception:
            pass
        raise
    return output
