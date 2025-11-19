"""Minimal TikTok Content Posting API uploader for Sandbox testing.

This module provides a small CLI that:
- Uploads a local MP4 file to TikTok via the Content Posting API (inbox upload flow).
- Is designed to be invoked from the longVideoAI workspace for demo recording.

It does NOT perform OAuth. You are expected to obtain a user access token
via the TikTok Developer Portal or another flow and pass it via environment
variable or CLI argument.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv


# TikTok Content Posting base URL.
API_BASE = "https://open.tiktokapis.com"


def _load_access_token(env_var: str = "TIKTOK_ACCESS_TOKEN") -> str:
    """Get access token from environment or raise a clear error."""

    load_dotenv()
    token = os.environ.get(env_var)
    if not token:
        raise RuntimeError(
            f"Access token not found. Please set {env_var} in your environment or .env file."
        )
    return token


def _init_inbox_upload(
    *,
    access_token: str,
    video_size: int,
    chunk_size: Optional[int] = None,
) -> Dict[str, Any]:
    """Call TikTok inbox video init endpoint and return JSON response.

    This uses a single-chunk FILE_UPLOAD flow suitable for small/medium videos.
    """

    if chunk_size is None or chunk_size <= 0:
        chunk_size = video_size

    url = f"{API_BASE}/v2/post/publish/inbox/video/init/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    total_chunk_count = int((video_size + chunk_size - 1) // chunk_size)
    payload = {
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunk_count,
        }
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    try:
        data = resp.json()
    except Exception:
        resp.raise_for_status()
        raise

    if not resp.ok:
        raise RuntimeError(f"Init upload failed: HTTP {resp.status_code} - {data}")

    # TikTok API returns {"error": {"code": "ok", ...}} on success.
    error_info = data.get("error")
    if error_info and str(error_info.get("code")) not in {"ok", "0"}:
        raise RuntimeError(f"Init upload error: {json.dumps(data, ensure_ascii=False)}")

    return data


def _upload_video_file(upload_url: str, video_path: Path) -> None:
    """PUT the video binary to the provided upload_url as a single chunk."""

    size = video_path.stat().st_size
    if size <= 0:
        raise ValueError(f"Video file is empty: {video_path}")

    start = 0
    end = size - 1
    headers = {
        "Content-Type": "video/mp4",
        "Content-Length": str(size),
        "Content-Range": f"bytes {start}-{end}/{size}",
    }

    with video_path.open("rb") as f:
        resp = requests.put(upload_url, data=f, headers=headers, timeout=600)

    if not resp.ok:
        snippet = resp.text[:500] if resp.text else ""
        raise RuntimeError(
            f"Video upload failed: HTTP {resp.status_code} - {snippet}"
        )


def upload_to_tiktok_inbox(
    *,
    video_path: Path,
    caption: str,
    access_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload a video to TikTok inbox via Content Posting API (inbox flow).

    Returns the full JSON response from the init endpoint.
    """

    if access_token is None:
        access_token = _load_access_token()

    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    size = video_path.stat().st_size
    if size <= 0:
        raise ValueError(f"Video file is empty: {video_path}")

    print(f"[TikTok] Init inbox upload: file={video_path} size={size} bytes")
    init_data = _init_inbox_upload(
        access_token=access_token,
        video_size=size,
    )

    upload_url = init_data.get("data", {}).get("upload_url")
    if not upload_url:
        raise RuntimeError(
            f"upload_url not found in response: {json.dumps(init_data, ensure_ascii=False)}"
        )

    print(f"[TikTok] Caption (for reference only in inbox flow): {caption}")
    print("[TikTok] Uploading video binary to TikTok servers...")
    _upload_video_file(upload_url, video_path)
    print("[TikTok] Upload finished.")

    # NOTE:
    #   In the inbox upload flow, the user finalizes caption and posting
    #   inside the TikTok app (inbox). This script's primary purpose is to
    #   demonstrate that a local tool can send videos to TikTok.

    print("[TikTok] Init response JSON:")
    print(json.dumps(init_data, ensure_ascii=False, indent=2))
    return init_data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload a local MP4 to TikTok inbox via Content Posting API (sandbox‑friendly)."
    )
    parser.add_argument(
        "--video",
        required=True,
        help="Path to the MP4 file generated by longVideoAI.",
    )
    parser.add_argument(
        "--caption",
        default="Test upload from longVideoAI",
        help="Caption text (logged only; actual caption is set in TikTok app for inbox flow).",
    )
    parser.add_argument(
        "--access-token",
        help="TikTok user access token. If omitted, read from TIKTOK_ACCESS_TOKEN.",
    )
    parser.add_argument(
        "--env-var",
        default="TIKTOK_ACCESS_TOKEN",
        help="Environment variable name that holds the access token (default: TIKTOK_ACCESS_TOKEN).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        access_token = args.access_token or _load_access_token(args.env_var)
        video_path = Path(args.video).expanduser().resolve()

        print(
            f"[TikTok] Using access token from "
            f"{args.env_var if not args.access_token else 'CLI argument'}"
        )
        upload_to_tiktok_inbox(
            video_path=video_path,
            caption=args.caption,
            access_token=access_token,
        )
        print("[TikTok] Done. Check your TikTok app inbox / sandbox account.")
        return 0
    except Exception as exc:
        print(f"[TikTok] Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
