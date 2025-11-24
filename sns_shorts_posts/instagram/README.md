# Instagram Reels API Uploader

Instagram Graph APIを使用してReelsに動画をアップロードするモジュールです。

## セットアップ

### 1. 環境変数の設定

`.env`ファイルに以下の環境変数を設定してください：

```bash
INSTAGRAM_APP_ID=your_app_id
INSTAGRAM_APP_SECRET=your_app_secret
INSTAGRAM_ACCESS_TOKEN=your_access_token
INSTAGRAM_BUSINESS_ACCOUNT_ID=your_business_account_id
```

### 2. 必要な権限

Instagram APIで以下の権限が必要です：
- `instagram_content_publish` - Reels投稿に必要
- `pages_show_list` - Facebookページへのアクセス
- `pages_read_engagement` - エンゲージメントデータ読み取り（任意）

## 使用方法

### CLIから実行

```bash
python -m sns_shorts_posts.instagram.instagram_uploader \
  --video /path/to/video.mp4 \
  --caption "キャプション #ハッシュタグ"
```

### Pythonコードから使用

```python
from pathlib import Path
from sns_shorts_posts.instagram.instagram_uploader import upload_reel

result = upload_reel(
    video_path=Path("output/shorts/ready/video.mp4"),
    caption="自動生成された動画 #AI #自動生成",
)

print(f"Media ID: {result['id']}")
```

## 動画の要件

- **形式**: MP4
- **解像度**: 1080×1920（9:16の縦型）推奨
- **最大長**: 90秒以内
- **ファイルサイズ**: 最大100MB（推奨）
- **キャプション**: 最大2200文字

## エラーハンドリング

API呼び出しでエラーが発生した場合、`RuntimeError`が発生します。エラーメッセージには詳細な情報が含まれます。

## 参考リンク

- [Instagram Graph API Documentation](https://developers.facebook.com/docs/instagram-api/)
- [Instagram Content Publishing API](https://developers.facebook.com/docs/instagram-api/guides/content-publishing/)

