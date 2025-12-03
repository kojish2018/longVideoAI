"""台本生成モジュール"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from logging_utils import get_logger

if TYPE_CHECKING:
    from .news_fetcher import Article
    from .gemini_client import GeminiClient

logger = get_logger(__name__)


class ScriptGenerator:
    """台本生成クラス"""
    
    def __init__(self, prompt_template_path: Path):
        self.prompt_template = prompt_template_path.read_text(encoding='utf-8')
    
    def generate_script_file(
        self,
        article: 'Article',
        gemini_client: 'GeminiClient',
        output_dir: Path,
        index: int
    ) -> Path:
        """記事から台本ファイルを生成"""
        logger.info(f"台本生成開始: {article.title}")
        
        # 1. サムネイルテキスト生成
        subs, mains = gemini_client.generate_thumbnail_texts(article)
        
        # 2. 人物名抽出
        person_name = gemini_client.extract_person_names(article.title)
        
        # 3. タイトル変更
        modified_title = gemini_client.modify_title(article.title)
        
        # 4. 台本生成
        script_content = gemini_client.generate_script(article, self.prompt_template)
        
        # 5. shashin_mode形式のtxtファイルを作成
        script_lines = [
            f'subs"{subs}"',
            '',
            f'mains"{mains}"',
            '',
            f'icrawler"{person_name}"',
            '',
            modified_title,
            '',
            script_content
        ]
        
        script_text = '\n'.join(script_lines)
        
        # ファイル保存
        output_dir.mkdir(parents=True, exist_ok=True)
        script_path = output_dir / f"script_{index:03d}.txt"
        script_path.write_text(script_text, encoding='utf-8')
        
        logger.info(f"台本ファイル生成完了: {script_path}")
        return script_path

