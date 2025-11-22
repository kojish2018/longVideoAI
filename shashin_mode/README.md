## shashin_mode 使い方

- 台本: 改行・空行で字幕チャンクを分けた UTF-8 の TXT。空行ごとに 1 チャンク、行はそのまま字幕行として使います。
- 背景: 任意の GIF/MP4。全長分ループします。デフォルトは `shashin_mode/assets/sakura.mp4`（ここにファイルを置けば --background 省略可）。
- 音声: VOICEVOX（`config.yaml` の apis.voicevox を参照）。
- 画像: デフォルトは **Openverse API**（API キー不要、CC 系ライセンス付き）。チャンクごとに検索し、同じチャンク中は同じ画像を維持。失敗時は Bing Image Search API（キーがあれば）やスクレイピング、最終的に `shashin_mode/referenceimage` や `default_img/` にフォールバック。
- 出力: `shashin_mode/output/<run_id>/` に mp4 / srt / plan.json、ログは `shashin_mode/logs/` に保存。`--output` でパスを指定した場合もファイル名に `_<run_id>` を自動付与して上書きを防ぎます（ディレクトリを渡すとその中に `<run_id>.mp4` を作成）。

### 実行例

```bash
python -m shashin_mode.main \
  sample_txt/sample1.txt \
  --background path/to/background.gif \
  --image-source openverse \
  --image-prefix "景色 写真" \
  --wrap 20 \
  --output shashin_mode/output/demo.mp4
```

### 主要オプション

- `--image-source`: `openverse|bing_api|google|bing|local|none`（デフォルトは openverse）
- `--image-prefix`: 検索キーワードのプレフィックス（最初の行と結合）
- `--wrap`: 1 行の最大文字数（超えると自動改行）
- `--subtitle-font`, `--subtitle-size`: 字幕フォント調整
- `--chunk-padding`, `--min-chunk-duration`: 読み上げ長さに足す余白秒数・最小秒数

### レンダラー切り替え (renderer_override.yaml)

- `shashin_mode/renderer_override.yaml` を編集すると、Shashin モード専用にレンダラーを切り替えたりエンコード設定を上書きできます。
- デフォルトは新しい `ffmpeg` レンダラーです。`options` に `ffmpeg_path`, `video_codec`, `audio_bitrate`, `crf`, `threads`, `extra_video_flags` などを記述すると、そのまま FFmpeg コマンドへ渡されます。
- 旧来の MoviePy 実装を使いたい場合は `name: moviepy` に変更してください。`options` に `write_videofile` 用のパラメータ（`codec`, `preset`, `ffmpeg_params`, `threads` など）を指定できます。
- 別実装を使いたい場合は `class` に完全修飾クラス名（例: `my_package.renderers.CustomRenderer`）を設定してください。クラスは `layout`, `paths`, `overlay_dir` を受け取る `__init__` を実装し、`render()` で Shashin パイプラインと互換の挙動を提供する必要があります。

### Openverse / Bing Image Search API の設定

- Openverse: API キー不要。大量リクエストはレート制限があるため連打しないこと。
- Bing Image Search API（任意で利用可）
  - 必須: 環境変数 `BING_SEARCH_V7_KEY`（もしくは `BING_SEARCH_KEY`）。
  - 任意: `BING_SEARCH_V7_ENDPOINT`（既定: `https://api.bing.microsoft.com/v7.0/images/search`）。
  - キー未設定の場合は Openverse → スクレイピング/ローカル に自動フォールバックします。
