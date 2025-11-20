# yukkuri_mode: ゆっくり霊夢/魔理沙の対話動画パイプライン

`yukkuri_mode/` だけで完結する JSON 台本→ムービー生成の MVP を追加しました。  
`presentation_mode/` と同じように `python yukkuri_mode/main.py --script ...` で MP4 と字幕(SRT/VTT) を出力できます。

## クイックスタート

```bash
# デモ台本で試す
python yukkuri_mode/main.py --script yukkuri_mode/sample_script.json

# VOICEVOX を使わずにドライラン（無音・所要時間のみ）
python yukkuri_mode/main.py --script my.json --no-voice --dry-run
```

- 出力先: `output/yukkuri_mode/<タイトル>.mp4`（`yukkuri_mode/config.yaml` で変更可）
- ログ: `logs/yukkuri_mode.log`
- BGM: `background_music/` から設定ファイルの指定を使用（デフォルト `GoodDays.mp3`）

## 新規モジュール

- `yukkuri_mode/main.py` … CLI。JSON/JSONL ロード → タイムライン化 → VOICEVOX 合成 → MoviePy レンダ → 字幕出力。
- `yukkuri_mode/video_renderer.py` … 背景+キャラ+テロップ帯を合成。BGM 混在、SRT/VTT とは別生成。
- `yukkuri_mode/timeline_builder.py` … `YukkuriScript` から 1 行=1 カットのタイムラインを構築。
- `yukkuri_mode/voice_adapter.py` … spaker alias → VOICEVOX speaker ID マッピング。無音推定のフォールバック付き。
- `yukkuri_mode/styles.py` … 配色・フォント・ケンバーンズ・キャラ位置などのスタイルを YAML から解決。
- `yukkuri_mode/config.yaml` … デフォルト設定。解像度・色・BGM・VOICEVOX・背景パスなどを記載。
- `yukkuri_mode/sample_script.json` … 霊夢/魔理沙のデモ台本。

## JSON/JSONL 台本フォーマット

- ルート: 配列、または `utterances` / `dialogue` / `lines` / `entries` / `script` を含むオブジェクト。`scenes` 階層もサポート（各シーンのキーを発話にマージ）。
- 必須: `speaker`, `text`
- 任意:
  - 時間: `duration` / `duration_seconds`, `start`, `end`
  - 背景: `bg_image` / `background` / `bg`, `bg_prompt` / `prompt`, `bg_reference`
  - 音: `bgm` / `bgm_cue`, `se` / `sound_effect`
  - レイアウト: `layout` / `layout_hint`, `style` / `overlay_style`
  - その他: 不明キーは `extras` に残す
- メタデータ: `title`, `tags`, `description`, `thumbnail_image_prompt` (`image_prompt` も可)

### 例（抜粋）

```json
{
  "title": "ひつか駅の怪談をゆっくり解説",
  "scenes": [
    {
      "bg_image": "IMG_3892.PNG",
      "utterances": [
        {"speaker": "霊夢", "text": "うわ、ほんとに霧だらけ……。"},
        {"speaker": "魔理沙", "text": "昼間なら平気だぜ。", "bgm": "GoodDays.mp3", "duration": 4.0}
      ]
    }
  ]
}
```

## スタイル・設定の主な項目（`config.yaml`）

- `video`: `width` / `height` / `fps` / `codec` / `crf` など FFmpeg 設定。
- `style`: フォント、色、テロップ帯の余白、文字枠線、ドロップシャドウ、折り返し文字数。
- `characters`: 霊夢・魔理沙などの表示名、左右配置、スプライトパス（未設定なら丸型プレースホルダー）。
- `backgrounds.search_dirs`: 背景検索パス（デフォルトで `yukkuri_mode/referenceimage`, `default_img`, `visual_img`）。
- `voice`: VOICEVOX 接続設定、chars_per_second による長さ見積もり、speaker_id マッピング。
- `bgm`: デフォルトの BGM ファイルと音量/フェード。
- `timing`: カット間ギャップ、テロップの余白など。
- `subtitles`: `write_srt` / `write_vtt` の ON/OFF。

## 実行時の挙動

1. JSON/JSONL を `yukkuri_mode/json_script_loader.py` で読み込む。
2. `timeline_builder` が `inter_shot_gap` を考慮して 1 行=1 カット化。
3. `voice_adapter` が VOICEVOX 合成（`--no-voice` 時は無音 WAV を推定長で生成）。
4. `video_renderer` が背景+キャラ+テロップ帯+BGM を合成し MP4 出力。
5. SRT/VTT を出力（オフ可）。

## 既知の制約・メモ

- 画像生成や外部 API 呼び出しはまだ未接続。`bg_prompt`/`bg_reference` はログみに保持のみ。
- スプライトが無い場合は丸型プレースホルダーで代用。
- 長尺レンダリング前に `sample_script.json` など短尺で確認してください。
