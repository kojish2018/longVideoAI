from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from logging_utils import get_logger
from long_form.ffmpeg.runner import run_ffmpeg

logger = get_logger(__name__)


@dataclass(frozen=True)
class BgmSettings:
    enabled: bool
    directory: Path
    selected: str
    volume: float
    fade_in: float
    fade_out: float

    @classmethod
    def from_config(cls, raw_config: Dict[str, object] | None) -> "BgmSettings":
        if raw_config is None:
            raw_config = {}

        enabled = bool(raw_config.get("enabled", True))

        directory_raw = raw_config.get("directory", "background_music")
        directory = str(directory_raw).strip() if directory_raw else "background_music"
        if not directory:
            directory = "background_music"

        selected_raw = raw_config.get("selected", "GoodDays.mp3")
        selected = str(selected_raw).strip() if selected_raw else "GoodDays.mp3"
        if not selected:
            selected = "GoodDays.mp3"

        try:
            volume = float(raw_config.get("volume", 0.24))
        except (TypeError, ValueError):
            volume = 0.24

        try:
            fade_in = float(raw_config.get("fade_in", 0.5))
        except (TypeError, ValueError):
            fade_in = 0.5

        try:
            fade_out = float(raw_config.get("fade_out", 1.0))
        except (TypeError, ValueError):
            fade_out = 1.0

        fade_in = max(fade_in, 0.0)
        fade_out = max(fade_out, 0.0)
        volume = max(volume, 0.0)

        return cls(
            enabled=enabled,
            directory=Path(directory),
            selected=selected,
            volume=volume,
            fade_in=fade_in,
            fade_out=fade_out,
        )


class PresentationBgmMixer:
    """Mix narration audio with a background music track for presentation videos."""

    def __init__(self, config: Dict[str, object]) -> None:
        bgm_cfg = config.get("bgm", {}) if isinstance(config, dict) else {}
        bgm_dict = bgm_cfg if isinstance(bgm_cfg, dict) else {}
        self.settings = BgmSettings.from_config(bgm_dict)

    def mix(
        self,
        input_video: Path,
        output_path: Path,
        *,
        total_duration: float,
        audio_codec: str,
        audio_sample_rate: int,
        audio_bitrate: Optional[str],
    ) -> Path:
        """Overlay the configured BGM onto the concatenated narration video."""

        total_duration = max(total_duration, 0.1)
        if not self.settings.enabled:
            logger.info("Presentation BGM disabled via configuration.")
            return self._passthrough(input_video, output_path)

        bgm_path = self._resolve_bgm_path()
        if not bgm_path:
            return self._passthrough(input_video, output_path)

        fade_out_start = max(total_duration - self.settings.fade_out, 0.0)
        sr = int(audio_sample_rate)

        filter_complex = (
            f"[1:a]atrim=0:duration={total_duration:.3f},asetpts=PTS-STARTPTS,"
            f"loudnorm=I=-30:LRA=7:TP=-2,"
            f"volume={self.settings.volume:.3f},"
            f"afade=t=in:st=0:d={self.settings.fade_in:.3f},"
            f"afade=t=out:st={fade_out_start:.3f}:d={self.settings.fade_out:.3f},"
            f"aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo[bgm];"
            f"[0:a]aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo[narr];"
            f"[narr][bgm]amix=inputs=2:duration=first:dropout_transition=2[a];"
            f"[a]loudnorm=I=-14:LRA=7:TP=-1.5,"
            f"aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo[aout]"
        )

        args: list[str] = [
            "-i",
            str(input_video),
            "-stream_loop",
            "-1",
            "-i",
            str(bgm_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            audio_codec,
            "-ar",
            str(sr),
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            "-shortest",
            "-y",
            str(output_path),
        ]

        if audio_bitrate:
            args.extend(["-b:a", str(audio_bitrate)])

        logger.info("Mixing presentation BGM: %s", bgm_path)

        try:
            run_ffmpeg(args)
        except Exception:
            logger.exception("Failed to mix presentation BGM; falling back to narration audio only.")
            return self._passthrough(input_video, output_path)

        # FFmpeg succeeded; safe to remove temporary concatenated file.
        try:
            input_video.unlink(missing_ok=True)
        except Exception:
            logger.warning("Failed to delete temporary concat video: %s", input_video)
        return output_path

    # ------------------------------------------------------------------

    def _resolve_bgm_path(self) -> Optional[Path]:
        if not self.settings.enabled:
            return None

        candidates: list[Path] = []
        selected_path = Path(self.settings.selected)
        if selected_path.is_absolute():
            candidates.append(selected_path)
        else:
            candidates.append(self.settings.directory / self.settings.selected)
            candidates.append(selected_path)

        for candidate in candidates:
            try:
                if candidate.exists():
                    return candidate.resolve()
            except Exception:
                continue

        logger.warning(
            "Presentation BGM file not found; skipping mix. selection=%s directory=%s",
            self.settings.selected,
            self.settings.directory,
        )
        return None

    def _passthrough(self, input_video: Path, output_path: Path) -> Path:
        if input_video.resolve() == output_path.resolve():
            return output_path

        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(input_video), str(output_path))
        except Exception:
            logger.exception("Failed to move concatenated video into place for passthrough; attempting copy.")
            try:
                shutil.copy2(str(input_video), str(output_path))
                input_video.unlink(missing_ok=True)
            except Exception:
                logger.exception("Failed to copy concatenated video during passthrough fallback.")
                return input_video
        return output_path
