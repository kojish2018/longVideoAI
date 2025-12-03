"""Gemini API連携クラス"""
from __future__ import annotations

import os
import re
from typing import List, Tuple, Optional

try:
    import google.generativeai as genai
except ImportError:
    genai = None

from logging_utils import get_logger

logger = get_logger(__name__)


class GeminiClient:
    """Gemini APIクライアント"""
    
    def __init__(self, api_key: Optional[str] = None):
        if genai is None:
            raise ImportError("google-generativeai がインストールされていません。pip install google-generativeai を実行してください。")
        
        api_key = api_key or os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise ValueError("GEMINI_API_KEYが設定されていません。環境変数または引数で指定してください。")
        
        genai.configure(api_key=api_key)
        
        # proとflashの両方を初期化
        self.model_pro = self._initialize_model('gemini-2.5-pro')
        self.model_flash = self._initialize_model('gemini-2.5-flash')
    
    def _initialize_model(self, model_name: str):
        """指定されたモデルを初期化"""
        try:
            model = genai.GenerativeModel(model_name)
            # 簡単なテストでモデルが使用可能か確認
            model.generate_content("test", generation_config={"max_output_tokens": 1})
            logger.info("Gemini APIモデルを初期化しました: %s", model_name)
            return model
        except Exception as e:
            logger.warning("モデル %s の初期化に失敗: %s", model_name, str(e)[:100])
            # フォールバック: flashを返す（proが失敗した場合のみ）
            if model_name == 'gemini-2.5-pro':
                logger.warning("gemini-2.5-pro が使用できないため、flash をフォールバックとして使用します")
                try:
                    return genai.GenerativeModel('gemini-2.5-flash')
                except Exception:
                    pass
            # flashも失敗した場合はエラーを発生させる
            raise RuntimeError(f"モデル {model_name} の初期化に失敗しました")
    
    def select_buzzworthy_articles(self, articles: List, count: int = 3) -> List:
        """YouTubeでバズりそうな記事を選定"""
        if len(articles) <= count:
            return articles
        
        titles = [f"{idx+1}. {article.title}" for idx, article in enumerate(articles)]
        titles_text = "\n".join(titles)
        
        prompt = f"""以下の記事タイトルリストから、YouTubeでバズりそうな記事を{count}つ選んでください。

【最重要】選定基準（優先順位順）：
1. **タイトルに人物名が含まれる記事を最優先で選ぶ**
   - 政治家（例：岸田首相、石破、高市早苗、橋下徹、蓮舫、玉木、小泉進次郎）
   - 有名人・コメンテーター（例：ひろゆき、堀江貴文、成田悠輔）
   - 組織の代表者・著名人
2. 保守層が興味を持ちそうな内容
3. 議論を呼びそうな・炎上しそうな話題
4. 視聴者の感情に訴えかける内容
5. 選ぶ記事は多様性を重視し、似たようなテーマの記事を避ける

記事タイトルリスト：
{titles_text}

【回答ルール】
- 人物名を含む記事が{count}件以上ある場合は、必ずそれらから選ぶこと
- 人物名を含む記事が{count}件未満の場合のみ、他の基準で補う
- 選んだ記事の番号を、カンマ区切りで返してください（例: 1,3,5）
- 番号のみを返し、説明は不要"""
        
        try:
            # 記事選定はflashで十分
            response = self.model_flash.generate_content(prompt)
            selected_indices = self._parse_indices(response.text, len(articles))
            
            selected = [articles[i-1] for i in selected_indices if 1 <= i <= len(articles)]
            
            if len(selected) < count:
                # 不足分は最初の記事から追加
                remaining = [a for a in articles if a not in selected]
                selected.extend(remaining[:count - len(selected)])
            
            logger.info(f"{len(selected)}件の記事を選定しました")
            return selected[:count]
            
        except Exception as e:
            logger.error(f"記事選定エラー: {e}")
            # エラー時は最初の3件を返す
            return articles[:count]
    
    def generate_script(self, article, prompt_template: str) -> str:
        """保守系.mdのプロンプトで台本を生成（proを使用）"""
        prompt = prompt_template.replace("<<テーマ>>", article.content)
        
        try:
            # 台本生成は難しい処理なのでproを使用
            response = self.model_pro.generate_content(prompt)
            script = response.text.strip()
            
            # プロンプトの指示に従って、台本のみを抽出
            # "---" で囲まれた部分や、余計な説明を除去
            lines = script.split('\n')
            script_lines = []
            in_script = False
            
            for line in lines:
                if '---' in line:
                    in_script = not in_script
                    continue
                if not in_script and ('出力' in line or 'イメージ' in line or '参考' in line):
                    continue
                script_lines.append(line)
            
            result = '\n'.join(script_lines).strip()
            
            if not result:
                result = script  # フォールバック
            
            logger.info(f"台本生成完了: {len(result)}文字")
            return result
            
        except Exception as e:
            logger.error(f"台本生成エラー: {e}")
            return ""
    
    def generate_thumbnail_texts(self, article) -> Tuple[str, str]:
        """subsとmainsを生成（flashを使用）"""
        prompt = f"""以下の記事タイトルと内容から、YouTubeのサムネイル用のテキストを2つ生成してください。

記事タイトル: {article.title}
記事内容（要約）: {article.content[:500]}

以下の形式で出力してください：
subs: [8文字以内のバカにする感じの言葉。例：「必死すぎるw」「全部バレバレw」]
mains: [短くインパクトの強い名詞で終わる感じ。例：「岡田氏、終了」「中国、大誤算」]

保守層が興味を持ちそうで、クリックしたくなるようなテキストにしてください。
重要: 説明文は不要で、テキストのみを返してください。"""
        
        try:
            # サムネイルテキスト生成は簡単な処理なのでflashを使用
            response = self.model_flash.generate_content(prompt)
            text = response.text.strip()
            
            # パース
            subs = ""
            mains = ""
            
            for line in text.split('\n'):
                line_stripped = line.strip()
                if line_stripped.startswith('subs:'):
                    subs = line.replace('subs:', '').strip().strip('"').strip("'")
                elif line_stripped.startswith('mains:'):
                    mains = line.replace('mains:', '').strip().strip('"').strip("'")
            
            # 余計な説明を除去
            if '：' in subs:
                subs = subs.split('：')[-1].strip()
            if ':' in subs and not subs.startswith('http'):
                subs = subs.split(':')[-1].strip()
            
            if '：' in mains:
                mains = mains.split('：')[-1].strip()
            if ':' in mains and not mains.startswith('http'):
                mains = mains.split(':')[-1].strip()
            
            # 8文字以内に制限（subs）
            if len(subs) > 8:
                subs = subs[:8]
            
            if not subs or not mains:
                # フォールバック: タイトルから生成
                subs = article.title[:8] if len(article.title) >= 8 else article.title
                mains = article.title[:15] if len(article.title) >= 15 else article.title
            
            logger.info(f"サムネイルテキスト生成完了: subs={subs}, mains={mains}")
            return subs, mains
            
        except Exception as e:
            logger.error(f"サムネイルテキスト生成エラー: {e}")
            # フォールバック
            return article.title[:8], article.title[:15]
    
    def extract_person_names(self, title: str) -> str:
        """記事タイトルから画像検索用のキーワードを抽出（icrawler用、flashを使用）"""
        prompt = f"""以下の記事タイトルから、画像検索に適したキーワードを抽出してください。

記事タイトル: {title}

重要: 画像検索で確実にヒットするような、具体的なキーワードを返してください。
- 人物名がある場合: 「名前 + 役職/組織」の形式を優先（例：「経団連・筒井会長」「橋下徹」）
- 組織名がある場合: 組織名を含める（例：「経団連」「中国」）
- 単純な名詞だけではなく、文脈を含めた具体的なキーワードにする

例：
- 「経団連・筒井会長、中国大使とトップ会談」→「経団連・筒井会長」
- 「橋下徹、批判されすぎて...」→「橋下徹」
- 「中国、大誤算」→「中国」
- 「岡田克也が、高市総理につっかかる」→「岡田克也」

画像検索キーワード:"""
        
        try:
            # キーワード抽出は簡単な処理なのでflashを使用
            response = self.model_flash.generate_content(prompt)
            name = response.text.strip()
            
            # 余計な説明を除去
            lines = name.split('\n')
            name = lines[0].strip() if lines else name.strip()
            
            # 説明文を除去
            if '：' in name:
                name = name.split('：')[-1].strip()
            if ':' in name and not name.startswith('http'):
                name = name.split(':')[-1].strip()
            
            # 「→」や「-」などの記号を除去
            if '→' in name:
                name = name.split('→')[-1].strip()
            if '-' in name and name.startswith('-'):
                name = name.lstrip('-').strip()
            
            # 引用符・装飾を除去
            name = name.strip('"').strip("'").strip('「').strip('」')
            # 【】などの装飾を除去
            import re
            name = re.sub(r'【[^】]*】', '', name).strip()
            name = re.sub(r'\[[^\]]*\]', '', name).strip()
            
            # 「画像検索キーワード:」などのラベルを除去
            if '画像検索キーワード' in name:
                name = name.split('画像検索キーワード')[-1].strip().lstrip(':').strip()
            
            # 説明文が含まれている場合は最初の部分のみ（ただし「・」や「、」は保持）
            # 「経団連・筒井会長」のような形式は保持
            if '。' in name:
                name = name.split('。')[0].strip()
            if 'と' in name and len(name) > 30:
                # 長すぎる場合は最初の部分のみ
                name = name.split('と')[0].strip()
            
            # 長すぎる場合は最初の部分のみ（ただし「・」は保持）
            if len(name) > 30:
                # 「・」がある場合はその前後を保持
                if '・' in name:
                    parts = name.split('・')
                    if len(parts) >= 2:
                        name = f"{parts[0]}・{parts[1][:10]}"
                    else:
                        name = name[:30]
                else:
                    name = name[:30]
            
            if not name:
                # フォールバック: タイトルから最初の名詞を抽出
                words = title.replace('、', ' ').replace('。', ' ').split()
                name = words[0] if words else title[:10]
            
            logger.info(f"画像検索キーワード抽出: {name}")
            return name
            
        except Exception as e:
            logger.error(f"画像検索キーワード抽出エラー: {e}")
            # フォールバック
            words = title.replace('、', ' ').replace('。', ' ').split()
            return words[0] if words else title[:10]
    
    def modify_title(self, title: str) -> str:
        """記事タイトルを保守派向けに変更（flashを使用）"""
        prompt = f"""以下の記事タイトルを、日本の保守層が強烈に興味を持つようなタイトルに変更してください。

【重要なルール】
1. 必ず「1つだけ」タイトルを返してください（複数候補は不要）
2. 保守派が支持する政治家（高市早苗、安倍晋三、etc）は肯定的に
3. 保守派が嫌う政治家・メディアを批判する立場で
4. 「衝撃」「暴露」「炎上」「正論」などのパワーワードを適切に使用
5. タイトルは12-20文字程度で改行し、最大2-3行まで可能
6. 説明や候補リストは一切不要。タイトルのみ出力

元のタイトル: {title}

変更後のタイトル:"""
        
        try:
            # タイトル変更は簡単な処理なのでflashを使用
            response = self.model_flash.generate_content(prompt)
            modified = response.text.strip()
            
            # 複数行のタイトルを取得（説明文は除外）
            lines = []
            for line in modified.split('\n'):
                line_stripped = line.strip()
                if not line_stripped:
                    continue
                # 説明文を除外
                if any(keyword in line_stripped for keyword in ['変更後', 'タイトル:', '：', '説明', '候補']):
                    continue
                # 箇条書き記号を除去
                cleaned = line_stripped.lstrip('*・-•▶︎►▸1234567890.').strip()
                if cleaned:
                    lines.append(cleaned)
            
            # 有効な行を結合（最大3行まで）
            if lines:
                modified = '\n'.join(lines[:3])
            else:
                modified = title  # フォールバック
            
            # 余計な説明を除去
            modified = modified.strip().strip('"').strip("'").strip('「').strip('」')
            
            if not modified:
                modified = title  # フォールバック
            
            logger.info(f"タイトル変更: {title} → {modified}")
            return modified
            
        except Exception as e:
            logger.error(f"タイトル変更エラー: {e}")
            return title
    
    def _parse_indices(self, text: str, max_index: int) -> List[int]:
        """テキストから番号を抽出"""
        numbers = re.findall(r'\d+', text)
        indices = [int(n) for n in numbers if 1 <= int(n) <= max_index]
        return indices[:3]  # 最大3つ

