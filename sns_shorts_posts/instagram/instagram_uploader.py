"""Instagram Reels API uploader.

This module provides functionality to upload videos to Instagram Reels
via the Instagram Graph API.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv


# Instagram Graph API base URL (Facebook Login方式 - Resumable Upload対応)
API_BASE = "https://graph.facebook.com/v24.0"
RUPLOAD_BASE = "https://rupload.facebook.com"

# Maximum wait time for container processing (5 minutes)
MAX_WAIT_TIME = 300

# Polling interval for container status check (5 seconds)
POLL_INTERVAL = 5


def _load_credentials() -> Dict[str, str]:
    """Load Instagram API credentials from environment."""
    load_dotenv()
    return {
        "app_id": os.environ.get("INSTAGRAM_APP_ID", ""),
        "app_secret": os.environ.get("INSTAGRAM_APP_SECRET", ""),
        "access_token": os.environ.get("INSTAGRAM_ACCESS_TOKEN", ""),
        "business_account_id": os.environ.get("INSTAGRAM_BUSINESS_ACCOUNT_ID", ""),
    }


def _get_instagram_user_id_from_page(access_token: str) -> str:
    """Get Instagram Business Account ID from Facebook page.
    
    Args:
        access_token: Facebook Page access token
    
    Returns:
        Instagram Business Account ID
    """
    print("[Instagram] Getting Instagram Business Account ID from Facebook page...")
    
    # Get pages
    pages_url = f"{API_BASE}/me/accounts"
    resp = requests.get(
        pages_url,
        params={
            "access_token": access_token,
            "fields": "id,name,instagram_business_account",
        },
        timeout=30,
    )
    
    if not resp.ok:
        error_text = resp.text[:500] if resp.text else ""
        raise RuntimeError(
            f"Failed to get pages: HTTP {resp.status_code} - {error_text}"
        )
    
    try:
        pages_data = resp.json()
    except Exception as e:
        raise RuntimeError(
            f"Failed to parse pages response: {e} - {resp.text[:500]}"
        )
    
    # Check for API errors
    if "error" in pages_data:
        error_info = pages_data["error"]
        raise RuntimeError(
            f"Instagram API error: {error_info.get('message', 'Unknown error')} "
            f"(code: {error_info.get('code', 'unknown')})"
        )
    
    pages = pages_data.get("data", [])
    if not pages:
        raise RuntimeError(
            "No Facebook pages found. Make sure you have admin access to a page "
            "that is connected to your Instagram Business account."
        )
    
    # Find page with Instagram account
    for page in pages:
        insta_account = page.get("instagram_business_account", {})
        if insta_account:
            insta_id = insta_account.get("id", "")
            if insta_id:
                print(f"[Instagram] Found Instagram Business Account ID: {insta_id}")
                return insta_id
    
    raise RuntimeError(
        "No Instagram Business Account found. Make sure your Facebook page "
        "is connected to an Instagram Business account."
    )


def _create_resumable_media_container(
    *,
    business_account_id: str,
    video_path: Path,
    caption: str,
    access_token: str,
    cover_url: Optional[str] = None,
) -> str:
    """Create a resumable media container for Instagram Reels upload.
    
    Args:
        business_account_id: Instagram Business Account ID
        video_path: Path to the video file
        caption: Caption text (max 2200 characters)
        access_token: Instagram access token
        cover_url: Optional cover image URL
    
    Returns:
        Container ID
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    
    size = video_path.stat().st_size
    if size <= 0:
        raise ValueError(f"Video file is empty: {video_path}")
    
    print(f"[Instagram] Creating resumable media container: {video_path} (size: {size} bytes)")
    
    container_url = f"{API_BASE}/{business_account_id}/media"
    
    # Create container with upload_type=resumable
    data = {
        "media_type": "REELS",
        "caption": caption[:2200],  # Instagram caption limit
        "upload_type": "resumable",
        "access_token": access_token,
    }
    
    if cover_url:
        data["cover_url"] = cover_url
    
    # Send JSON request (not multipart)
    resp = requests.post(
        container_url,
        json=data,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    
    if not resp.ok:
        error_text = resp.text[:500] if resp.text else ""
        raise RuntimeError(
            f"Failed to create media container: HTTP {resp.status_code} - {error_text}"
        )
    
    try:
        container_data = resp.json()
    except Exception as e:
        raise RuntimeError(
            f"Failed to parse container response: {e} - {resp.text[:500]}"
        )
    
    # Check for API errors
    if "error" in container_data:
        error_info = container_data["error"]
        raise RuntimeError(
            f"Instagram API error: {error_info.get('message', 'Unknown error')} "
            f"(code: {error_info.get('code', 'unknown')})"
        )
    
    container_id = container_data.get("id")
    if not container_id:
        raise RuntimeError(
            f"Container ID not found in response: {json.dumps(container_data, ensure_ascii=False)}"
        )
    
    print(f"[Instagram] Resumable media container created: {container_id}")
    return container_id


def _upload_video_to_rupload(
    *,
    container_id: str,
    video_path: Path,
    access_token: str,
) -> None:
    """Upload video file to rupload.facebook.com.
    
    Args:
        container_id: Instagram Media Container ID
        video_path: Path to the video file
        access_token: Instagram access token
    
    Raises:
        RuntimeError: If upload fails
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    
    size = video_path.stat().st_size
    if size <= 0:
        raise ValueError(f"Video file is empty: {video_path}")
    
    print(f"[Instagram] Uploading video to rupload.facebook.com...")
    
    # Extract API version from API_BASE (e.g., "v24.0")
    api_version = API_BASE.split("/")[-1]
    upload_url = f"{RUPLOAD_BASE}/ig-api-upload/{api_version}/{container_id}"
    
    # Read video file
    with open(video_path, "rb") as video_file:
        headers = {
            "Authorization": f"OAuth {access_token}",
            "offset": "0",
            "file_size": str(size),
        }
        
        resp = requests.post(
            upload_url,
            headers=headers,
            data=video_file,
            timeout=600,  # 10 minutes timeout for large files
        )
    
    if not resp.ok:
        error_text = resp.text[:500] if resp.text else ""
        raise RuntimeError(
            f"Failed to upload video: HTTP {resp.status_code} - {error_text}"
        )
    
    try:
        upload_data = resp.json()
    except Exception as e:
        raise RuntimeError(
            f"Failed to parse upload response: {e} - {resp.text[:500]}"
        )
    
    # Check for errors
    if "error" in upload_data:
        error_info = upload_data["error"]
        raise RuntimeError(
            f"Upload error: {error_info.get('message', 'Unknown error')} "
            f"(code: {error_info.get('code', 'unknown')})"
        )
    
    # Check for success
    if upload_data.get("success") is True:
        print("[Instagram] Video uploaded successfully!")
    else:
        # Check debug_info for errors
        debug_info = upload_data.get("debug_info", {})
        if debug_info.get("type") == "ProcessingFailedError":
            error_msg = debug_info.get("message", "Unknown error")
            raise RuntimeError(f"Upload failed: {error_msg}")
        else:
            raise RuntimeError(
                f"Upload response indicates failure: {json.dumps(upload_data, ensure_ascii=False)}"
            )


def _wait_for_container_ready(
    *,
    container_id: str,
    access_token: str,
    max_wait: int = MAX_WAIT_TIME,
    poll_interval: int = POLL_INTERVAL,
) -> None:
    """Wait for media container to be ready for publishing.
    
    Args:
        container_id: Container ID to check
        access_token: Instagram access token
        max_wait: Maximum wait time in seconds
        poll_interval: Polling interval in seconds
    
    Raises:
        RuntimeError: If container processing fails or times out
    """
    print("[Instagram] Waiting for container to be ready...")
    status_url = f"{API_BASE}/{container_id}"
    wait_time = 0
    
    while wait_time < max_wait:
        try:
            status_resp = requests.get(
                status_url,
                params={
                    "fields": "status_code",
                    "access_token": access_token,
                },
                timeout=30,
            )
            
            if not status_resp.ok:
                raise RuntimeError(
                    f"Failed to check container status: HTTP {status_resp.status_code}"
                )
            
            status_data = status_resp.json()
            
            # Check for API errors
            if "error" in status_data:
                error_info = status_data["error"]
                raise RuntimeError(
                    f"Instagram API error: {error_info.get('message', 'Unknown error')} "
                    f"(code: {error_info.get('code', 'unknown')})"
                )
            
            status_code = status_data.get("status_code")
            
            if status_code == "FINISHED":
                print("[Instagram] Container is ready!")
                return
            elif status_code == "ERROR":
                raise RuntimeError(
                    f"Container processing failed: {json.dumps(status_data, ensure_ascii=False)}"
                )
            
            # Still processing
            time.sleep(poll_interval)
            wait_time += poll_interval
            print(f"[Instagram] Still processing... (waited {wait_time}s)")
        
        except requests.exceptions.RequestException as e:
            print(f"[Instagram] Error checking status: {e}, retrying...")
            time.sleep(poll_interval)
            wait_time += poll_interval
    
    raise RuntimeError(
        f"Container processing timeout after {max_wait} seconds"
    )


def _publish_reel(
    *,
    business_account_id: str,
    container_id: str,
    access_token: str,
) -> str:
    """Publish the reel.
    
    Args:
        business_account_id: Instagram Business Account ID
        container_id: Container ID to publish
        access_token: Instagram access token
    
    Returns:
        Published media ID
    """
    print("[Instagram] Publishing reel...")
    publish_url = f"{API_BASE}/{business_account_id}/media_publish"
    publish_params = {
        "creation_id": container_id,
        "access_token": access_token,
    }
    
    publish_resp = requests.post(publish_url, params=publish_params, timeout=30)
    
    if not publish_resp.ok:
        error_text = publish_resp.text[:500] if publish_resp.text else ""
        raise RuntimeError(
            f"Failed to publish reel: HTTP {publish_resp.status_code} - {error_text}"
        )
    
    try:
        publish_data = publish_resp.json()
    except Exception as e:
        raise RuntimeError(
            f"Failed to parse publish response: {e} - {publish_resp.text[:500]}"
        )
    
    # Check for API errors
    if "error" in publish_data:
        error_info = publish_data["error"]
        raise RuntimeError(
            f"Instagram API error: {error_info.get('message', 'Unknown error')} "
            f"(code: {error_info.get('code', 'unknown')})"
        )
    
    media_id = publish_data.get("id")
    if not media_id:
        raise RuntimeError(
            f"Media ID not found in response: {json.dumps(publish_data, ensure_ascii=False)}"
        )
    
    print(f"[Instagram] Reel published successfully! Media ID: {media_id}")
    return media_id


def upload_reel(
    *,
    video_path: Path,
    caption: str,
    cover_url: Optional[str] = None,
    access_token: Optional[str] = None,
    instagram_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload a video as Instagram Reel.
    
    Args:
        video_path: Path to MP4 file (max 90 seconds, 9:16 aspect ratio recommended)
        caption: Caption text (max 2200 characters)
        cover_url: Optional cover image URL
        access_token: Instagram access token (if None, loaded from env)
        instagram_user_id: Instagram User ID (if None, fetched from /me endpoint)
    
    Returns:
        Dict containing the response with media_id and other metadata
    
    Raises:
        FileNotFoundError: If video file doesn't exist
        ValueError: If video file is empty
        RuntimeError: If API calls fail
    """
    creds = _load_credentials()
    
    if access_token is None:
        access_token = creds["access_token"]
    
    if not access_token:
        raise RuntimeError(
            "Instagram access token not found. Set INSTAGRAM_ACCESS_TOKEN in .env"
        )
    
    # Get Instagram Business Account ID if not provided
    if instagram_user_id is None:
        # Try to get from env first
        instagram_user_id = creds.get("business_account_id", "")
        if not instagram_user_id:
            # Get from Facebook page
            instagram_user_id = _get_instagram_user_id_from_page(access_token)
    
    # Step 1: Create resumable media container
    container_id = _create_resumable_media_container(
        business_account_id=instagram_user_id,
        video_path=video_path,
        caption=caption,
        access_token=access_token,
        cover_url=cover_url,
    )
    
    # Step 2: Upload video to rupload.facebook.com
    _upload_video_to_rupload(
        container_id=container_id,
        video_path=video_path,
        access_token=access_token,
    )
    
    # Step 3: Wait for container to be ready
    _wait_for_container_ready(
        container_id=container_id,
        access_token=access_token,
    )
    
    # Step 4: Publish the reel
    media_id = _publish_reel(
        business_account_id=instagram_user_id,
        container_id=container_id,
        access_token=access_token,
    )
    
    return {
        "id": media_id,
        "container_id": container_id,
        "status": "published",
    }


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Upload a local MP4 to Instagram Reels via Graph API."
    )
    parser.add_argument(
        "--video",
        required=True,
        help="Path to the MP4 file generated by longVideoAI.",
    )
    parser.add_argument(
        "--caption",
        default="自動生成された動画 #AI #自動生成",
        help="Caption text (max 2200 characters).",
    )
    parser.add_argument(
        "--cover-url",
        help="Cover image URL (optional).",
    )
    parser.add_argument(
        "--access-token",
        help="Instagram access token. If omitted, read from INSTAGRAM_ACCESS_TOKEN.",
    )
    parser.add_argument(
        "--instagram-user-id",
        help="Instagram User ID. If omitted, fetched from /me endpoint.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    
    try:
        video_path = Path(args.video).expanduser().resolve()
        
        print(f"[Instagram] Uploading reel: {video_path}")
        result = upload_reel(
            video_path=video_path,
            caption=args.caption,
            cover_url=args.cover_url,
            access_token=args.access_token,
            instagram_user_id=args.instagram_user_id,
        )
        
        print(f"\n[Instagram] Success! Media ID: {result.get('id')}")
        print(f"[Instagram] Container ID: {result.get('container_id')}")
        print(f"[Instagram] Status: {result.get('status')}")
        return 0
    
    except Exception as exc:
        print(f"\n[Instagram] Error: {exc}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

