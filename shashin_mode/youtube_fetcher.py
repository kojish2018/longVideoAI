"""YouTube video fetcher for shashin_mode.

Uses yt-dlp to download videos and extract metadata.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from logging_utils import get_logger

logger = get_logger(__name__)


@dataclass
class YouTubeVideo:
    """YouTube動画のメタデータと関連ファイルパス"""
    video_id: str
    title: str
    description: str
    channel: str
    duration: float  # 秒
    video_path: Path
    audio_path: Path
    
    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


class YouTubeFetcher:
    """YouTube動画をダウンロードしてメタデータを取得"""
    
    def __init__(
        self,
        output_dir: Optional[Path] = None,
        max_duration: int = 1800,  # 最大30分
        audio_only: bool = False,
    ) -> None:
        self.output_dir = output_dir or Path(tempfile.mkdtemp(prefix="yt_"))
        self.max_duration = max_duration
        self.audio_only = audio_only
        self._check_yt_dlp()
    
    def _check_yt_dlp(self) -> None:
        """yt-dlpがインストールされているか確認"""
        try:
            result = subprocess.run(
                ["yt-dlp", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                logger.info("yt-dlp version: %s", result.stdout.strip())
            else:
                raise RuntimeError("yt-dlp is not working properly")
        except FileNotFoundError:
            raise RuntimeError(
                "yt-dlp is not installed. Install with: pip install yt-dlp"
            )
    
    def fetch(self, url: str) -> YouTubeVideo:
        """YouTube動画をダウンロードしてメタデータを取得
        
        Args:
            url: YouTube動画のURL
            
        Returns:
            YouTubeVideo: ダウンロードした動画の情報
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # まずメタデータを取得
        metadata = self._get_metadata(url)
        video_id = metadata.get("id", "unknown")
        title = metadata.get("title", "Untitled")
        description = metadata.get("description", "")
        channel = metadata.get("channel", metadata.get("uploader", "Unknown"))
        duration = float(metadata.get("duration", 0))
        
        logger.info("動画情報取得: %s (%.1f分)", title, duration / 60)
        
        if duration > self.max_duration:
            logger.warning(
                "動画が長すぎます (%.1f分 > %.1f分)。最初の%.1f分のみ処理します。",
                duration / 60,
                self.max_duration / 60,
                self.max_duration / 60,
            )
        
        # 動画/音声をダウンロード
        video_path, audio_path = self._download(url, video_id)
        
        return YouTubeVideo(
            video_id=video_id,
            title=title,
            description=description,
            channel=channel,
            duration=duration,
            video_path=video_path,
            audio_path=audio_path,
        )
    
    def _get_metadata(self, url: str) -> dict:
        """動画のメタデータを取得"""
        try:
            result = subprocess.run(
                [
                    "yt-dlp",
                    "--dump-json",
                    "--no-download",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                logger.error("メタデータ取得エラー: %s", result.stderr)
                raise RuntimeError(f"Failed to get metadata: {result.stderr}")
            
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            logger.error("メタデータのパースエラー: %s", e)
            raise RuntimeError(f"Failed to parse metadata: {e}")
    
    def _download(self, url: str, video_id: str) -> tuple[Path, Path]:
        """動画と音声をダウンロード"""
        video_path = self.output_dir / f"{video_id}.mp4"
        audio_path = self.output_dir / f"{video_id}.mp3"
        
        # 音声のみの場合
        if self.audio_only:
            cmd = [
                "yt-dlp",
                "-x",  # 音声のみ抽出
                "--audio-format", "mp3",
                "--audio-quality", "0",  # 最高品質
                "-o", str(audio_path.with_suffix("")),  # 拡張子は自動付与
                url,
            ]
            if self.max_duration:
                cmd.extend(["--download-sections", f"*0:00-{self._format_duration(self.max_duration)}"])
            
            logger.info("音声ダウンロード中...")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            
            if result.returncode != 0:
                logger.error("ダウンロードエラー: %s", result.stderr)
                raise RuntimeError(f"Download failed: {result.stderr}")
            
            # yt-dlpは拡張子を自動で付けるので確認
            if not audio_path.exists():
                # 別の拡張子で保存されている可能性
                for ext in [".mp3", ".m4a", ".opus", ".webm"]:
                    alt_path = self.output_dir / f"{video_id}{ext}"
                    if alt_path.exists():
                        audio_path = alt_path
                        break
            
            logger.info("音声ダウンロード完了: %s", audio_path.name)
            return video_path, audio_path
        
        # 動画と音声両方
        cmd = [
            "yt-dlp",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "-o", str(video_path),
            url,
        ]
        if self.max_duration:
            cmd.extend(["--download-sections", f"*0:00-{self._format_duration(self.max_duration)}"])
        
        logger.info("動画ダウンロード中...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        
        if result.returncode != 0:
            logger.error("ダウンロードエラー: %s", result.stderr)
            raise RuntimeError(f"Download failed: {result.stderr}")
        
        logger.info("動画ダウンロード完了: %s", video_path.name)
        
        # 音声を抽出
        audio_path = self._extract_audio(video_path)
        
        return video_path, audio_path
    
    def _extract_audio(self, video_path: Path) -> Path:
        """動画から音声を抽出"""
        audio_path = video_path.with_suffix(".mp3")
        
        cmd = [
            "ffmpeg",
            "-i", str(video_path),
            "-vn",  # 映像なし
            "-acodec", "libmp3lame",
            "-q:a", "2",  # 高品質
            "-y",  # 上書き
            str(audio_path),
        ]
        
        logger.info("音声抽出中...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode != 0:
            logger.error("音声抽出エラー: %s", result.stderr)
            raise RuntimeError(f"Audio extraction failed: {result.stderr}")
        
        logger.info("音声抽出完了: %s", audio_path.name)
        return audio_path
    
    @staticmethod
    def _format_duration(seconds: int) -> str:
        """秒をHH:MM:SS形式に変換"""
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours}:{minutes:02d}:{secs:02d}"


def extract_video_id(url: str) -> Optional[str]:
    """URLからYouTube動画IDを抽出"""
    import re
    
    patterns = [
        r"(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"youtube\.com/embed/([a-zA-Z0-9_-]{11})",
        r"youtube\.com/v/([a-zA-Z0-9_-]{11})",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

