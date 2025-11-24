# Facebookページトークンの取得手順

## Graph API Explorerでの手順

1. **Graph API Explorerにアクセス**
   - https://developers.facebook.com/tools/explorer/

2. **アプリを選択**
   - 右上の「メタ」→「アプリ」で「Reels API uploader」を選択

3. **ユーザーまたはページを選択**
   - 「ユーザーまたはページ」のドロップダウンを開く
   - Instagramと接続されているFacebookページを選択
   - （もし表示されない場合は、下記のトラブルシューティングを参照）

4. **権限を追加**
   - 「権限」タブをクリック
   - 以下の権限を追加：
     - `instagram_content_publish`
     - `pages_show_list`
     - `pages_read_engagement`
   - 「権限を追加」をクリック

5. **トークンを生成**
   - 「アクセストークンを生成」をクリック
   - 生成されたトークンをコピー

6. **.envファイルを更新**
   ```bash
   INSTAGRAM_ACCESS_TOKEN=生成したトークンをここに貼り付け
   ```

## トラブルシューティング

### ページが表示されない場合

以下の方法でページIDを確認できます：

1. Facebookページの設定を開く
2. 「ページ情報」→「ページID」を確認
3. または、ページのURLから確認（例: facebook.com/YourPageName）

### ページトークンを直接取得する方法

ページIDが分かっている場合、以下のAPIエンドポイントで取得できます：

```
GET /{page-id}?fields=access_token&access_token={user-token}
```

ただし、これには有効なユーザートークンが必要です。

