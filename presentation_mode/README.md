# presentation_mode

MVP 向けのプレゼン型タイムラインをまとめたモジュールです。以下のサブディレクトリを想定しています:

- `assets/`: 左パネル用の固定テンプレート画像など（ユーザーが PNG 等を配置）。
- `sample_scripts/`: テスト用の JSON 台本サンプル。
- 実行時に自動生成されるディレクトリ
  - `backgrounds/`: `presentation_mode/backgrounds/back8.png` を 16:9 に整形した静止背景。
  - `panel_layers/`: 描画済みの左パネル透過 PNG（自動生成）。
  - `subtitles/`: 字幕 ASS ファイル（自動生成）。
  - `scenes/`: シーンごとの中間動画（自動生成）。

## 必須アセット

- 左パネルのベース画像は `presentation_mode/assets/panel_base.png` に配置してください。動画キャンバス幅 × 高さに対して横 65%・縦 82%になるよう自動リサイズされます（例: 1920x1080 なら 1248x886 付近）。似たアスペクト比の画像を用意すると歪みが少なくなります。未配置の場合は淡い紫系のデフォルト背景が自動生成されます。
- 右側キャラクター画像は任意のパスを JSON で指定します。ワークスペース内に配置し、例として `presentation_mode/assets/character_rabbit.png` などの名前で保存してください。透過 PNG 推奨です。
- `character.animation` を指定しなくても、デフォルトで上下ボブ+休止のループが適用されます。`amplitude` (上下移動量 px)、`move_duration` (揺れる秒数)、`rest_duration` (停止する秒数) のみ上書きしたい場合は必要な項目だけ記述してください。アニメーションを無効化したいときは `"animation": { "enabled": false }` を設定します。
- 字幕タイミングを明示したい場合は、各シーンに `subtitle_lines` 配列を追加してください。音声は `narration` 全文を 1 度だけ合成し、`subtitle_lines` に沿って VOICEVOX の音素長から割り出したタイミングで字幕が切り替わります。未指定なら従来通り自動分割されます。
- BGM はデフォルトで `background_music/GoodDays.mp3` を参照し、ナレーションと自動でミックスします。別トラックを使いたい場合は `config.yaml` の `bgm.directory`・`bgm.selected` で上書きできます（例: `bgm.selected: MyTrack.mp3`）。トラックが見つからない場合はナレーションのみで出力されます。

## サンプル台本

`presentation_mode/sample_scripts/quick_demo.json` に 1 分弱のデモ台本を用意しています。VOICEVOX が利用可能な状態で次のコマンドを実行するとテストレンダリングできます:

```bash
python -m presentation_mode.main presentation_mode/sample_scripts/quick_demo.json --print-plan
```

背景は常に `presentation_mode/backgrounds/back8.png` を 16:9 にリサイズして利用するため、ネットワーク接続は不要です。

## サムネ再利用と YouTube アップロード

長尺パイプライン同様に、既存サムネイルのコピーと YouTube への自動アップロードに対応しました。いずれも `presentation_mode/` 配下のみの変更で完結します。

### サムネイルのコピー

- `--thumbnail-path` に既存 PNG/JPG などのファイルを指定すると、レンダリング結果のランディレクトリへコピーされます。
- コピー先ファイル名は `thumbnail_<run_id><拡張子>` がデフォルトです。別名にしたい場合は `--thumbnail-copy-name custom_name.png` を併用してください。
- コピーに成功すると `plan.json` と `PresentationResult` の `thumbnail_path` に保存先パスが含まれます。そのままアップロード処理へ引き渡されます。

### YouTube へのアップロード

- `--upload` を指定するとレンダリング後に `youtube_uploader.py` を呼び出し、`credentials_fire/` の認証情報（デフォルト）で動画をアップロードします。
- チャンネルプロファイルは `--youtube-channel` で変更できます（既定値は `fire`）。`config.yaml` の `youtube.channel_profiles` に定義されたキーを渡してください。
- 予約公開を行いたい場合は `--publish-at "2025-11-08 21:30"` または `--publish-at 2025-11-08T21:30:00+09:00` のように指定します。無効な形式はログに警告を出して即時公開にフォールバックします。
- 説明文は JSON 台本の `description` を優先し、未設定の場合は `config.yaml` の `youtube.description_template` を利用します。タグは `tags` フィールドから自動整形され、ハッシュタグ行として説明文末尾に追記されます。

#### 利用例

```bash
python -m presentation_mode.main presentation_mode/sample_scripts/quick_demo.json \
  --thumbnail-path output/thumbnails/latest.png \
  --upload \
  --publish-at "2025-11-08 21:30"
```

> **注意**: YouTube Data API を利用するために `google-api-python-client` と OAuth 認証情報が必要です。`credentials_fire/` 以下に配置されたクライアントシークレットとトークンが有効な状態であることを確認してください。初回実行時はブラウザでの OAuth 認可が求められます。
