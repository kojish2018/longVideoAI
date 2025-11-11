from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from logging_utils import get_logger

logger = get_logger(__name__)


def copy_thumbnail(
    *,
    run_dir: Path,
    run_id: str,
    source_path: Optional[str | Path],
    copy_name: Optional[str] = None,
) -> Optional[Path]:
    """Copy an existing thumbnail into the run directory and return its new path."""

    if not source_path:
        return None

    source = Path(source_path).expanduser().resolve()
    if not source.exists():
        logger.error("Thumbnail source not found: %s", source)
        return None
    if not source.is_file():
        logger.error("Thumbnail source is not a file: %s", source)
        return None

    destination_name = _determine_destination_name(
        copy_name=copy_name,
        run_id=run_id,
        source=source,
    )

    destination = run_dir / destination_name
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        logger.info("Thumbnail copied to: %s", destination)
        return destination
    except Exception as exc:  # pragma: no cover - filesystem error handling
        logger.exception("Failed to copy thumbnail '%s' -> '%s': %s", source, destination, exc)
        return None


def _determine_destination_name(*, copy_name: Optional[str], run_id: str, source: Path) -> str:
    if copy_name:
        name = copy_name.strip()
        if not name:
            name = None
        else:
            if Path(name).suffix:
                return name
            return f"{name}{source.suffix or '.png'}"

    fallback_suffix = source.suffix or ".png"
    return f"thumbnail_{run_id}{fallback_suffix}".lstrip("/")
