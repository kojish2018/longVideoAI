from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional, TextIO


def format_hms(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


@dataclass
class ConsoleBar:
    total_seconds: float
    label: str = "Render"
    width: int = 24
    stream: TextIO = sys.stderr

    def __post_init__(self) -> None:
        self.start_time = time.time()
        self.last_render = 0.0
        self._draw(0.0)

    def update(self, current_seconds: float) -> None:
        now = time.time()
        # Rate-limit updates to avoid flicker (10 fps max).
        if now - self.last_render < 0.1:
            return
        self.last_render = now
        self._draw(current_seconds)

    def finish(self) -> None:
        self._draw(self.total_seconds)
        self.stream.write("\n")
        self.stream.flush()

    # ------------------------------------------------------------------
    def _draw(self, current_seconds: float) -> None:
        total = max(self.total_seconds, 0.001)
        cur = min(max(current_seconds, 0.0), total)
        frac = cur / total
        filled = int(round(self.width * frac))
        bar = "█" * filled + "·" * (self.width - filled)
        elapsed = time.time() - self.start_time
        # Simple ETA estimate; guard for small frac
        eta = 0.0 if frac <= 0.0001 else elapsed * (1.0 / frac - 1.0)
        msg = (
            f"[{bar}] {int(frac*100):3d}% | "
            f"{format_hms(elapsed)} / {format_hms(total)} | "
            f"ETA {format_hms(eta)} | {self.label}"
        )
        self.stream.write("\r" + msg)
        self.stream.flush()


class ProgressParser:
    """Parse `-progress pipe:1` key=value pairs and report out_time seconds."""

    def __init__(self, on_time: Callable[[float], None]) -> None:
        self.on_time = on_time

    def feed_line(self, line: str) -> None:
        line = line.strip()
        if not line or "=" not in line:
            return
        key, value = line.split("=", 1)
        if key == "out_time_ms":
            try:
                ms = int(value)
                self.on_time(ms / 1000000.0)
            except ValueError:
                pass

