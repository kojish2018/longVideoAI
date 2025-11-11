"""Pollinations image downloader for long-form pipeline."""
from __future__ import annotations

import logging
import os
import random
import time
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
        token_cfg = str(pollinations_cfg.get("api_token", "")).strip()
        env_token = os.getenv("POLLINATIONS_API_TOKEN", "").strip()
        self.api_token = token_cfg or env_token or None
        referrer_cfg = str(pollinations_cfg.get("referrer", "")).strip()
        env_referrer = os.getenv("POLLINATIONS_REFERRER", "").strip()
        self.referrer = referrer_cfg or env_referrer or None
        # Retry settings (optional)
        self.retries = int(pollinations_cfg.get("retries", 3))
        self.retry_backoff_base = float(pollinations_cfg.get("retry_backoff_base", 1.0))
        # Timeouts (seconds). If not provided, use fast-fail defaults.
        # requests.get accepts a (connect, read) tuple.
        self.timeout_connect = float(pollinations_cfg.get("timeout_connect", 5))
        self.timeout_read = float(pollinations_cfg.get("timeout_read", 45))

    def fetch(self, prompt: str, output_path: Path) -> Optional[Path]:
        if not prompt.strip():
            logger.warning("Empty prompt for Pollinations; skipping image generation")
            return None

        params = {
            "model": self.model,
            "width": self.width,
            "height": self.height,
        }
        if self.referrer:
            params["referrer"] = self.referrer
        query = urlencode(params)
        # Encode all reserved chars including '/'
        encoded_prompt = quote(prompt, safe="")
        url = f"{self.BASE_URL}{encoded_prompt}?{query}"

        logger.info("Pollinations request: %s", prompt[:80])
        logger.debug("Pollinations URL: %s", url)

        headers: Dict[str, str] = {}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
            logger.debug("Pollinations client using bearer token authentication")

        if output_path.exists():
            logger.info("Pollinations cache hit: %s", output_path.name)
            return output_path

        attempt = 0
        while True:
            attempt += 1
            try:
                start = time.monotonic()
                response = requests.get(
                    url,
                    timeout=(self.timeout_connect, self.timeout_read),
                    allow_redirects=True,
                    headers=headers or None,
                )
                # Retry on 429/5xx
                status = response.status_code
                elapsed = time.monotonic() - start
                logger.info("Pollinations response: status=%s elapsed=%.2fs", status, elapsed)
                if status == 404:
                    # Likely bad prompt/path: don't retry
                    response.raise_for_status()
                if status in (429,) or 500 <= status < 600:
                    raise requests.HTTPError(f"HTTP {status}")

                response.raise_for_status()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(response.content)
                return output_path
            except requests.RequestException as exc:  # pragma: no cover - network
                if attempt > max(0, self.retries):
                    logger.error("Pollinations fetch failed after %d attempts: %s", attempt - 1, exc)
                    return None
                # Backoff with jitter
                wait = self.retry_backoff_base * (2 ** (attempt - 1))
                wait *= random.uniform(0.8, 1.2)
                logger.warning(
                    "Pollinations request failed (attempt %d/%d): %s; retrying in %.2fs",
                    attempt,
                    self.retries,
                    exc,
                    wait,
                )
                time.sleep(max(0.1, min(wait, 10.0)))
