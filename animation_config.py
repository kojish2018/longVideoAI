"""Helpers for resolving animation/Ken Burns configuration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class KenBurnsProfile:
    mode: str
    padding_seconds: float
    zoom: float
    offset: float
    margin: float
    motion_scale: float
    full_travel: bool
    max_margin: float
    pan_extent: float
    intro_relief: float
    intro_seconds: float


_COMMON_DEFAULTS: Dict[str, Any] = {
    "padding_seconds": 0.35,
    "ken_burns_zoom": 0.0,
    "ken_burns_offset": 0.03,
    "ken_burns_margin": 0.08,
    "ken_burns_motion_scale": 1.0,
    "ken_burns_full_travel": False,
    "ken_burns_max_margin": 0.5,
    "ken_burns_pan_extent": 0.17,
    "ken_burns_intro_relief": 0.2,
    "ken_burns_intro_seconds": 0.8,
}

_MODE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "zoompan": {
        "ken_burns_zoom": 0.04,
        "ken_burns_offset": 0.085,
        "ken_burns_margin": 0.09,
        "ken_burns_motion_scale": 1.0,
        "ken_burns_max_margin": 0.45,
        "ken_burns_pan_extent": 1.0,
        "ken_burns_intro_relief": 0.2,
        "ken_burns_intro_seconds": 0.8,
    },
    "pan_only": {
        "ken_burns_zoom": 0.0,
        "ken_burns_offset": 0.4,
        "ken_burns_margin": 0.2,
        "ken_burns_motion_scale": 3.0,
        "ken_burns_max_margin": 1.5,
        "ken_burns_pan_extent": 0.17,
        "ken_burns_intro_relief": 1.0,
        "ken_burns_intro_seconds": 0.0,
    },
}


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in {"true", "1", "yes", "on"}:
            return True
        if lower in {"false", "0", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def resolve_ken_burns_profile(animation_cfg: Dict[str, Any] | None) -> KenBurnsProfile:
    cfg = animation_cfg if isinstance(animation_cfg, dict) else {}
    mode_value = cfg.get("mode", cfg.get("ken_burns_mode", "pan_only"))
    mode = str(mode_value).lower() if isinstance(mode_value, (str, bytes)) else "pan_only"
    if mode not in _MODE_DEFAULTS:
        mode = "pan_only"

    merged: Dict[str, Any] = dict(_COMMON_DEFAULTS)
    merged.update(_MODE_DEFAULTS.get(mode, {}))

    nested_mode_cfg = cfg.get(mode)
    if isinstance(nested_mode_cfg, dict):
        merged.update({k: v for k, v in nested_mode_cfg.items() if v is not None})

    for key in _COMMON_DEFAULTS.keys():
        if key in cfg and cfg[key] is not None:
            merged[key] = cfg[key]

    return KenBurnsProfile(
        mode=mode,
        padding_seconds=_to_float(merged.get("padding_seconds"), _COMMON_DEFAULTS["padding_seconds"]),
        zoom=_to_float(merged.get("ken_burns_zoom"), _COMMON_DEFAULTS["ken_burns_zoom"]),
        offset=_to_float(merged.get("ken_burns_offset"), _COMMON_DEFAULTS["ken_burns_offset"]),
        margin=_to_float(merged.get("ken_burns_margin"), _COMMON_DEFAULTS["ken_burns_margin"]),
        motion_scale=_to_float(merged.get("ken_burns_motion_scale"), _COMMON_DEFAULTS["ken_burns_motion_scale"]),
        full_travel=_to_bool(merged.get("ken_burns_full_travel"), _COMMON_DEFAULTS["ken_burns_full_travel"]),
        max_margin=_to_float(merged.get("ken_burns_max_margin"), _COMMON_DEFAULTS["ken_burns_max_margin"]),
        pan_extent=_to_float(merged.get("ken_burns_pan_extent"), _COMMON_DEFAULTS["ken_burns_pan_extent"]),
        intro_relief=_to_float(merged.get("ken_burns_intro_relief"), _COMMON_DEFAULTS["ken_burns_intro_relief"]),
        intro_seconds=_to_float(merged.get("ken_burns_intro_seconds"), _COMMON_DEFAULTS["ken_burns_intro_seconds"]),
    )
