# SNSショート動画・自動投稿 開発計画

最終更新: 2025-11-09
作業対象リポジトリ: `/Users/koji/longVideoAI`

---

## 目的
- `long_video_main.py` で生成した長尺動画の台本（スクリプト）から「見どころ」を抽出し、9:16 の縦動画（YouTube Shorts/Instagram Reels/TikTok）を複数本自動生成する。
- 長尺で使用済みの素材（画像・字幕・音声）を再利用しつつ、縦向けにデザインを最適化する。
- 生成されたショート動画を、プラットフォームごとの要件に合わせてメタデータ付与・下書き/予約投稿し、cron でスケジュール実行できるようにする。

## 前提・方針（AGENTS.md 準拠）
- `repo_clone/` は参照専用。既存ファイルの変更・削除は禁止。新規コードはルート直下または新規ディレクトリに配置。
- 外部APIキーは `.env` に保存し、`.env.example` にダミー値を記載。リポジトリには機密をコミットしない。
- 長時間処理の進捗/エラーは `logs/` へ。生成物は `output/` 配下へ保存し、不要な一時ファイルはクリーンアップ。
- ショート用設定と長尺用設定は分離する（ディレクトリ/設定ファイルを分ける）。

---

## 全体アーキテクチャ
1. ハイライト抽出層（Script → Highlights）
   - 台本（例: Markdown/JSON）からハイライト候補を抽出。
   - 長尺タイムライン/字幕と照合して、開始/終了時刻・対応素材へのリンクを確定。
2. ショート生成層（Highlights → Short Clips）
   - 9:16 テンプレートに合わせて、画面構成（背景・被写体クロップ・字幕・CTA）を自動レイアウト。
   - 素材は長尺で使用済みの画像/音声/字幕を再利用（差分レンダリング）。
3. メタデータ/スケジュール層（Short Clips → Schedules）
   - タイトル/説明/タグ/公開範囲/予約日時などをプラットフォーム別に整形。
   - 予約ジョブをキューに登録し、cron から実行。
4. 配信層（Uploader）
   - 各SNSの公式API/SDKをラップしたアップローダーで下書き/公開/予約を実行。
   - レート制限/リトライ/失敗時の再開を吸収。

---

## 想定ディレクトリ構成（新規）
```
longVideoAI/
  sns_shorts_posts/
    sns_posts.md                 ← 本計画書
  shorts/
    extractor/                   ← 台本・字幕からのハイライト抽出
    renderer/                    ← 9:16 レイアウト/合成/焼き込み
    uploader/                    ← YouTube/Instagram/TikTok アップロード実装
    scheduler/                   ← 予約・キュー・cron 連携
    templates/                   ← 9:16 デザインテンプレート（JSON/PNG/SVG）
    config/
      shorts_config.yaml         ← 共通設定
      platforms.yaml             ← 各SNS要件/メタデータマップ
  output/
    long/                        ← 既存長尺出力（参照）
    shorts/
      ready/                     ← 生成済みショート（最終納品）
      work/                      ← 一時中間ファイル
      meta/                      ← ショート毎の JSON メタ
  logs/
    shorts/                      ← 生成/投稿のログ
.env
.env.example
```

---

## データモデル（案）
- ハイライト（`output/shorts/meta/highlights.json`）
```json
{
  "source_project": "<long_video_id>",
  "highlights": [
    {
      "id": "hl_001",
      "title": "○○のコツ",
      "summary": "要点の短い説明",
      "start": 753.2,
      "end": 812.9,
      "assets": {
        "voice_id": "v_track_05",
        "subtitle_file": "output/long/subtitles.vtt",
        "images": ["output/long/stills/scene_12.jpg"]
      },
      "style": {
        "layout": "split_face_top_text_bottom",
        "caption": "karaoke_bold",
        "accent_color": "#FFCC00"
      },
      "platform_overrides": {
        "tiktok": { "hashtags": ["#学び", "#tips"] },
        "youtube": { "title_suffix": " #Shorts" }
      }
    }
  ]
}
```

- 予約スケジュール（`shorts/scheduler/schedule.yaml`）
```yaml
timezone: Asia/Tokyo
items:
  - highlight_id: hl_001
    platforms: [youtube, instagram, tiktok]
    publish_at: 2025-11-12T19:00:00+09:00
    visibility: public
    notes: "初回ローンチ"
```

---

## プラットフォーム別要件（抜粋・検証前提）
- 共通: 9:16・推奨 1080x1920・60秒以内（TikTok は>60秒可の場合あり）。最新仕様は実装前に公式ドキュメントで必ず再確認する。
- YouTube Shorts: 縦/スクエアかつ短尺で自動的にShorts扱い。`#Shorts` をタイトル/説明に付与推奨。
- Instagram Reels: Business/Creator アカウントと必要権限が前提。コンテンツ公開APIでの投稿フローを実装。
- TikTok: 公式 Open API を使用し、アップロード/公開のフローを実装。レート制限/審査ポリシーを考慮。

※ 各APIのエンドポイント/制約は変更可能性が高いため、`shorts/uploader/*/README.md` にリンク/手順を常時更新する。

---

## 実装タスク詳細

### 0. 基盤整備
- `.env.example` に API キーのダミー値と必要スコープを列挙。
- `shorts/config/shorts_config.yaml` に共通パラメータ（解像度、fps、字幕スタイル、最大長、音量正規化など）。
- ロギング初期化（構造化ログ、JSON 併用、ローテーション）。

### 1. ハイライト抽出（extractor）
- 入力: 台本（`scripts/<id>.md|json`）、長尺字幕（VTT/SRT）、長尺タイムラインJSON（長尺側から出力; EDL対応可）。
- 手法:
  - A) 台本へのマークアップ（`[HIGHLIGHT]` ブロック）を正規表現/パーサで抽出。
  - B) 字幕と台本の文単位類似度で最適アライメント → タイムコード推定。
  - C) 章マーカー/目次（長尺側）から候補分割 → 長さ/キーワードでスコアリング。
- 出力: `highlights.json`（上記モデル）。
- 妥当性チェック: 各ハイライト長さ（例: 15–59s）、音声/字幕の同期、素材存在確認。

### 2. 縦用レンダリング（renderer）
- レイアウトテンプレート（JSON）: セーフエリア、テキストボックス、画像枠、ブランド要素（ロゴ/CTA）。
- 処理:
  - 背景: 長尺映像のクロップ＋ガウスぼかし/カラーグレーディング。
  - 被写体: 中心人物/領域を自動トラッキングし9:16でクロップ（人物ない場合はダイナミックパン）。
  - 字幕: 長尺字幕を時間で切り出し、縦用スタイルで焼き込み（ルビ/縁取り/カラオケ風）。
  - 音声: 長尺ボイスを区間切り出し、ラウドネス正規化（EBU R128 目安）。
- 出力: `output/shorts/work/*.mp4` → 検証後 `output/shorts/ready/*.mp4`。

### 3. メタデータ生成
- タイトル・説明・ハッシュタグの自動整形（プラットフォーム別の文字数・NG記号を考慮）。
- サムネイル生成（フレーム抽出 + タイトル合成）。
- `output/shorts/meta/<id>.json` に保存。

### 4. アップローダー（uploader）
- `uploader/youtube_uploader.py`
- `uploader/instagram_uploader.py`
- `uploader/tiktok_uploader.py`
- 共通基盤:
  - 認証: OAuth/長期トークン更新、トークン暗号化保存（OSキーチェーン/ファイル暗号化）。
  - アップロード再試行、進捗表示、失敗復旧（途中から再開）。
  - API レスポンスを `logs/shorts/api/` に保存（PIIはマスク）。

### 5. スケジューラ（scheduler）
- `schedule.yaml` をポーリングし、予約時間到来でアップローダーを起動。
- タイムゾーン/夏時間対応、休日テーブル（任意）。
- 例: crontab（UTC運用推奨）
  ```cron
  # 毎分実行して期限到来をチェック
  * * * * * /usr/bin/python3 /Users/koji/longVideoAI/shorts/scheduler/run_scheduler.py >> /Users/koji/longVideoAI/logs/shorts/scheduler.log 2>&1
  ```
- フェイルオーバー: 直近未投稿アイテムを再試行。上限回数とバックオフを設定。

### 6. 運用/監視
- サマリダッシュボード（JSON集計 → 可視化は任意）。
- Slack/メール通知（成功/失敗/次回予定）。
- 週次で API エラーレポート自動生成。

### 7. セキュリティ/コンプライアンス
- 秘密情報は `.env` または OS キーチェーンに保存。
- 投稿前チェック: 著作権/商標/個人情報/音源ライセンス。
- 失敗時ログにトークンや完全URLを出さない（マスキング）。

### 8. 品質保証/テスト
- ユニット: パーサ、アライメント、FFmpeg コマンド生成、メタ整形。
- スモーク: サンプル台本 → 2本のショート生成 → ローカル検証。
- e2e（任意）: サンドボックス/非公開モードでアップロードまで通す。

---

## マイルストン（2–3週間想定）
1. W1: 抽出 PoC（台本→ハイライトJSON）/ テンプレ初版 / FFmpeg 最小フロー
2. W2: レンダリング安定化（字幕/音量/レイアウト）/ メタ自動化
3. W3: アップローダー/スケジューラ結合 / 小規模本番テスト / 運用整備

---

## 成功基準（DoD）
- サンプル台本から3本以上のショートを自動生成し、3プラットフォームへ予約投稿が通る。
- 失敗時にリトライ/原因特定ができるログが揃っている。
- `.env.example` と README が最新で、新規環境で再現可能。

---

## 次アクション
- `shorts/` 以下のモジュール雛形を作成し、`.env.example` と `shorts/config/shorts_config.yaml` を追加。
- 長尺側出力（字幕/タイムライン/使用素材リスト）フォーマットの合意とサンプル作成。
- 最小PoC: 台本に `[HIGHLIGHT]` を2箇所付け、1本のショートを生成してローカル再生確認。

