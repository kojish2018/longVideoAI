"""自動化パイプラインのメインエントリーポイント"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from config_loader import load_config
from logging_utils import configure_logging, get_logger

from .auto_runner import AutoRunner
from .gemini_client import GeminiClient
from .news_fetcher import NewsFetcher
from .script_generator import ScriptGenerator

logger = get_logger(__name__)


def main():
    """メイン関数"""
    logger.info("自動化パイプライン開始")
    start_time = datetime.now()
    
    # 設定読み込み
    config_path = Path("config.yaml")
    config = load_config(config_path, project_root=Path.cwd())
    
    # ログ設定
    log_dir = Path("shashin_mode/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"auto_pipeline_{start_time.strftime('%Y%m%d_%H%M%S')}.log"
    configure_logging(config.logging_level, log_path)
    
    try:
        # 1. 記事取得
        logger.info("記事取得開始")
        news_fetcher = NewsFetcher()
        articles = news_fetcher.fetch_political_articles()
        
        if len(articles) < 3:
            logger.error(f"記事が3件未満です: {len(articles)}件")
            return 1
        
        logger.info(f"{len(articles)}件の記事を取得しました")
        
        # 2. Gemini APIクライアント初期化
        gemini_config = config.raw.get('apis', {}).get('gemini', {}) if isinstance(config.raw, dict) else {}
        gemini_api_key = gemini_config.get('api_key') or None
        
        gemini_client = GeminiClient(api_key=gemini_api_key)
        
        # 3. バズ予測選定（3件）
        logger.info("バズ予測選定開始")
        selected_articles = gemini_client.select_buzzworthy_articles(articles, count=3)
        
        if len(selected_articles) < 3:
            logger.error(f"選定された記事が3件未満です: {len(selected_articles)}件")
            return 1
        
        logger.info(f"3件の記事を選定しました")
        for idx, article in enumerate(selected_articles, 1):
            logger.info(f"  {idx}. {article.title}")
        
        # 4. 台本生成（3件）
        logger.info("台本生成開始")
        prompt_template_path = Path("scripts_prompts/保守系.md")
        if not prompt_template_path.exists():
            logger.error(f"プロンプトテンプレートが見つかりません: {prompt_template_path}")
            return 1
        
        script_generator = ScriptGenerator(prompt_template_path)
        output_dir = Path("shashin_mode/scripts/auto_generated")
        scripts = []
        
        for idx, article in enumerate(selected_articles, 1):
            try:
                script_path = script_generator.generate_script_file(
                    article=article,
                    gemini_client=gemini_client,
                    output_dir=output_dir,
                    index=idx
                )
                scripts.append(script_path)
            except Exception as e:
                logger.error(f"台本生成エラー ({article.title}): {e}")
                import traceback
                traceback.print_exc()
                continue
        
        if len(scripts) < 3:
            logger.error(f"生成された台本が3件未満です: {len(scripts)}件")
            return 1
        
        logger.info(f"3件の台本を生成しました")
        
        # 5. 動画生成・アップロード（3件）
        logger.info("動画生成・アップロード開始")
        auto_pipeline_config = config.raw.get('auto_pipeline', {}) if isinstance(config.raw, dict) else {}
        delays_hours = auto_pipeline_config.get('scheduling', {}).get('publish_delays_hours', [4, 8, 12])
        youtube_channel = auto_pipeline_config.get('youtube_channel')
        
        auto_runner = AutoRunner(delays_hours=delays_hours)
        auto_runner.run_pipeline(
            scripts=scripts,
            start_time=start_time,
            config_path=config_path,
            youtube_channel=youtube_channel
        )
        
        logger.info("自動化パイプライン完了")
        return 0
        
    except Exception as e:
        logger.error(f"自動化パイプラインエラー: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

