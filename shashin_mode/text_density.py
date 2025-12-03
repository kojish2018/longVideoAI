"""Utility for filtering text-heavy images using pytesseract detection."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

from PIL import Image
import pytesseract
from pytesseract import Output, TesseractError, TesseractNotFoundError

from logging_utils import get_logger

logger = get_logger(__name__)


class TextDensityDetector:
    """Detect text-heavy images via pytesseract bounding boxes."""

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        area_threshold: float = 0.18,
        min_boxes: int = 15,
        lang: Optional[str] = None,
        min_confidence: float = 40.0,
        psm: int = 6,
    ) -> None:
        env_disable = os.getenv("SHASHIN_DISABLE_TEXT_FILTER")
        if enabled is None:
            enabled = env_disable not in {"1", "true", "True"}
        self.enabled = enabled
        self.area_threshold = area_threshold
        self.min_boxes = min_boxes
        self.lang = lang or os.getenv("SHASHIN_TESSERACT_LANG", "jpn+eng")
        self.min_confidence = min_confidence
        self.psm = psm
        self._cache: dict[str, bool] = {}
        self._lock = threading.Lock()
        self._tesseract_ready = self._check_tesseract()

    @property
    def available(self) -> bool:
        return self.enabled and self._tesseract_ready

    def _check_tesseract(self) -> bool:
        if not self.enabled:
            return False
        try:
            pytesseract.get_tesseract_version()
            return True
        except (TesseractNotFoundError, OSError) as exc:
            logger.warning("Tesseract CLI not found; text density filter disabled: %s", exc)
            return False

    def is_text_heavy(self, image_path: Path) -> bool:
        if not self.available:
            return False
        resolved = str(Path(image_path).resolve())
        if resolved in self._cache:
            return self._cache[resolved]

        boxes = self._extract_boxes(image_path)
        if not boxes:
            self._cache[resolved] = False
            return False

        image_area = self._image_area(image_path)
        total_area = sum(w * h for (_, _, w, h) in boxes)
        ratio = total_area / image_area if image_area else 0.0
        # OR判定: boxes数が多い OR テキスト面積比が高い → 除外
        is_heavy = len(boxes) >= self.min_boxes or ratio >= self.area_threshold
        self._cache[resolved] = is_heavy
        if is_heavy:
            logger.info(
                "Detected text-heavy image (boxes=%d ratio=%.3f >= %.3f): %s",
                len(boxes),
                ratio,
                self.area_threshold,
                Path(image_path).name,
            )
        return is_heavy

    def _extract_boxes(self, image_path: Path) -> list[tuple[int, int, int, int]]:
        try:
            with Image.open(image_path) as img:
                img = img.convert("RGB")
                data = pytesseract.image_to_data(
                    img,
                    lang=self.lang,
                    config=f"--psm {self.psm}",
                    output_type=Output.DICT,
                )
        except (TesseractNotFoundError, TesseractError, OSError) as exc:
            logger.warning("pytesseract failed (%s): %s", image_path, exc)
            return []

        boxes: list[tuple[int, int, int, int]] = []
        texts = data.get("text") or []
        confidences = data.get("conf") or []
        widths = data.get("width") or []
        heights = data.get("height") or []
        lefts = data.get("left") or []
        tops = data.get("top") or []

        for text, conf_str, w, h, x, y in zip(texts, confidences, widths, heights, lefts, tops):
            text = (text or "").strip()
            try:
                conf = float(conf_str)
            except (TypeError, ValueError):
                conf = -1.0
            if not text or conf < self.min_confidence:
                continue
            width = int(w)
            height = int(h)
            if width <= 0 or height <= 0:
                continue
            boxes.append((int(x), int(y), width, height))
        return boxes

    @staticmethod
    def _image_area(image_path: Path) -> float:
        try:
            with Image.open(image_path) as img:
                return float(max(1, img.width * img.height))
        except Exception:
            return 0.0


