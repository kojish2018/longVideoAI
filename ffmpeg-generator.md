# FFmpeg 最小移行計画

## ゴール

- MoviePy 依存を保守フェーズに回しつつ、FFmpeg ベースのソフトウェアエンコード(x264)でレンダリング時間の短縮を図る。
- ハードウェアエンコーダ(VideoToolbox 等)は後工程で検討し、今回の作業範囲から除外する。
- 既存パイプライン(LongFormPipeline)のインターフェースは維持し、設定スイッチで MoviePy 版と切り替え可能にする。

## 方針

1. MoviePy 版を即削除せず、新 FFmpeg レンダラーを並行実装する。
2. シーン単位で静止画+Ken Burns+字幕 PNG を FFmpeg フィルタで再現し、最終的に`concat`で結合する。
3. BGM ループ/フェード/正規化は FFmpeg フィルタ(`aloop`, `afade`, `loudnorm`等)で代替し、Python 側は素材管理に専念する。
4. ログ出力は`logging_utils`経由で統一し、FFmpeg 進捗の標準出力を INFO レベルに転送する。
5. 動作確認後に MoviePy→FFmpeg の切り替えデフォルトを検討し、十分な検証が完了するまでは両実装を併存させる。

## タスク一覧

- 計測: 代表的なスクリプトで MoviePy 版のレンダリング時間とログを記録 (`logs/`保管)。
- 設計: FFmpeg フィルタグラフのテンプレートを定義(Ken Burns、字幕、音声ミックス)。
- 実装: 新モジュール(仮: `ffmpeg_renderer.py`)を追加し、`VideoGenerator`相当の API を再現。
- 連結: シーンごとの一時 MP4 生成 →`concat`デマルチプレクサで本編を組み立てる処理を作成。
- 切替: 設定ファイルにレンダラー種別キーを追加し、`LongFormPipeline`終端で分岐。
- テスト: 同一素材で MoviePy 版／FFmpeg 版を比較し、時間短縮率と出力差異(画質・音量)を記録。
- ドキュメント: `requirements.md`や運用ガイドに新レンダラーの使い方と既知の制約を追記。

## 期待効果

- MoviePy で発生していた Python→FFmpeg 間のフレーム転送待ち時間を解消し、3〜6 倍程度のレンダリング短縮を目標とする。
- VideoToolbox 等を使わなくても CPU エンコーダがフルに稼働するため、1 時間 →10〜20 分台への短縮が見込める。
- 実装規模を最小限に留めながら、後続で GPU エンコーダ対応やトランジション強化に拡張できる土台を整える。

## サムネイルスタイルの切り替え

- `config.yaml` の `thumbnail.style` を `style1` (従来レイアウト) または `style2` (添付スクショ風の太字デザイン) に設定できる。
- 実行時は CLI フラグで一時的に上書き可能:

```bash
python long_video_main.py sample_txt/example.txt --thumbnail-style style2
```

- 利用可能なスタイルは順次追加予定。`style1` が指定のない場合のデフォルト。

## YouTube アップロードのチャンネル切り替え

- `config.yaml` の `youtube.channel` で既定のチャンネルプロファイルを指定できます (デフォルトは `default`)。
- チャンネルごとの認証情報は `youtube.channel_profiles` に定義し、必ずフォルダ・ファイルを分けて管理してください。

```yaml
youtube:
	channel: "default"
	channel_profiles:
		default:
			credentials_dir: "credentials"
			credentials_file: "youtube_credentials.json"
			token_file: "youtube_token.json"
		fire:
			credentials_dir: "credentials_fire"
			credentials_file: "credentials-fire.json"
			token_file: "youtube_token_fire.json"
```

- 実行時に別のチャンネルを使う場合は CLI で上書きできます:

```bash
python long_video_main.py sample_txt/example.txt --upload --youtube-channel fire
```

- 指定したチャンネルのフォルダに OAuth クライアント (`credentials-*.json`) とトークン (`youtube_token_*.json`) が保存され、互いに混ざらないよう自動で分離されます。

## VOICEVOX 話者の切り替え

- `config.yaml` の `apis.voicevox.profile` で既定の話者プロファイルを指定できます。
- 複数の話者設定は `apis.voicevox_profiles` に定義し、話者 ID や速度・ピッチなどをプロファイル単位で管理します。

```yaml
apis:
	voicevox:
		profile: "default"
	voicevox_profiles:
		default:
			speaker_id: 13
		fire_male:
			speaker_id: 3
```

- 実行時に別の話者を使う場合は CLI で上書きできます:

```bash
python long_video_main.py sample_txt/example.txt --voicevox-speaker 3
```

- プロファイル名で切り替える場合:

```bash
python long_video_main.py sample_txt/example.txt --voicevox-profile fire_male
```

- `--voicevox-speaker` と `--voicevox-profile` を併用した場合は、プロファイルで読み込んだ設定に対して話者 ID のみ明示的に上書きされます。
