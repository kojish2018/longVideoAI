"""Translate prompts to English using DeepL."""
from __future__ import annotations

import logging
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)


class PromptTranslator:
    """Translate Japanese text to English for image prompts."""

    def __init__(self, config: Dict[str, object]) -> None:
        apis = config.get("apis", {}) if isinstance(config, dict) else {}
        deepl_cfg = apis.get("deepl", {}) if isinstance(apis, dict) else {}
        self.api_key: str = str(deepl_cfg.get("api_key", "")) if deepl_cfg else ""
        self._cache: Dict[str, str] = {}

    def translate(self, text: str) -> str:
        """Translate text to English using DeepL; fall back to original on failure."""
        normalized = text.strip()
        if not normalized:
            return text

        if not self.api_key:
            logger.debug("DeepL API key not configured; using original prompt")
            return text

        if normalized in self._cache:
            return self._cache[normalized]

        url = "https://api-free.deepl.com/v2/translate"
        headers = {"Authorization": f"DeepL-Auth-Key {self.api_key}"}
        data = {"text": normalized, "target_lang": "EN"}

        try:
            response = requests.post(url, headers=headers, data=data, timeout=10)
            response.raise_for_status()
            translations = response.json().get("translations") or []
            if not translations:
                logger.warning("DeepL translation returned no candidates; using original text")
                self._cache[normalized] = text
                return text
            translated = translations[0].get("text", "").strip()
            if not translated:
                logger.warning("DeepL translation empty; using original text")
                self._cache[normalized] = text
                return text
            logger.debug("Translated prompt '%s' -> '%s'", normalized[:40], translated[:40])
            self._cache[normalized] = translated
            return translated
        except requests.RequestException as exc:
            logger.error("DeepL request failed: %s", exc)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Unexpected DeepL error: %s", exc)

        self._cache[normalized] = text
        return text
