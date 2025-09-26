"""Configuration loader for the long-form video pipeline."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

try:
    import yaml  # type: ignore
except ModuleNotFoundError as exc:  # pragma: no cover - import guard
    raise RuntimeError(
        "PyYAML is required. Please install it with `pip install pyyaml`."
    ) from exc


@dataclass
class AppConfig:
    """Wrapper around raw configuration with resolved paths."""

    raw: Dict[str, Any]
    config_path: Path
    project_root: Path
    output_dir: Path
    temp_dir: Path
    log_file: Path
    credentials_dir: Path

    @property
    def logging_level(self) -> str:
        level = (
            self.raw.get("logging", {}).get("level")
            or self.raw.get("logging", {}).get("LEVEL")
            or "INFO"
        )
        return str(level).upper()

    def to_debug_dict(self) -> Dict[str, Any]:
        return {
            "output_dir": str(self.output_dir),
            "temp_dir": str(self.temp_dir),
            "log_file": str(self.log_file),
            "credentials_dir": str(self.credentials_dir),
        }

    def dumps(self) -> str:
        """Return a JSON string for diagnostics."""
        return json.dumps(self.to_debug_dict(), ensure_ascii=False, indent=2)


def load_config(path: Path | str, project_root: Path | None = None) -> AppConfig:
    """Load YAML config and resolve key directories."""
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    root = project_root.resolve() if project_root else config_path.parent

    output_dir = (root / raw.get("output", {}).get("directory", "output")).resolve()
    temp_dir = (root / raw.get("output", {}).get("temp_directory", "temp")).resolve()
    log_file_name = raw.get("logging", {}).get("file", "logs/run.log")
    log_file = (root / log_file_name).resolve()

    credentials_dir = (root / "credentials").resolve()

    return AppConfig(
        raw=raw,
        config_path=config_path,
        project_root=root,
        output_dir=output_dir,
        temp_dir=temp_dir,
        log_file=log_file,
        credentials_dir=credentials_dir,
    )
