"""Lightweight image fetcher for shashin_mode.

Default: Openverse API (商用ライセンス系を優先して取得)。
Azure Bing Image Search API はオプション/フォールバックとして保持。
スクレイピング(Google/Bing)はさらなるフォールバック用途。
"""
from __future__ import annotations

import os
import random
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

import requests
from PIL import Image

from logging_utils import get_logger

logger = get_logger(__name__)


_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


_IMG_SRC_PATTERN = re.compile(r"src=\"(https?://[^\"]+)\"")
_MURL_PATTERN = re.compile(r"murl\":\"(https?://[^\"\\]+)")
_ALLOWED_OPENVERSE_LICENSE_TYPES = {"all", "all-cc", "commercial", "modification"}


@dataclass
class ImageFetcher:
    provider: str = "openverse"
    fallback_image: Optional[Path] = None
    reference_dir: Path = Path("shashin_mode/referenceimage")
    bing_api_key: Optional[str] = None
    bing_api_endpoint: Optional[str] = None
    openverse_page_size: int = 5
    openverse_license_filter: str = "cc0,by,by-sa"
    openverse_license_type: Optional[str] = None

    def __post_init__(self) -> None:
        self.provider = (self.provider or "google").lower()
        self._session = requests.Session()
        self._session.headers.update(_DEFAULT_HEADERS)
        self._cache: Dict[str, Path] = {}
        if not self.bing_api_key:
            self.bing_api_key = (
                os.getenv("BING_SEARCH_V7_KEY")
                or os.getenv("BING_SEARCH_KEY")
                or os.getenv("AZURE_BING_SEARCH_KEY")
            )
        if not self.bing_api_endpoint:
            self.bing_api_endpoint = (
                os.getenv("BING_SEARCH_V7_ENDPOINT")
                or os.getenv("BING_SEARCH_ENDPOINT")
                or "https://api.bing.microsoft.com/v7.0/images/search"
            )

    def fetch(self, query: str, target_path: Path, *, provider_override: Optional[str] = None) -> Optional[Path]:
        normalized = query.strip()
        if not normalized:
            logger.warning("Image query is empty; using fallback image")
            return self._fallback_image(target_path)

        if normalized in self._cache and self._cache[normalized].exists():
            existing = self._cache[normalized]
            if existing != target_path:
                try:
                    Image.open(existing).save(target_path)
                except Exception:
                    target_path.write_bytes(existing.read_bytes())
            return target_path

        provider = provider_override or self.provider
        fetcher = {
            "openverse": self._fetch_from_openverse,
            "bing_api": self._fetch_from_bing_api,
            "google": self._fetch_from_google,
            "bing": self._fetch_from_bing,
            "icrawler": self._fetch_from_icrawler,
            "local": self._fetch_local_only,
            "none": self._fetch_local_only,
        }.get(provider, self._fetch_from_openverse)

        fetched = fetcher(normalized, target_path)
        if fetched and fetched.exists():
            self._cache[normalized] = fetched
            return fetched

        logger.warning("Search provider failed (%s); using fallback", provider)
        return self._fallback_image(target_path)

    # ------------------------------------------------------------------ #
    # Providers
    # ------------------------------------------------------------------ #

    def _fetch_from_openverse(self, query: str, target_path: Path) -> Optional[Path]:
        """Openverse API (ライセンス情報付き)。APIキー不要。"""
        params = self._build_openverse_params(query)
        url = "https://api.openverse.org/v1/images"

        try:
            # Official endpoint moved to api.openverse.org (June 2024 migration)
            response = self._session.get(url, params=params, timeout=20)
            response.raise_for_status()
        except requests.HTTPError as exc:
            response = self._retry_openverse_without_license_type(exc, url, params)
            if not response:
                return self._fetch_from_bing_api(query, target_path)
        except requests.RequestException as exc:
            logger.error("Openverse request failed: %s", exc)
            return self._fetch_from_bing_api(query, target_path)

        try:
            data = response.json()
        except ValueError as exc:
            logger.error("Openverse response parse failed: %s", exc)
            return self._fetch_from_bing_api(query, target_path)

        results = data.get("results") or []
        for item in results:
            image_url = item.get("url") or item.get("thumbnail")
            if not image_url:
                continue
            fetched = self._download_image(image_url, target_path)
            if fetched:
                return fetched
        logger.warning("Openverse returned no usable images for query: %s", query)
        return self._fetch_from_bing_api(query, target_path)

    def fetch_batch(self, query: str, target_dir: Path, *, limit: int = 10, provider_override: Optional[str] = None) -> List[Path]:
        normalized = query.strip()
        if not normalized:
            logger.warning("Batch image query is empty; skipping batch fetch")
            return []
        if limit <= 0:
            logger.warning("Batch image limit must be positive; received %s", limit)
            return []

        provider = provider_override or self.provider
        if provider == "icrawler":
            return self._fetch_batch_from_icrawler(normalized, target_dir, limit=limit)
        else:
            # Default to openverse
            return self._fetch_batch_from_openverse(normalized, target_dir, limit=limit)

    def _fetch_batch_from_openverse(self, query: str, target_dir: Path, *, limit: int = 10) -> List[Path]:
        url = "https://api.openverse.org/v1/images"
        params = self._build_openverse_params(query, page_size=limit)
        try:
            response = self._session.get(url, params=params, timeout=20)
            response.raise_for_status()
            data = response.json()
        except requests.HTTPError as exc:
            body = getattr(exc.response, "text", "") or ""
            logger.error(
                "Openverse batch request failed (%s): %s",
                exc.response.status_code if exc.response else "HTTPError",
                body[:300],
            )
            return []
        except (requests.RequestException, ValueError) as exc:
            logger.error("Openverse batch request error: %s", exc)
            return []

        results: List[Path] = []
        target_dir.mkdir(parents=True, exist_ok=True)
        for item in data.get("results") or []:
            image_url = item.get("url") or item.get("thumbnail")
            if not image_url:
                continue
            # シンプルな連番形式のファイル名を使用（例: img_01.jpg, img_02.jpg）
            image_index = len(results) + 1
            target_path = target_dir / f"img_{image_index:02d}.jpg"
            fetched = self._download_image(image_url, target_path)
            if fetched:
                # アスペクト比をチェック（9:16 から 16:9 の範囲内）
                if self._check_aspect_ratio(fetched):
                        results.append(fetched)
                else:
                    # 範囲外の画像を削除
                    try:
                        fetched.unlink()
                    except Exception:
                        pass

        if not results:
            logger.warning("Openverse returned no usable images for batch query: %s", query)
        else:
            logger.info("Fetched %d shared Openverse images (aspect ratio filtered) for query: %s", len(results), query)
        return results

    def _fetch_from_bing_api(self, query: str, target_path: Path) -> Optional[Path]:
        if not self.bing_api_key:
            logger.warning("Bing API key is not set (env BING_SEARCH_V7_KEY); falling back to scraping")
            return self._fetch_from_google(query, target_path)

        try:
            params = {
                "q": query,
                "safeSearch": "Strict",
                "imageType": "Photo",
                "color": "Color",
                "mkt": "ja-JP",
                "count": 1,
            }
            headers = {"Ocp-Apim-Subscription-Key": self.bing_api_key}
            response = self._session.get(self.bing_api_endpoint, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            items = data.get("value") or []
            for item in items:
                image_url = item.get("contentUrl") or item.get("thumbnailUrl")
                if image_url:
                    return self._download_image(image_url, target_path)
            logger.warning("Bing API returned no images for query: %s ; trying Google scrape", query)
            return self._fetch_from_google(query, target_path)
        except requests.RequestException as exc:
            logger.error("Bing API request failed: %s", exc)
            return None
        except ValueError as exc:
            logger.error("Bing API response parse failed: %s", exc)
            return None

    def _fetch_from_google(self, query: str, target_path: Path) -> Optional[Path]:
        try:
            url = f"https://www.google.com/search?tbm=isch&q={requests.utils.quote(query)}"
            response = self._session.get(url, timeout=15)
            response.raise_for_status()
            image_url = self._extract_first_image_url(response.text, pattern=_IMG_SRC_PATTERN)
            if not image_url:
                return None
            return self._download_image(image_url, target_path)
        except requests.RequestException as exc:
            logger.error("Google image scrape failed: %s", exc)
            return None

    def _fetch_from_bing(self, query: str, target_path: Path) -> Optional[Path]:
        try:
            url = f"https://www.bing.com/images/search?q={requests.utils.quote(query)}"
            response = self._session.get(url, timeout=15)
            response.raise_for_status()
            image_url = self._extract_first_image_url(response.text, pattern=_MURL_PATTERN)
            if not image_url:
                image_url = self._extract_first_image_url(response.text, pattern=_IMG_SRC_PATTERN)
            if not image_url:
                return None
            return self._download_image(image_url, target_path)
        except requests.RequestException as exc:
            logger.error("Bing image scrape failed: %s", exc)
            return None

    def _fetch_from_icrawler(self, query: str, target_path: Path) -> Optional[Path]:
        """Fetch a single image using icrawler (BingImageCrawler)."""
        try:
            from icrawler.builtin import BingImageCrawler
        except ImportError:
            logger.error("icrawler is not installed. Install it with: pip install icrawler")
            return self._fetch_from_bing_api(query, target_path)
        
        import tempfile
        import shutil
        
        temp_dir = Path(tempfile.mkdtemp())
        try:
            storage = {'root_dir': str(temp_dir)}
            crawler = BingImageCrawler(storage=storage)
            crawler.crawl(keyword=query, max_num=1)
            
            # Find downloaded image
            downloaded_files = list(temp_dir.glob("*"))
            for file_path in downloaded_files:
                if file_path.is_file() and file_path.suffix.lower() in {'.jpg', '.jpeg', '.png', '.gif'}:
                    # Copy to target path
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    final_path = self._ensure_jpeg(file_path)
                    shutil.copy2(final_path, target_path)
                    logger.info("Downloaded image via icrawler: %s", target_path.name)
                    return target_path
            
            logger.warning("icrawler returned no usable images for query: %s", query)
            return self._fetch_from_bing_api(query, target_path)
        except Exception as exc:
            logger.error("icrawler fetch failed: %s", exc)
            return self._fetch_from_bing_api(query, target_path)
        finally:
            # Cleanup temp directory
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

    def _fetch_batch_from_icrawler(self, query: str, target_dir: Path, *, limit: int = 10) -> List[Path]:
        """Fetch multiple images using icrawler (BingImageCrawler), similar to openverse fetch_batch."""
        try:
            from icrawler.builtin import BingImageCrawler
        except ImportError:
            logger.error("icrawler is not installed. Install it with: pip install icrawler")
            return []
        
        target_dir.mkdir(parents=True, exist_ok=True)
        storage = {'root_dir': str(target_dir)}
        
        try:
            crawler = BingImageCrawler(storage=storage)
            crawler.crawl(keyword=query, max_num=limit)
            
            # Find downloaded images
            downloaded_files = sorted([f for f in target_dir.glob("*") if f.is_file() and f.suffix.lower() in {'.jpg', '.jpeg', '.png', '.gif'}])
            
            results: List[Path] = []
            valid_count = 0
            for file_path in downloaded_files:
                if valid_count >= limit:
                    break
                
                # Rename to sequential format (img_01.jpg, img_02.jpg)
                new_path = target_dir / f"img_{valid_count + 1:02d}.jpg"
                if new_path != file_path:
                    try:
                        final_path = self._ensure_jpeg(file_path)
                        shutil.move(str(final_path), str(new_path))
                    except Exception as exc:
                        logger.warning("Failed to rename icrawler image %s: %s", file_path.name, exc)
                        continue
                
                # アスペクト比をチェック（9:16 から 16:9 の範囲内）
                if self._check_aspect_ratio(new_path):
                    results.append(new_path)
                    valid_count += 1
                else:
                    # 範囲外の画像を削除
                    try:
                        new_path.unlink()
                    except Exception:
                        pass
            
            # 不要になったファイルをクリーンアップ
            for file_path in downloaded_files:
                if file_path not in results and file_path.exists():
                    try:
                        file_path.unlink()
                    except Exception:
                        pass
            
            if not results:
                logger.warning("icrawler returned no usable images for batch query: %s", query)
            else:
                logger.info("Fetched %d shared images via icrawler (aspect ratio filtered) for query: %s", len(results), query)
            return results
        except Exception as exc:
            logger.error("icrawler batch fetch failed: %s", exc)
            return []

    def _fetch_local_only(self, _query: str, target_path: Path) -> Optional[Path]:
        return self._fallback_image(target_path)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _check_aspect_ratio(self, image_path: Path, min_ratio: float = 9/16, max_ratio: float = 16/9) -> bool:
        """画像のアスペクト比をチェックし、16:9と9:16の範囲内か確認"""
        try:
            with Image.open(image_path) as img:
                width, height = img.size
                if height == 0:
                    logger.debug("画像の高さが0: %s", image_path.name)
                    return False
                aspect_ratio = width / height
                
                # アスペクト比が範囲内かチェック（9:16 から 16:9）
                is_valid = min_ratio <= aspect_ratio <= max_ratio
                
                if not is_valid:
                    logger.debug(
                        "画像を除外（アスペクト比: %.3f, 範囲: %.3f〜%.3f）: %s",
                        aspect_ratio,
                        min_ratio,
                        max_ratio,
                        image_path.name
                    )
                return is_valid
        except Exception as e:
            logger.warning("アスペクト比チェックエラー (%s): %s", image_path, e)
            return True  # エラー時は許可（既存の動作を維持）

    def _build_openverse_params(self, query: str, *, page_size: Optional[int] = None) -> Dict[str, str | int]:
        sanitized_query = query.strip()[:200]
        env_page_size = os.getenv("SHASHIN_OPENVERSE_PAGE_SIZE")
        effective_page_size = page_size if page_size is not None else self.openverse_page_size
        if env_page_size and env_page_size.isdigit() and page_size is None:
            effective_page_size = int(env_page_size)
        params: Dict[str, str | int] = {
            "q": sanitized_query,
            "page_size": max(1, min(effective_page_size, 50)),
        }

        license_override = (os.getenv("SHASHIN_OPENVERSE_LICENSES") or self.openverse_license_filter or "").strip()
        params["license"] = license_override or "cc0,by,by-sa"

        license_type = (os.getenv("SHASHIN_OPENVERSE_LICENSE_TYPE") or self.openverse_license_type or "").strip().lower()
        if license_type:
            if license_type in _ALLOWED_OPENVERSE_LICENSE_TYPES:
                params["license_type"] = license_type
            else:
                logger.warning(
                    "Ignoring invalid Openverse license_type '%s'. Allowed values: %s",
                    license_type,
                    ", ".join(sorted(_ALLOWED_OPENVERSE_LICENSE_TYPES)),
                )
        return params

    def _retry_openverse_without_license_type(
        self,
        exc: requests.HTTPError,
        url: str,
        params: Dict[str, str | int],
    ) -> Optional[requests.Response]:
        response = exc.response
        status_code = response.status_code if response else None
        body = (response.text if response else "")[:300]
        if (
            status_code == 400
            and "license_type" in params
            and "license_type" in (body or "").lower()
        ):
            logger.warning(
                "Openverse rejected license_type='%s'. Retrying without license_type (valid options: %s)",
                params["license_type"],
                ", ".join(sorted(_ALLOWED_OPENVERSE_LICENSE_TYPES)),
            )
            retry_params = {k: v for k, v in params.items() if k != "license_type"}
            try:
                retry_response = self._session.get(url, params=retry_params, timeout=20)
                retry_response.raise_for_status()
                self.openverse_license_type = None
                return retry_response
            except requests.RequestException as retry_exc:
                logger.error("Openverse retry without license_type failed: %s", retry_exc)
                return None
        logger.error(
            "Openverse request failed (%s): %s",
            status_code if status_code is not None else "HTTPError",
            body,
        )
        return None

    def _extract_first_image_url(self, html: str, *, pattern: re.Pattern[str]) -> Optional[str]:
        match = pattern.search(html)
        if match:
            return match.group(1)
        return None

    def _sanitize_identifier(self, value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", value)
        return cleaned[:48] or f"img_{uuid4().hex[:8]}"

    def _download_image(self, url: str, target_path: Path) -> Optional[Path]:
        try:
            response = self._session.get(url, timeout=20)
            response.raise_for_status()
            target_path.parent.mkdir(parents=True, exist_ok=True)
            content_type = response.headers.get("content-type", "")
            if "gif" in content_type.lower():
                target_path = target_path.with_suffix(".gif")
            target_path.write_bytes(response.content)
            final_path = self._ensure_jpeg(target_path)
            logger.info("Downloaded image: %s", final_path.name)
            return final_path
        except requests.RequestException as exc:
            logger.error("Image download failed: %s", exc)
            return None

    def _ensure_jpeg(self, path: Path) -> Path:
        try:
            image = Image.open(path).convert("RGB")
            if path.suffix.lower() not in {".jpg", ".jpeg"}:
                path = path.with_suffix(".jpg")
            image.save(path, format="JPEG", quality=90)
            return path
        except Exception as exc:
            logger.warning("Failed to re-encode image %s: %s", path.name, exc)
        return path

    def _fallback_image(self, target_path: Path) -> Optional[Path]:
        candidates: list[Path] = []
        if self.fallback_image and self.fallback_image.exists():
            candidates.append(self.fallback_image)

        reference_dir = self.reference_dir
        if reference_dir.exists():
            candidates.extend(reference_dir.glob("*.png"))
            candidates.extend(reference_dir.glob("*.jpg"))
            candidates.extend(reference_dir.glob("*.jpeg"))

        default_dir = Path("default_img")
        if default_dir.exists():
            candidates.extend(default_dir.glob("*.png"))
            candidates.extend(default_dir.glob("*.jpg"))
            candidates.extend(default_dir.glob("*.jpeg"))

        if not candidates:
            logger.error("No fallback images available")
            return None

        selected = random.choice(candidates)
        try:
            image = Image.open(selected).convert("RGB")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path = target_path.with_suffix(".jpg")
            image.save(target_path, format="JPEG", quality=90)
            logger.info("Using fallback image: %s", selected.name)
            return target_path
        except Exception as exc:
            logger.error("Failed to copy fallback image %s: %s", selected, exc)
            return None
