"""DeepInfra image generation client for long-form pipeline."""
from __future__ import annotations

import base64
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


class DeepInfraClient:
    """Generate images via DeepInfra's StabilityAI SDXL Turbo endpoint."""

    BASE_URL = "https://api.deepinfra.com/v1/inference"

    def __init__(self, config: Dict[str, Any]) -> None:
        apis_cfg = config.get("apis", {}) if isinstance(config, dict) else {}
        deepinfra_cfg = apis_cfg.get("deepinfra", {}) if isinstance(apis_cfg, dict) else {}
        if not isinstance(deepinfra_cfg, dict):
            deepinfra_cfg = {}

        active_cfg, active_profile = self._resolve_active_config(deepinfra_cfg)
        self.active_profile = active_profile
        self.active_config = dict(active_cfg)

        def _setting(key: str, default: Any) -> Any:
            if key in active_cfg and active_cfg[key] is not None:
                return active_cfg[key]
            if key in deepinfra_cfg and deepinfra_cfg[key] is not None:
                return deepinfra_cfg[key]
            return default

        model_value = str(_setting("model", "stabilityai/sdxl-turbo")).strip()
        self.model = model_value or "stabilityai/sdxl-turbo"

        token_cfg = str(_setting("api_token", "")).strip()
        env_token = os.getenv("DEEPINFRA_API_TOKEN", "").strip()
        self.api_token = token_cfg or env_token or None

        if not self.api_token:
            logger.warning("DeepInfra API token missing. Set DEEPINFRA_API_TOKEN or apis.deepinfra.api_token.")

        self.negative_prompt = str(_setting("negative_prompt", "") or "")
        self.guidance_scale = float(_setting("guidance_scale", 0.0) or 0.0)
        self.num_inference_steps = int(_setting("num_inference_steps", 4) or 4)
        if "flux-1-schnell" in self.model.lower() and self.num_inference_steps != 1:
            logger.info(
                "DeepInfra FLUX-1-schnell profile forces num_inference_steps to 1 (requested=%s)",
                self.num_inference_steps,
            )
            self.num_inference_steps = 1
        self.width = int(_setting("width", 1024) or 1024)
        self.height = int(_setting("height", 1024) or 1024)
        self.scheduler = str(_setting("scheduler", "") or "").strip()
        self.seed = _setting("seed", None)

        self.num_images = int(_setting("num_images", 1) or 1)
        if self.num_images < 1:
            self.num_images = 1

        self.retries = int(_setting("retries", 2) or 2)
        self.retry_backoff_base = float(_setting("retry_backoff_base", 0.75) or 0.75)
        self.timeout_connect = float(_setting("timeout_connect", 10) or 10)
        self.timeout_read = float(_setting("timeout_read", 60) or 60)

    def _build_url(self) -> str:
        return f"{self.BASE_URL}/{self.model}"

    def _resolve_active_config(
        self, deepinfra_cfg: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        base_cfg = deepinfra_cfg.get("base") if isinstance(deepinfra_cfg.get("base"), dict) else None
        profiles_cfg = deepinfra_cfg.get("profiles") if isinstance(deepinfra_cfg.get("profiles"), dict) else None

        if not base_cfg and not profiles_cfg:
            # Legacy flat configuration
            legacy = {
                key: value
                for key, value in deepinfra_cfg.items()
                if key not in {"base", "profiles", "default_profile"}
            }
            return legacy, None

        active: Dict[str, Any] = {}
        if base_cfg:
            for key, value in base_cfg.items():
                if value is not None:
                    active[key] = value

        selected_name = str(deepinfra_cfg.get("profile") or "").strip()
        default_name = str(deepinfra_cfg.get("default_profile") or "").strip()
        resolved_name: Optional[str] = None

        profile_cfg = None
        if isinstance(profiles_cfg, dict):
            if selected_name and isinstance(profiles_cfg.get(selected_name), dict):
                profile_cfg = profiles_cfg[selected_name]
                resolved_name = selected_name
            elif default_name and isinstance(profiles_cfg.get(default_name), dict):
                profile_cfg = profiles_cfg[default_name]
                resolved_name = default_name
            else:
                for name, candidate in profiles_cfg.items():
                    if isinstance(candidate, dict):
                        profile_cfg = candidate
                        resolved_name = str(name)
                        break

        if isinstance(profile_cfg, dict):
            for key, value in profile_cfg.items():
                if value is None:
                    active.pop(key, None)
                else:
                    active[key] = value

        return active, resolved_name

    def _build_payload(self, prompt: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "prompt": prompt,
            "num_images": self.num_images,
            "guidance_scale": self.guidance_scale,
            "num_inference_steps": self.num_inference_steps,
            "width": self.width,
            "height": self.height,
            "output_format": "jpeg",
        }
        if self.negative_prompt:
            payload["negative_prompt"] = self.negative_prompt
        if self.scheduler:
            payload["scheduler"] = self.scheduler
        if self.seed is not None:
            payload["seed"] = self.seed
        return payload

    def fetch(self, prompt: str, output_path: Path) -> Optional[Path]:
        prompt_clean = prompt.strip()
        if not prompt_clean:
            logger.warning("Empty prompt for DeepInfra; skipping image generation")
            return None

        payload = self._build_payload(prompt_clean)
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        else:
            logger.warning("DeepInfra API token is not set; request will likely fail with 401.")

        if output_path.exists():
            logger.info("DeepInfra cache hit: %s", output_path.name)
            return output_path

        url = self._build_url()
        attempt = 0
        while True:
            attempt += 1
            try:
                start = time.monotonic()
                response = requests.post(
                    url,
                    json=payload,
                    timeout=(self.timeout_connect, self.timeout_read),
                    headers=headers,
                )
                elapsed = time.monotonic() - start
                logger.info("DeepInfra response: status=%s elapsed=%.2fs", response.status_code, elapsed)

                if response.status_code in (429,) or 500 <= response.status_code < 600:
                    raise requests.HTTPError(f"HTTP {response.status_code}")

                response.raise_for_status()
                data = response.json()
                debug_path = os.getenv("DEEPINFRA_DEBUG_JSON")
                if debug_path:
                    try:
                        Path(debug_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    except Exception:
                        logger.debug("Failed to write DeepInfra debug json", exc_info=True)

                images = data.get("images")
                if not images:
                    logger.error("DeepInfra response missing 'images' field: %s", data)
                    return None

                first_image = images[0]
                if isinstance(first_image, dict):
                    encoded = first_image.get("b64_json")
                else:
                    encoded = first_image

                if not encoded:
                    logger.error("DeepInfra response missing base64 payload: %s", first_image)
                    return None

                try:
                    encoded_str = str(encoded).strip()
                    if encoded_str.startswith("data:"):
                        comma_index = encoded_str.find(",")
                        if comma_index == -1:
                            raise ValueError("Invalid data URI payload")
                        encoded_str = encoded_str[comma_index + 1 :]

                    padding = (-len(encoded_str)) % 4
                    if padding:
                        encoded_str += "=" * padding
                    try:
                        image_bytes = base64.b64decode(encoded_str)
                    except Exception:
                        image_bytes = base64.urlsafe_b64decode(encoded_str)
                except Exception as exc:
                    logger.error("Failed to decode DeepInfra image payload: %s", exc)
                    return None

                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(image_bytes)
                return output_path
            except requests.RequestException as exc:
                if attempt > max(0, self.retries):
                    logger.error("DeepInfra fetch failed after %d attempts: %s", attempt - 1, exc)
                    return None
                wait = self.retry_backoff_base * (2 ** (attempt - 1))
                wait *= random.uniform(0.8, 1.2)
                wait = max(0.2, min(wait, 15.0))
                logger.warning(
                    "DeepInfra request failed (attempt %d/%d): %s; retrying in %.2fs",
                    attempt,
                    self.retries,
                    exc,
                    wait,
                )
                time.sleep(wait)

        return None