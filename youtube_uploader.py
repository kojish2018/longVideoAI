"""YouTube アップロード機能（ロング動画向け）。"""
from __future__ import annotations

import logging
import mimetypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

try:  # pragma: no cover - optional dependency guard
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    _YOUTUBE_API_AVAILABLE = True
except ImportError:  # pragma: no cover - log at runtime
    logging.warning("google-api-python-client が見つかりません。YouTube アップロード機能は無効です。")
    _YOUTUBE_API_AVAILABLE = False


logger = logging.getLogger(__name__)


@dataclass
class UploadPayload:
    """アップロード時に必要なメタデータをまとめた構造体。"""

    video_path: Path
    title: str
    description: str
    tags: Sequence[str]
    publish_at: Optional[str]
    privacy: str
    category_id: str
    thumbnail_path: Optional[Path]


class YouTubeUploader:
    """YouTube Data API を用いたロング動画用アップローダー。"""

    SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

    def __init__(self, *, config: Dict[str, Any], credentials_dir: Path) -> None:
        self._config = config.get("youtube", {}) if isinstance(config, dict) else {}
        self._credentials_dir = credentials_dir
        self._credentials_path = credentials_dir / self._config.get(
            "credentials_file", "youtube_credentials.json"
        )
        self._token_path = credentials_dir / self._config.get(
            "token_file", "youtube_token.json"
        )
        self._default_privacy = str(self._config.get("default_privacy", "private"))
        self._default_category = str(self._config.get("default_category", "22"))
        self._default_tags = list(self._config.get("default_tags", ["ai", "longform", "documentary"]))
        self._youtube = None

        logger.info(
            "YouTubeUploader 初期化: privacy=%s, category_id=%s", self._default_privacy, self._default_category
        )

        if not _YOUTUBE_API_AVAILABLE:
            logger.error("YouTube API クライアントが利用できません。google-api-python-client をインストールしてください。")

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def authenticate(self) -> bool:
        """OAuth 2.0 認証を行い、YouTube サービスクライアントを用意する。"""
        if not _YOUTUBE_API_AVAILABLE:
            return False

        creds: Optional[Credentials] = None
        try:
            if self._token_path.exists():
                creds = Credentials.from_authorized_user_file(str(self._token_path), self.SCOPES)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    logger.info("YouTube アクセストークンをリフレッシュします。")
                    creds.refresh(Request())
                else:
                    if not self._credentials_path.exists():
                        logger.error("OAuth クライアント情報が見つかりません: %s", self._credentials_path)
                        return False
                    logger.info("YouTube OAuth フローを開始します。ブラウザで認証してください。")
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self._credentials_path), self.SCOPES
                    )
                    creds = flow.run_local_server(port=0)

                self._token_path.parent.mkdir(parents=True, exist_ok=True)
                with self._token_path.open("w", encoding="utf-8") as fh:
                    fh.write(creds.to_json())
                    logger.info("YouTube トークン情報を保存しました: %s", self._token_path)

            self._youtube = build("youtube", "v3", credentials=creds)
            logger.info("YouTube API 認証に成功しました。")
            return True

        except Exception as exc:  # pragma: no cover - network interaction
            logger.exception("YouTube 認証処理でエラーが発生しました: %s", exc)
            return False

    def upload(
        self,
        *,
        video_path: Path | str,
        title: str,
        description: str,
        tags: Optional[Sequence[str]] = None,
        publish_at: Optional[str] = None,
        thumbnail_path: Optional[Path | str] = None,
    ) -> Optional[str]:
        """動画をアップロードし、成功時は video_id を返す。"""
        if not _YOUTUBE_API_AVAILABLE:
            logger.error("YouTube API が利用できないためアップロードを中止します。")
            return None
        if self._youtube is None:
            logger.error("authenticate() を先に呼び出してください。")
            return None

        payload = self._build_payload(
            video_path=Path(video_path),
            title=title,
            description=description,
            tags=tags,
            publish_at=publish_at,
            thumbnail_path=Path(thumbnail_path) if thumbnail_path else None,
        )

        if payload is None:
            return None

        logger.info("YouTube へアップロードを開始します: %s", payload.video_path)
        try:
            body = {
                "snippet": {
                    "title": payload.title,
                    "description": payload.description,
                    "tags": list(payload.tags),
                    "categoryId": payload.category_id,
                },
                "status": {
                    "privacyStatus": payload.privacy,
                    "selfDeclaredMadeForKids": False,
                },
            }

            if payload.publish_at:
                body["status"]["publishAt"] = payload.publish_at
                logger.info("予約公開日時: %s", payload.publish_at)

            media = MediaFileUpload(
                str(payload.video_path),
                chunksize=-1,
                resumable=True,
                mimetype="video/mp4",
            )

            request = self._youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
            )

            response = self._resumable_upload(request)
            if not response:
                logger.error("YouTube アップロードが失敗しました。")
                return None

            video_id = response.get("id")
            if not video_id:
                logger.error("YouTube API から動画 ID が取得できませんでした: %s", response)
                return None

            logger.info("YouTube アップロード成功: video_id=%s", video_id)

            if payload.thumbnail_path:
                self._set_thumbnail(video_id=video_id, thumbnail_path=payload.thumbnail_path)

            return video_id

        except HttpError as exc:  # pragma: no cover - API call
            logger.error("YouTube API エラー: %s", exc)
            return None
        except Exception as exc:  # pragma: no cover - catch-all
            logger.exception("YouTube アップロード処理で予期せぬエラー: %s", exc)
            return None

    # ------------------------------------------------------------------
    # 内部ユーティリティ
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        *,
        video_path: Path,
        title: str,
        description: str,
        tags: Optional[Sequence[str]],
        publish_at: Optional[str],
        thumbnail_path: Optional[Path],
    ) -> Optional[UploadPayload]:
        if not video_path.exists():
            logger.error("動画ファイルが見つかりません: %s", video_path)
            return None

        normalised_tags = list(tags) if tags else list(self._default_tags)
        normalised_tags = [tag for tag in normalised_tags if tag]
        if len(normalised_tags) > 500:
            normalised_tags = normalised_tags[:500]

        iso_publish_at = self._normalise_publish_at(publish_at)
        privacy = self._default_privacy if iso_publish_at is None else "private"

        return UploadPayload(
            video_path=video_path,
            title=title.strip()[:100] or "AI Generated Video",
            description=description.strip() or "Generated by LongVideoAI",
            tags=tuple(normalised_tags),
            publish_at=iso_publish_at,
            privacy=privacy,
            category_id=self._default_category,
            thumbnail_path=thumbnail_path if thumbnail_path and thumbnail_path.exists() else None,
        )

    def _normalise_publish_at(self, publish_at: Optional[str]) -> Optional[str]:
        if not publish_at:
            return None

        text = publish_at.strip()
        if not text:
            return None

        try:
            # ISO 8601 形式を優先的に処理
            if "T" in text:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(text)
        except ValueError:
            logger.warning("publish_at を ISO 8601 として解釈できませんでした: %s", publish_at)
            return None

        if dt.tzinfo is None:
            logger.warning("タイムゾーン情報のない日時は即時公開扱いにフォールバックします: %s", publish_at)
            return None

        iso_text = dt.astimezone().isoformat(timespec="seconds")
        logger.debug("publish_at 正規化: %s -> %s", publish_at, iso_text)
        return iso_text

    def _resumable_upload(self, request):
        response = None
        error = None
        retry = 0
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    logger.info("YouTube アップロード進捗: %d%%", int(status.progress() * 100))
            except HttpError as exc:  # pragma: no cover - API call
                if exc.resp.status in {500, 502, 503, 504}:
                    error = exc
                    retry += 1
                    if retry > 5:
                        logger.error("YouTube アップロードのリトライ回数が上限に達しました。")
                        raise
                    backoff = 2 ** retry
                    logger.warning("一時的なエラーのためリトライします (%d 秒後): %s", backoff, error)
                    import time

                    time.sleep(backoff)
                    continue
                raise
            except Exception:
                logger.exception("YouTube アップロードのレジューム処理でエラーが発生しました。")
                raise
        return response

    def _set_thumbnail(self, *, video_id: str, thumbnail_path: Path) -> None:
        if not thumbnail_path.exists():
            logger.warning("サムネイル画像が見つからないためスキップします: %s", thumbnail_path)
            return

        logger.info("YouTube サムネイルを設定します: %s", thumbnail_path)
        try:
            guessed_type, _ = mimetypes.guess_type(str(thumbnail_path))
            mimetype = guessed_type or "image/png"
            media = MediaFileUpload(str(thumbnail_path), mimetype=mimetype)
            request = self._youtube.thumbnails().set(videoId=video_id, media_body=media)
            request.execute()
        except HttpError as exc:  # pragma: no cover - API call
            logger.error("サムネイル設定に失敗しました: %s", exc)
        except Exception:  # pragma: no cover - catch-all
            logger.exception("サムネイル設定処理で予期せぬエラーが発生しました")


__all__ = ["YouTubeUploader"]
