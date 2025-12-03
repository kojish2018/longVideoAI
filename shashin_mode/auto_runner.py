"""自動実行・スケジューリングモジュール"""
from __future__ import annotations

import random
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from logging_utils import get_logger

logger = get_logger(__name__)


class AutoRunner:
    """自動実行クラス"""
    
    def __init__(self, delays_hours: List[int] = None):
        self.delays_hours = delays_hours or [4, 8, 12]
    
    def run_pipeline(
        self,
        scripts: List[Path],
        start_time: datetime,
        config_path: Path = Path("config.yaml"),
        youtube_channel: Optional[str] = None
    ) -> None:
        """3つのスクリプトを順次処理"""
        if len(scripts) != len(self.delays_hours):
            logger.warning(f"スクリプト数({len(scripts)})と遅延時間数({len(self.delays_hours)})が一致しません")
        
        for idx, script_path in enumerate(scripts):
            if idx >= len(self.delays_hours):
                break
            
            delay_hours = self.delays_hours[idx]
            publish_at = start_time + timedelta(hours=delay_hours)
            
            logger.info(f"スクリプト {idx+1}/{len(scripts)} を処理: {script_path}")
            logger.info(f"予約投稿時刻: {publish_at}")
            
            self._run_single_script(
                script_path=script_path,
                publish_at=publish_at,
                config_path=config_path,
                youtube_channel=youtube_channel
            )
    
    def _run_single_script(
        self,
        script_path: Path,
        publish_at: datetime,
        config_path: Path,
        youtube_channel: Optional[str] = None
    ) -> None:
        """単一のスクリプトを実行"""
        # 予約投稿時刻をRFC3339形式に変換
        # タイムゾーンを考慮
        if publish_at.tzinfo is None:
            from zoneinfo import ZoneInfo
            publish_at = publish_at.replace(tzinfo=ZoneInfo("Asia/Tokyo"))
        
        publish_at_str = publish_at.strftime('%Y-%m-%dT%H:%M:%S%z')
        # タイムゾーン形式を調整（+0900 -> +09:00）
        if len(publish_at_str) > 19 and publish_at_str[-5] != ':':
            publish_at_str = publish_at_str[:-2] + ':' + publish_at_str[-2:]
        
        # コマンドを構築
        cmd = [
            'python', '-m', 'shashin_mode.main',
            str(script_path),
            '--upload',
            '--publish-at', publish_at_str,
            '--config', str(config_path),
            '--random-thumbnail',  # ランダム選択を有効化
        ]
        
        if youtube_channel:
            cmd.extend(['--youtube-channel', youtube_channel])
        
        logger.info(f"コマンド実行: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd,
                cwd=Path.cwd(),
                capture_output=True,
                text=True,
                timeout=3600  # 1時間タイムアウト
            )
            
            if result.returncode == 0:
                logger.info(f"スクリプト実行成功: {script_path}")
                if result.stdout:
                    logger.debug(f"標準出力: {result.stdout}")
            else:
                logger.error(f"スクリプト実行失敗: {script_path}")
                logger.error(f"エラー出力: {result.stderr}")
                if result.stdout:
                    logger.error(f"標準出力: {result.stdout}")
                
        except subprocess.TimeoutExpired:
            logger.error(f"スクリプト実行タイムアウト: {script_path}")
        except Exception as e:
            logger.error(f"スクリプト実行エラー: {e}")
            import traceback
            traceback.print_exc()
    
    def select_random_thumbnail_images(self, image_dir: Path, count: int = 2) -> tuple[Optional[Path], Optional[Path]]:
        """画像からランダムに2枚選択"""
        if not image_dir.exists():
            logger.warning(f"画像ディレクトリが存在しません: {image_dir}")
            return None, None
        
        image_files = list(image_dir.glob("*.jpg")) + list(image_dir.glob("*.png"))
        
        if len(image_files) < count:
            logger.warning(f"画像が{count}枚未満です: {len(image_files)}")
            if len(image_files) == 1:
                return image_files[0], image_files[0]
            return None, None
        
        selected = random.sample(image_files, count)
        logger.info(f"ランダム選択した画像: {[str(p) for p in selected]}")
        return selected[0], selected[1]

