"""WordPress REST APIを使用してsn-jp.comから記事を取得"""
from __future__ import annotations

from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List

import requests
from bs4 import BeautifulSoup

from logging_utils import get_logger

logger = get_logger(__name__)


@dataclass
class Article:
    title: str
    url: str
    category: str
    pub_date: datetime
    content: str


class NewsFetcher:
    """sn-jp.comから記事を取得するクラス"""
    
    def __init__(self, api_base: str = "https://sn-jp.com/wp-json/wp/v2"):
        self.api_base = api_base
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def fetch_political_articles(self, per_page: int = 100) -> List[Article]:
        """当日の政治カテゴリの記事を取得"""
        try:
            # カテゴリIDを取得
            categories_response = self.session.get(
                f"{self.api_base}/categories",
                params={'search': '政治', 'per_page': 10},
                timeout=10
            )
            
            if categories_response.status_code != 200:
                logger.error(f"カテゴリ取得失敗: {categories_response.status_code}")
                return []
            
            categories = categories_response.json()
            if not categories:
                logger.warning("政治カテゴリが見つかりませんでした")
                return []
            
            # 政治カテゴリのIDを取得
            political_category_id = None
            for cat in categories:
                if '政治' in cat.get('name', ''):
                    political_category_id = cat.get('id')
                    break
            
            if not political_category_id:
                logger.warning("政治カテゴリのIDが見つかりませんでした")
                return []
            
            logger.info(f"政治カテゴリID: {political_category_id}")
            
            # 記事を取得
            posts_response = self.session.get(
                f"{self.api_base}/posts",
                params={
                    'categories': political_category_id,
                    'per_page': per_page,
                    'orderby': 'date',
                    'order': 'desc'
                },
                timeout=10
            )
            
            if posts_response.status_code != 200:
                logger.error(f"記事取得失敗: {posts_response.status_code}")
                return []
            
            posts = posts_response.json()
            logger.info(f"記事取得成功: {len(posts)}件")
            
            # 直近24時間の記事のみフィルタリング
            articles = []
            threshold = datetime.now() - timedelta(hours=24)
            
            for post in posts:
                try:
                    pub_date = datetime.fromisoformat(post['date'].replace('Z', '+00:00'))
                    if pub_date < threshold:
                        continue
                    
                    content = self._clean_html_content(post['content']['rendered'])
                    
                    article = Article(
                        title=post['title']['rendered'],
                        url=post['link'],
                        category='政治',
                        pub_date=pub_date,
                        content=content
                    )
                    articles.append(article)
                    
                except Exception as e:
                    logger.warning(f"記事処理エラー: {e}")
                    continue
            
            logger.info(f"直近24時間の政治カテゴリ記事: {len(articles)}件")
            return articles
            
        except Exception as e:
            logger.error(f"記事取得エラー: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def _clean_html_content(self, html_content: str) -> str:
        """HTMLコンテンツをテキストに変換"""
        if not html_content:
            return ""
        
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 不要な要素を除去
        for tag in soup.find_all(['script', 'style', 'aside', 'nav', 'footer', 'header']):
            tag.decompose()
        
        # テキストを取得
        text = soup.get_text(separator='\n', strip=True)
        
        # 余分な空白行を削除
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        return '\n'.join(lines)

