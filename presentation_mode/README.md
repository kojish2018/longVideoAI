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
