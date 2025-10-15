from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, List, Sequence, Optional

from logging_utils import get_logger

logger = get_logger(__name__)


def run_ffmpeg(args: Sequence[str], *, cwd: Path | None = None) -> None:
    """Run ffmpeg with the given arguments, raising on non-zero exit.

    Logs the full command for debuggability.
    """
    # Keep ffmpeg quiet: only errors; no stats; no banner
    cmd: List[str] = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats"] + list(args)
    pretty = " ".join(a if " " not in a else f"'{a}'" for a in cmd)
    logger.debug("FFmpeg: %s", pretty)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").splitlines()[-50:]
        for line in tail:
            logger.error("ffmpeg: %s", line)
        raise RuntimeError(f"ffmpeg failed with exit code {proc.returncode}")


def run_ffmpeg_stream(
    args: Sequence[str],
    *,
    expected_duration_sec: float,
    label: str,
    on_draw: Optional[Callable[[float], None]] = None,
    cwd: Optional[Path] = None,
    # When an external ConsoleBar is provided, use it instead of creating a per-step bar.
    external_bar=None,
    offset_seconds: float = 0.0,
) -> None:
    """Run ffmpeg with `-progress pipe:1` and stream progress.

    Calls `on_draw(current_seconds)` with the parsed `out_time_ms` converted to seconds.
    """
    from .progress import ProgressParser, ConsoleBar

    full_args: List[str] = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-progress",
        "pipe:1",
    ] + list(args)
    pretty = " ".join(a if " " not in a else f"'{a}'" for a in full_args)
    logger.debug("FFmpeg(stream): %s", pretty)

    local_bar = None
    if external_bar is None:
        local_bar = ConsoleBar(total_seconds=expected_duration_sec, label=label)
        parser = ProgressParser(
            on_time=lambda t: (on_draw(t) if on_draw else None) or local_bar.update(t)
        )
    else:
        # Use a shared bar tracking the whole timeline
        try:
            external_bar.label = label  # update step label
        except Exception:
            pass
        parser = ProgressParser(
            on_time=lambda t: (on_draw(t) if on_draw else None) or external_bar.update(max(0.0, offset_seconds + t))
        )

    proc = subprocess.Popen(
        full_args,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            parser.feed_line(line)
    finally:
        proc.wait()
        if local_bar is not None:
            local_bar.finish()
    if proc.returncode != 0:
        err = proc.stderr.read() if proc.stderr else ""
        tail = err.splitlines()[-50:]
        for line in tail:
            logger.error("ffmpeg: %s", line)
        raise RuntimeError(f"ffmpeg failed with exit code {proc.returncode}")
