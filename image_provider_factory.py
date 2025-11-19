"""Factory helpers for selecting image generation providers."""
from __future__ import annotations

import logging
from typing import Any, Dict

from pollinations_client import PollinationsClient

try:
    from deepinfra_client import DeepInfraClient
except ModuleNotFoundError:  # pragma: no cover - optional dependency during bootstrap
    DeepInfraClient = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_SUPPORTED_PROVIDERS = {"pollinations", "deepinfra"}


def _resolve_provider(config: Dict[str, Any]) -> str:
    provider = "pollinations"
    if not isinstance(config, dict):
        return provider

    apis = config.get("apis", {})
    if isinstance(apis, dict):
        candidate = apis.get("image_provider")
        if candidate is not None:
            provider = str(candidate).strip().lower() or provider
    return provider


def make_image_client(config: Dict[str, Any]):
    """Return an image generation client based on configuration."""

    provider = _resolve_provider(config)
    if provider not in _SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported image provider '{provider}'. Supported providers: {sorted(_SUPPORTED_PROVIDERS)}"
        )

    if provider == "deepinfra":
        if DeepInfraClient is None:
            raise RuntimeError(
                "DeepInfra client module not available. Ensure deepinfra_client.py exists and dependencies are installed."
            )
        logger.debug("Using DeepInfra client for image generation")
        return DeepInfraClient(config)

    logger.debug("Using Pollinations client for image generation")
    return PollinationsClient(config)