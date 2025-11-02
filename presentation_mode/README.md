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

- 左パネルのベース画像は `presentation_mode/assets/panel_base.png` に配置してください。1920x1080キャンバスの左端にぴったり張り付く前提で、幅1140px程度・高さ1080pxのPNGが扱いやすい想定です。未配置の場合は淡い紫系のデフォルト背景が自動生成されます。
- 右側キャラクター画像は任意のパスをJSONで指定します。ワークスペース内に配置し、例として `presentation_mode/assets/character_rabbit.png` などの名前で保存してください。透過PNG推奨です。

## サンプル台本

`presentation_mode/sample_scripts/quick_demo.json` に1分弱のデモ台本を用意しています。VOICEVOXが利用可能な状態で次のコマンドを実行するとテストレンダリングできます:

```bash
python -m presentation_mode.main presentation_mode/sample_scripts/quick_demo.json --print-plan
```

Pollinationsから背景を取得するため、ネットワークへのアクセスが必要です。API利用環境を整えた上で試してください。
