"""Pollinations image downloader for long-form pipeline."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode, quote

import requests

logger = logging.getLogger(__name__)


class PollinationsClient:
    """Retrieve images from pollinations.ai using simple HTTP requests."""

    BASE_URL = "https://image.pollinations.ai/prompt/"

    def __init__(self, config: Dict[str, Any]) -> None:
        pollinations_cfg = config.get("apis", {}).get("pollinations", {})
        self.model = pollinations_cfg.get("model", "flux")
        self.width = pollinations_cfg.get("width", 1920)
        self.height = pollinations_cfg.get("height", 1080)

    def fetch(self, prompt: str, output_path: Path) -> Optional[Path]:
        if not prompt.strip():
            logger.warning("Empty prompt for Pollinations; skipping image generation")
            return None

        params = {
            "model": self.model,
            "width": self.width,
            "height": self.height,
        }
        query = urlencode(params)
        encoded_prompt = quote(prompt)
        url = f"{self.BASE_URL}{encoded_prompt}?{query}"

        logger.info("Pollinations request: %s", prompt[:80])
        logger.debug("Pollinations URL: %s", url)

        try:
            if output_path.exists():
                logger.info("Pollinations cache hit: %s", output_path.name)
                return output_path

            response = requests.get(url, timeout=120, allow_redirects=True)
            response.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(response.content)
            return output_path
        except requests.RequestException as exc:  # pragma: no cover - network
            logger.error("Pollinations fetch failed: %s", exc)
            return None
