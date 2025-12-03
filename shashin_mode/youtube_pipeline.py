"""YouTube動画から自動的に動画を生成するパイプライン

使用方法:
    python -m shashin_mode.youtube_pipeline "https://www.youtube.com/watch?v=xxxxx"
    python -m shashin_mode.youtube_pipeline urls.txt  # 複数URL（1行1URL）
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from config_loader import load_config
from logging_utils import configure_logging, get_logger

logger = get_logger(__name__)


@dataclass
class YouTubeArticle:
    """YouTube動画をArticleとして扱うためのアダプター"""
    title: str
    content: str  # 文字起こしテキスト
    url: str
    channel: str
    duration: float
    
    @property
    def pub_date(self) -> datetime:
        return datetime.now()


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="YouTube動画から自動動画生成パイプライン"
    )
    parser.add_argument(
        "input",
        help="YouTube URL または URLリストファイル（.txt）",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="設定ファイルパス (default: config.yaml)",
    )
    parser.add_argument(
        "--output-dir",
        default="shashin_mode/scripts/youtube_generated",
        help="台本出力ディレクトリ",
    )
    parser.add_argument(
        "--model",
        default="medium",
        choices=["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"],
        help="Whisperモデルサイズ (default: medium)",
    )
    parser.add_argument(
        "--max-duration",
        type=int,
        default=900,  # 15分
        help="最大処理時間（秒）。長い動画の最初の部分のみ処理 (default: 900)",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="YouTubeにアップロードする",
    )
    parser.add_argument(
        "--publish-delay",
        type=int,
        default=4,
        help="公開までの遅延時間（時間）。--publish-at未指定時に使用 (default: 4)",
    )
    parser.add_argument(
        "--publish-at",
        help="予約投稿日時 (例: '2025-11-30 18:00' or '2025-11-30T18:00:00+09:00')",
    )
    parser.add_argument(
        "--skip-transcribe",
        action="store_true",
        help="文字起こしをスキップ（タイトルと説明文のみ使用）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="台本生成のみ行い、動画生成はスキップ",
    )
    
    args = parser.parse_args(argv)
    
    # ライブラリのインポート確認
    try:
        from .youtube_fetcher import YouTubeFetcher, extract_video_id
        from .transcriber import Transcriber, check_whisper_available
        from .gemini_client import GeminiClient
        from .script_generator import ScriptGenerator
        from .auto_runner import AutoRunner
    except ImportError as e:
        logger.error("必要なモジュールのインポートに失敗: %s", e)
        sys.exit(1)
    
    # Whisperの確認
    if not args.skip_transcribe:
        available, backend = check_whisper_available()
        if not available:
            logger.error(
                "Whisperがインストールされていません。"
                "pip install faster-whisper または pip install openai-whisper を実行してください。"
            )
            sys.exit(1)
        logger.info("Whisper backend: %s", backend)
    
    # URLリストの読み込み
    urls = _parse_input(args.input)
    if not urls:
        logger.error("有効なURLが見つかりません")
        sys.exit(1)
    
    logger.info("%d件のYouTube URLを処理します", len(urls))
    
    # 設定ファイル読み込み
    config_path = Path(args.config)
    config = load_config(config_path, project_root=Path.cwd())
    
    # ログ設定
    log_dir = Path("shashin_mode/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    start_time = datetime.now()
    log_path = log_dir / f"youtube_pipeline_{start_time.strftime('%Y%m%d_%H%M%S')}.log"
    configure_logging(config.logging_level, log_path)
    
    logger.info("YouTube パイプライン開始")
    
    prompt_template_path = Path("scripts_prompts/保守系.md")
    
    if not prompt_template_path.exists():
        logger.error("プロンプトテンプレートが見つかりません: %s", prompt_template_path)
        sys.exit(1)
    
    # 各コンポーネントの初期化
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Gemini API キーをconfig.yamlから取得
    try:
        gemini_config = config.raw.get('apis', {}).get('gemini', {}) if isinstance(config.raw, dict) else {}
        gemini_api_key = gemini_config.get('api_key') or None
        gemini_client = GeminiClient(api_key=gemini_api_key)
    except Exception as e:
        logger.error("Gemini APIの初期化に失敗: %s", e)
        sys.exit(1)
    
    youtube_fetcher = YouTubeFetcher(
        output_dir=output_dir / "downloads",
        max_duration=args.max_duration,
        audio_only=True,  # 文字起こしには音声のみで十分
    )
    
    if not args.skip_transcribe:
        transcriber = Transcriber(
            model_size=args.model,
            language="ja",
        )
    
    script_generator = ScriptGenerator(prompt_template_path)
    
    # 処理開始
    scripts: List[Path] = []
    
    for idx, url in enumerate(urls, start=1):
        logger.info("=" * 60)
        logger.info("処理中 [%d/%d]: %s", idx, len(urls), url)
        
        try:
            # 1. YouTube動画をダウンロード
            video = youtube_fetcher.fetch(url)
            logger.info("タイトル: %s", video.title)
            logger.info("チャンネル: %s", video.channel)
            logger.info("長さ: %.1f分", video.duration / 60)
            
            # 2. 文字起こし
            if args.skip_transcribe:
                content = f"{video.title}\n\n{video.description}"
                logger.info("文字起こしスキップ: タイトルと説明文を使用")
            else:
                logger.info("文字起こし中...")
                transcript = transcriber.transcribe(
                    video.audio_path,
                    max_duration=args.max_duration,
                )
                content = transcript.text
                logger.info("文字起こし完了: %d文字", len(content))
            
            # 3. Articleオブジェクトを作成
            article = YouTubeArticle(
                title=video.title,
                content=content,
                url=video.url,
                channel=video.channel,
                duration=video.duration,
            )
            
            # 4. 台本生成
            script_path = script_generator.generate_script_file(
                article=article,
                gemini_client=gemini_client,
                output_dir=output_dir,
                index=idx,
            )
            scripts.append(script_path)
            logger.info("台本生成完了: %s", script_path)
            
        except Exception as e:
            logger.error("処理失敗 [%s]: %s", url, e)
            continue
    
    logger.info("=" * 60)
    logger.info("%d件の台本を生成しました", len(scripts))
    
    if args.dry_run:
        logger.info("ドライラン: 動画生成をスキップ")
        for script in scripts:
            logger.info("  - %s", script)
        return
    
    if not scripts:
        logger.warning("生成された台本がありません")
        return
    
    # 5. 動画生成・アップロード
    if args.upload:
        import subprocess
        
        if args.publish_at:
            # 日時指定の場合は直接main.pyを呼び出す
            publish_at_str = _parse_publish_at(args.publish_at)
            if not publish_at_str:
                logger.error("無効な日時形式: %s", args.publish_at)
                sys.exit(1)
            
            logger.info("予約投稿日時: %s", publish_at_str)
            
            for idx, script in enumerate(scripts):
                # 複数動画の場合は4時間ずつずらす
                if idx > 0:
                    publish_dt = _add_hours_to_publish_at(args.publish_at, idx * 4)
                    publish_at_str = _parse_publish_at(publish_dt)
                
                cmd = [
                    "python", "-m", "shashin_mode.main",
                    str(script),
                    "--upload",
                    "--publish-at", publish_at_str,
                    "--config", str(config_path),
                    "--random-thumbnail",
                ]
                logger.info("実行: %s", " ".join(cmd))
                try:
                    subprocess.run(cmd, check=True)
                except subprocess.CalledProcessError as e:
                    logger.error("動画生成失敗: %s", e)
        else:
            # 遅延時間指定の場合はAutoRunnerを使用
            auto_runner = AutoRunner(
                delays_hours=[args.publish_delay + i * 4 for i in range(len(scripts))]
            )
            auto_runner.run_pipeline(
                scripts=scripts,
                start_time=start_time,
                config_path=config_path,
            )
    else:
        # アップロードなしで動画生成のみ
        import subprocess
        logger.info("動画生成を開始...")
        for script in scripts:
            try:
                cmd = [
                    "python", "-m", "shashin_mode.main",
                    str(script),
                    "--config", str(config_path),
                    "--random-thumbnail",
                ]
                logger.info("実行: %s", " ".join(cmd))
                subprocess.run(cmd, check=True)
            except subprocess.CalledProcessError as e:
                logger.error("動画生成失敗: %s", e)
    
    logger.info("YouTube パイプライン完了")


def _parse_input(input_str: str) -> List[str]:
    """入力をパースしてURLリストを返す"""
    urls: List[str] = []
    
    # ファイルの場合
    input_path = Path(input_str)
    if input_path.exists() and input_path.suffix == ".txt":
        lines = input_path.read_text(encoding="utf-8").strip().split("\n")
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                if _is_youtube_url(line):
                    urls.append(line)
        return urls
    
    # 単一URLの場合
    if _is_youtube_url(input_str):
        return [input_str]
    
    return []


def _is_youtube_url(url: str) -> bool:
    """YouTube URLかどうかを判定"""
    return "youtube.com" in url or "youtu.be" in url


def _parse_publish_at(value: str) -> Optional[str]:
    """予約投稿日時をRFC3339形式に変換
    
    対応形式:
        - "2025-11-30 18:00" (ローカル時間)
        - "2025-11-30T18:00:00+09:00" (RFC3339)
    """
    from zoneinfo import ZoneInfo
    
    value = value.strip()
    
    # すでにRFC3339形式の場合はそのまま返す
    if "T" in value and ("+" in value or "Z" in value):
        return value
    
    # ローカル時間形式をパース
    formats = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)
            # タイムゾーンを追加
            dt = dt.replace(tzinfo=ZoneInfo("Asia/Tokyo"))
            # RFC3339形式に変換
            result = dt.strftime("%Y-%m-%dT%H:%M:%S%z")
            # +0900 を +09:00 に変換
            if len(result) > 19 and result[-5] != ":":
                result = result[:-2] + ":" + result[-2:]
            return result
        except ValueError:
            continue
    
    return None


def _add_hours_to_publish_at(value: str, hours: int) -> str:
    """予約投稿日時に時間を加算"""
    from zoneinfo import ZoneInfo
    
    value = value.strip()
    
    # パース
    formats = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)
            dt = dt + timedelta(hours=hours)
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    
    # RFC3339形式の場合
    if "T" in value:
        try:
            # タイムゾーン部分を処理
            if "+" in value:
                dt_str, tz_str = value.rsplit("+", 1)
            elif value.endswith("Z"):
                dt_str = value[:-1]
                tz_str = "00:00"
            else:
                dt_str = value
                tz_str = None
            
            dt = datetime.fromisoformat(dt_str.replace("T", " "))
            dt = dt + timedelta(hours=hours)
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    
    return value


if __name__ == "__main__":
    main()

