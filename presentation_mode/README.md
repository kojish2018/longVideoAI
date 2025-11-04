# presentation_mode

MVP向けのプレゼン型タイムラインをまとめたモジュールです。以下のサブディレクトリを想定しています:

- `assets/`: 左パネル用の固定テンプレート画像など（ユーザーがPNG等を配置）。
- `sample_scripts/`: テスト用のJSON台本サンプル。
- 実行時に自動生成されるディレクトリ  
  - `backgrounds/`: Pollinationsから取得した背景画像のキャッシュ。  
  - `panel_layers/`: 描画済みの左パネル透過PNG（自動生成）。  
  - `subtitles/`: 字幕ASSファイル（自動生成）。  
  - `scenes/`: シーンごとの中間動画（自動生成）。

## 必須アセット

- 左パネルのベース画像は `presentation_mode/assets/panel_base.png` に配置してください。動画キャンバス幅×高さに対して横65%・縦82%になるよう自動リサイズされます（例: 1920x1080なら 1248x886 付近）。似たアスペクト比の画像を用意すると歪みが少なくなります。未配置の場合は淡い紫系のデフォルト背景が自動生成されます。
- 右側キャラクター画像は任意のパスをJSONで指定します。ワークスペース内に配置し、例として `presentation_mode/assets/character_rabbit.png` などの名前で保存してください。透過PNG推奨です。
- 字幕タイミングを明示したい場合は、各シーンに `subtitle_lines` 配列を追加してください。音声は `narration` 全文を1度だけ合成し、`subtitle_lines` に沿ってVOICEVOXの音素長から割り出したタイミングで字幕が切り替わります。未指定なら従来通り自動分割されます。
- BGMはデフォルトで `background_music/GoodDays.mp3` を参照し、ナレーションと自動でミックスします。別トラックを使いたい場合は `config.yaml` の `bgm.directory`・`bgm.selected` で上書きできます（例: `bgm.selected: MyTrack.mp3`）。トラックが見つからない場合はナレーションのみで出力されます。

## サンプル台本

`presentation_mode/sample_scripts/quick_demo.json` に1分弱のデモ台本を用意しています。VOICEVOXが利用可能な状態で次のコマンドを実行するとテストレンダリングできます:

```bash
python -m presentation_mode.main presentation_mode/sample_scripts/quick_demo.json --print-plan
```

Pollinationsから背景を取得するため、ネットワークへのアクセスが必要です。API利用環境を整えた上で試してください。
