# longVideoAI における FFmpeg ロング動画レンダリング徹底解説（SaaS設計向け）

このドキュメントは、longVideoAI リポジトリの FFmpeg ベース実装（主に `long_form/ffmpeg/` 配下および `long_pipeline.py` 近傍）を読み解き、SaaS 化に転用できるロジック、フィルタグラフ設計、運用ノウハウを体系化したものです。MoviePy 実装は参考にとどめ、FFmpeg 実装を中心にまとめています。

- 主要参照ファイル:
  - `renderer_factory.py`（レンダラー切替）
  - `long_form/ffmpeg/renderer.py`（FFmpeg レンダラー本体）
  - `long_form/ffmpeg/concat.py`（MP4 セグメント連結）
  - `long_form/ffmpeg/runner.py`・`progress.py`（実行・進捗）
  - `long_form/ass_timeline.py`・`long_form/typing_overlay.py`（ASS 生成と字幕オーバーレイ）
  - `long_pipeline.py`（全体オーケストレーション）
  - `config.yaml`（設定例）

---

## 全体像（パイプライン）

1. スクリプト解析・アセット生成
   - 音声ナレーション（WAV/MP3）、シーンごとの静止画、テキストセグメントを用意。
2. シーン単位の映像生成（FFmpeg）
   - Opening（冒頭タイトル）シーンと Content（本文）シーンを個別 MP4 として出力。
   - 本文シーンはベース静止画に Ken Burns（ズーム・パン）とテキストオーバーレイを合成。
3. セグメント連結（concat demuxer, stream copy）
   - シーン MP4 群を `temp_concat.mp4` にストリームコピーで連結。
4. BGM ミックス（最終合成）
   - 連結済み映像にナレーション＋BGM を EBU R128 基準で正規化・フェードしミックス。
5. 進捗・ログ
   - 中間シーン生成は静かに、最終合成は `-progress pipe:1` を使って 1 本の進捗バーで表示。

SaaS では 1 ジョブ＝1 つの run ディレクトリ（`{output_dir}/{run_id}/`）に閉じ込め、`ffmpeg_scenes/`, `overlays/`, `ass/`, `temp_concat.mp4` 等を生成する構成が扱いやすいです。

---

## レンダラー切替と設定マッピング

- 切替: `renderer_factory.make_renderer(config)` が `config['renderer']` を見て `moviepy`／`ffmpeg` を分岐。SaaS では API 入力でレンダラー選択を可能に。
- レンダリング設定（抜粋）: `long_form/ffmpeg/renderer.py` の `RenderConfig` にマップ。
  - `video.width|height|fps|codec|bitrate|crf|preset` → 映像出力プロファイル
  - `video.audio_codec|audio_bitrate|audio_sample_rate` → 音声出力プロファイル
  - `animation.padding_seconds` → セグメント間マージン等の調整
  - Ken Burns 系（後述）: `ken_burns_*`
  - `text.font_path|default_size|colors.*` → オーバーレイ PNG/ASS の描画
  - `ffmpeg.animation.*` → FFmpeg 実装に限った上書き（MoviePy との差分吸収）

---

## Opening シーン（センタータイトル＋ナレーション）

- 入力ストリーム
  - `lavfi` の黒背景（指定サイズ・FPS）
  - PNG オーバーレイ（中央にタイトル文字列を PIL で描画）を `-loop 1` で全長に伸長
  - ナレーション音声（`-map 2:a:0`）
- フィルタグラフ（概念）
  - 中央合成→FPS 整形→`yuv420p`

例（擬似コマンド）:

```
ffmpeg -hide_banner -loglevel error -nostats \
  -t {DUR} -f lavfi -r {FPS} -i color=c=black:size={W}x{H} \
  -loop 1 -framerate {FPS} -t {DUR} -i overlay.png \
  -i narration.wav \
  -filter_complex "[0:v][1:v]overlay=x=(W-w)/2:y=(H-h)/2:eval=init:format=auto, \
                   fps={FPS},format=yuv420p[vout]" \
  -map [vout] -map 2:a:0 {ENCODE_ARGS} -shortest -y opening.mp4
```

`ENCODE_ARGS` は以下（後述「エンコード設定」）。

---

## Content シーン（ベース静止画＋Ken Burns＋テキスト）

### 入力構成

- ベース静止画（存在しなければ 1 フレームの黒映像を lavfi で生成）
- テキストオーバーレイ PNG をセグメント数分（`-loop 1` で尺合わせ）
- ナレーション音声（各シーンに 1 本）

### テキストオーバーレイ生成

- `PIL` で半透明の角丸バンド上に中央揃えテキストを描画し PNG 化。
  - 横マージン、角丸半径、上下の外側/内側パディングをフォントサイズから動的決定。
  - マルチライン時は行間（`size * 0.42` など）で高さを算出。
- タイピングモード（`overlay.type = typing`）時
  - バンドのみの PNG を生成（テキストは描かない）。
  - テキストは ASS 字幕を生成し `subtitles` フィルタで合成（libass 必須）。
  - 各セグメントの左上固定座標（PNG のバンド矩形内）を計算し、`\pos(x,y)` で配置。速度は文字数/区間長から算出しつつ `typing_speed` を倍率で調整。

### Ken Burns（ズーム・パン）

- 2 モード対応: `ken_burns_mode = pan_only | zoompan`
  - 共通: ベース静止画を「カバー」基準でまず拡大し、マージンを乗せてから処理。
- pan_only（推奨・安定）
  - `crop` の `x(t), y(t)` を時間で補間してズーム無しのパンを実現。
  - 可動範囲は `ken_burns_pan_extent` とベース拡大率、`ken_burns_motion_scale`、`ken_burns_full_travel` で決定。
  - 方向ベクトルは `(-1,0),(1,0),(0,1)` 等をシーンごとに疑似乱数で選択。
- zoompan
  - `zoompan` で `zoom` をフレーム毎に `step` 加算、`x(t), y(t)` は `offset` と `margin` を使ってズーム中心から移動。
  - `ken_burns_zoom <= 0` はエプシロンにクランプ（0.015）して安定化。

### フィルタグラフ（概念）

- ベース静止画 → scale（cover＋margin）→ pan_only: `crop` or zoompan: `zoompan` → `[base]`
- セグメント毎の PNG を `[base]` の下端に `overlay`（`enable='between(t,start,end)'`）
- タイピング時は最後に `subtitles=filename='scene.ass':fontsdir='fonts'`
- 最終 `format=yuv420p`

例（pan_only の一部イメージ。実際は式が長いので簡略化）:

```
-filter_complex " \
  [0:v]scale=iw*({cover})*(1+{margin}):ih*({cover})*(1+{margin})[base_in]; \
  [base_in]crop=w={W}:h={H}:x='{x(t)}':y='{y(t)}',fps={FPS},format=yuv420p[base]; \
  [base][1:v]overlay=x=0:y=H-h:enable='between(t,TS0,TE0)'[v0]; \
  [v0][2:v]overlay=x=0:y=H-h:enable='between(t,TS1,TE1)'[vout]; \
  [vout]format=yuv420p[vout] "
```

`x(t), y(t)` は可動範囲・補間（ease-out など）を考慮して `min(max(...),...)` で画角内に収めています。

---

## タイピングアニメーション詳細（ASS + FFmpeg）

本プロジェクトのタイピングは、パイプライン内では「カラオケベース（`\kf`）」を採用し、イベント数を最小化して高速/軽量化しています。補助的に「1文字1イベント」型の実装も併存しており、要件に応じて切り替え可能です。

【切替と速度】
- `overlay.type = typing` で有効化。`overlay.typing_speed`（既定 1.0、CLI `--typing-speed` の上書き可）で相対速度を制御。
- 1セグメント内の合計文字数 `N` と区間長 `D` から `cps = max((N/D) * typing_speed, 1.0)` を算出。

【カラオケ型（採用中）】
- 生成関数: `long_form/ass_timeline.py` の `build_ass_karaoke_centered()`。
- レイアウト: `Alignment=8`（上中寄せ）＋ `\pos(cx, y)` で行の中心 x と上端 y を与え、帯PNGの中心に文字列を揃える。
- タイミング: 1 行あたり 1 イベントで `\kf{ticks}` を文字ごとに付与。`ticks` はおおむね 100分の1秒刻み（合計=`min(N/cps, seg_end - t0) * 100`）を均等配分し、余りを先頭から加算。
- Opening でも typing 選択時は黒ベース＋カラオケ ASS を使用（行間は PNG と同じ `font.size * 0.6` で垂直センタリング）。
- 字形/太字: `fontTools` でフォントの PostScript 名を抽出し、`subtitles` フィルタに `:force_style=FontName=...,Bold=0|1` を付与。`fontsdir=fonts` と併用してフォント崩れを防止。

【固定座標＋1文字イベント型（代替）】
- 生成関数: `build_ass_for_content_scene_pos()`／`build_ass_centered_lines_typing()`。
- レイアウト: 帯PNGのジオメトリ（`horizontal_margin`, `text_top_y`, `band_height`）から `\pos(x,y)` を直接算出。最終的な見た目が中央揃えになるよう行ごとに left を計算する実装も用意。
- タイミング: 1 文字ごとに `Dialogue` を生成（イベント数が多くなるため長尺では非推奨）。

【FFmpeg への適用順】
- ベース映像（Ken Burns）→ 帯PNG overlay 群 → `subtitles=filename='scene.ass':fontsdir='fonts'[:force_style=...]` → `format=yuv420p`。
- 文字が PNG より上に描画されるよう、ASS を最後に適用。

【実装上のポイント】
- 帯PNGは typing でも表示し（半透明の箱のみ）、文字は ASS 側で描く。これにより、静的/typing の外観を統一。
- 行間・上下余白は PNG 側の計算と合わせる（複数行は `0.42*font.size` 程度の行間）。
- 速度安定性のため、VOICEVOX に投入するテキストは `speech_sanitizer.py` で括弧や引用符を除去（不要なポーズを防ぎ、字幕タイミングのズレを低減）。

【CLI ユーティリティ】
- `long_form/typing_overlay.py` は単体動画に対して ASS を合成する簡易ツール。`--ass-only` で ASS のみ出力可能。SaaS ではプレビュー生成用に有用。

---

## BGM ミックス（EBU R128 準拠の二段正規化）

- 入力: 連結済み映像（`0:v` と `0:a=ナレーション`）、BGM 音源（`1:a`）。BGM は `-stream_loop -1` でループ。
- 手順
  1. BGM を尺にトリム→R128 下げ目（I=-30, LRA=7, TP=-2）で正規化→`volume=0.24`→`afade in/out`→ステレオ化
  2. ナレーションもステレオ化
  3. `amix=inputs=2:duration=first:dropout_transition=2` で合成
  4. 合成後を番組全体で再度 R128 正規化（I=-14, LRA=7, TP=-1.5）
  5. 映像は `-c:v copy`、音声のみ再エンコード。`-shortest` で長さを合わせる

フィルタグラフ例:

```
-filter_complex " \
  [1:a]atrim=0:duration={TOTAL},asetpts=PTS-STARTPTS, \
       loudnorm=I=-30:LRA=7:TP=-2, volume=0.24, \
       afade=t=in:st=0:d=0.5,afade=t=out:st={TOTAL-1.0}:d=1.0, \
       aformat=sample_fmts=fltp:sample_rates={SR}:channel_layouts=stereo[bgm]; \
  [0:a]aformat=sample_fmts=fltp:sample_rates={SR}:channel_layouts=stereo[narr]; \
  [narr][bgm]amix=inputs=2:duration=first:dropout_transition=2[a]; \
  [a]loudnorm=I=-14:LRA=7:TP=-1.5, \
     aformat=sample_fmts=fltp:sample_rates={SR}:channel_layouts=stereo[aout] "
```

コマンド（概念）:

```
ffmpeg -hide_banner -loglevel error -nostats \
  -i temp_concat.mp4 \
  -stream_loop -1 -i background_music/Vandals.mp3 \
  -filter_complex {上記} \
  -map 0:v -map [aout] -c:v copy -c:a aac -ar 48000 -ac 2 \
  -movflags +faststart -shortest -y final.mp4
```

BGM が存在しない場合は `-c copy` のみで faststart を付けて移送します（高速経路）。

---

## セグメント連結（concat demuxer, stream copy）

- 役割: シーンごとの MP4（同一プロファイル）を可逆に結合。
- 実装: `ffconcat version 1.0` 形式のリストを作成し、`-f concat -safe 0 -i list.txt -c copy`。
- バリデーション: 入力存在・サイズ>0 を事前検査。1 本のみなら単純コピー。

---

## エンコード設定（配信プラットフォーム互換）

- 共通
  - `-r {fps}`、`-pix_fmt yuv420p`、`-profile:v high -level:v 4.1`
  - 色空間タグ: `-color_primaries bt709 -color_trc bt709 -colorspace bt709`
  - `-movflags +faststart`（MP4 配信最適化）
- 映像
  - `-c:v libx264`（既定）、`-crf {crf}` or `-b:v {bitrate}`、`-preset {preset}`
- 音声
  - `-c:a aac`（既定）、`-ar 48000`、`-ac 2`、`-b:a {audio_bitrate}`
- シーン書き出し時は `-shortest` で音声/映像の長さ差を吸収。

---

## 進捗表示とログ

- 実行ラッパ
  - `run_ffmpeg(...)`: 失敗時は STDERR の末尾 ~50 行をログ出力して例外化。
  - `run_ffmpeg_stream(...)`: `-progress pipe:1` を使い `out_time_ms` をパースして進捗バー描画。
- バーの設計
  - シーン書き出しは静的または外部バー（タイムライン全体）に合流可能。
  - 最終 BGM ミックス時に 1 本のバー（MoviePy と同等の体験）。

---

## フォルダ構成（1 ジョブ = 1 ディレクトリ）

- `{run_dir}/ffmpeg_scenes/{SCENE_ID}.mp4` … シーンごとの書き出し
- `{run_dir}/overlays/*.png` … テキストバンド/タイトル PNG キャッシュ
- `{run_dir}/ass/*.ass` … タイピング字幕（必要時）
- `{run_dir}/temp_concat.mp4` … 連結済み中間ファイル
- `{run_dir}/{run_id}.mp4` … 最終ファイル

---

## 設定パラメータ（SaaS 入力に向けた整理）

- 映像: `width`/`height`/`fps`/`codec`/`crf`/`bitrate`/`preset`
- 音声: `audio_codec`/`audio_bitrate`/`audio_sample_rate`
- テキスト: `font_path`/`font_family`/`default_size`/`colors.default|background_box`
- オーバーレイ: `overlay.type = static|typing`、`overlay.typing_speed`
- Ken Burns: 
  - `ken_burns_mode = pan_only|zoompan`
  - `ken_burns_zoom`（zoompan 時の最大ズーム 1+z）
  - `ken_burns_offset`（ズーム中心からの相対移動量）
  - `ken_burns_margin`（初期拡大率に対する余白）
  - `ken_burns_motion_scale`（全体モーション倍率）
  - `ken_burns_max_margin`（マージン上限のヘッドルーム）
  - `ken_burns_full_travel`（到達距離の強制 100%）
  - `ken_burns_pan_extent`（pan_only の移動割合）
  - `ken_burns_intro_relief|ken_burns_intro_seconds`（導入部のマージン緩和）
- BGM: 入力パス、既定ゲイン、フェード長、目標ラウドネス（-14 LUFS など）

SaaS 側ではこれらを JSON/YAML で受け取り、妥当性チェックとデフォルト補完を行うと安全です。

---

## SaaS 実装指針（実運用の観点）

1. 非同期ジョブ化
   - 受付 API（例: `POST /render`）は `run_id` を即時返却。実行はワーカー（キュー: Redis/SQS 等）。
   - 進捗は `-progress` から秒単位で記録し、`GET /render/{id}/progress` でポーリング返却。
2. ストレージ設計
   - 作業用 `{run_dir}` はローカル NVMe or 一時ボリューム。成果物はオブジェクトストレージへ退避。
   - 中間ファイル（`ffmpeg_scenes/`, `overlays/`, `ass/`, `temp_concat.mp4`）はオプションで削除。
3. 依存関係
   - FFmpeg は `--enable-libass --enable-libfreetype --enable-libx264` を必須化。`fonts/` をコンテナ内に同梱。
   - BGM が未提供なら自動スキップ（ストリームコピー）。
4. フォールトトレランス
   - 失敗時は STDERR 末尾のダンプを添えて再現性を確保。シーン粒度でリトライ可能に分割（同一 determinism を維持）。
5. パフォーマンス
   - シーン並列: 画像→シーン MP4 は CPU コア数に応じて並列化（最終 concat・BGM ミックスは単一）。
   - I/O 最適化: 入出力同一ボリューム＋`-movflags +faststart`。`ultrafast` プリセットで試算→画質要件に応じて調整。
6. セキュリティ・多言語
   - フォントと libass の組み合わせで日本語・多言語の表示崩れを避ける。ユーザーアップロードフォントは隔離。

---

## 参考コマンド（テンプレート）

- Opening（タイトル）テンプレート

```
ffmpeg -hide_banner -loglevel error -nostats \
  -t {D} -f lavfi -r {FPS} -i color=c=black:size={W}x{H} \
  -loop 1 -framerate {FPS} -t {D} -i title.png \
  -i narration.wav \
  -filter_complex "[0:v][1:v]overlay=x=(W-w)/2:y=(H-h)/2:eval=init:format=auto, \
                   fps={FPS},format=yuv420p[vout]" \
  -map [vout] -map 2:a:0 -r {FPS} -c:v libx264 -crf {CRF} -preset {PRESET} \
  -pix_fmt yuv420p -profile:v high -level:v 4.1 \
  -c:a aac -ar 48000 -movflags +faststart -shortest -y opening.mp4
```

- Content（pan_only）の骨子

```
ffmpeg -hide_banner -loglevel error -nostats \
  -i scene.jpg \
  -loop 1 -framerate {FPS} -t {D} -i seg0.png \
  -loop 1 -framerate {FPS} -t {D} -i seg1.png \
  -i narration.wav \
  -filter_complex " \
    [0:v]scale=iw*({COVER})*(1+{MARGIN}):ih*({COVER})*(1+{MARGIN})[s]; \
    [s]crop=w={W}:h={H}:x='{X(t)}':y='{Y(t)}',fps={FPS},format=yuv420p[base]; \
    [base][1:v]overlay=x=0:y=H-h:enable='between(t,{TS0},{TE0})'[v0]; \
    [v0][2:v]overlay=x=0:y=H-h:enable='between(t,{TS1},{TE1})'[vout]; \
    [vout]format=yuv420p[vout] " \
  -map [vout] -map 3:a:0 {ENCODE_ARGS} -shortest -y scene.mp4
```

- タイピング（ASS）追加

```
... -filter_complex " ... ; [LAST]subtitles=filename='scene.ass':fontsdir='fonts'[vout] "
```

- 連結

```
# list.concat.txt（例）
ffconcat version 1.0
file 'S001.mp4'
file 'S002.mp4'
...

ffmpeg -hide_banner -loglevel error -nostats -safe 0 -f concat \
  -i list.concat.txt -c copy -movflags +faststart -y temp_concat.mp4
```

- 最終 BGM ミックス

```
ffmpeg -hide_banner -loglevel error -nostats \
  -i temp_concat.mp4 -stream_loop -1 -i bgm.mp3 \
  -filter_complex "[1:a]atrim=0:duration={TOTAL},asetpts=PTS-STARTPTS, \
                   loudnorm=I=-30:LRA=7:TP=-2,volume=0.24, \
                   afade=t=in:st=0:d=0.5,afade=t=out:st={TOTAL-1.0}:d=1.0, \
                   aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[bgm]; \
                   [0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[narr]; \
                   [narr][bgm]amix=inputs=2:duration=first:dropout_transition=2[a]; \
                   [a]loudnorm=I=-14:LRA=7:TP=-1.5, \
                      aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[aout]" \
  -map 0:v -map [aout] -c:v copy -c:a aac -ar 48000 -ac 2 \
  -movflags +faststart -shortest -y final.mp4
```

---

## 実装時のハマりどころと対策

- libass が無い FFmpeg では `subtitles` フィルタが失敗 → ビルド時に `--enable-libass` 必須。
- 文字化け対策 → `fonts/` に対象言語フォントを同梱し、`fontsdir` を必ず指定。
- Ken Burns の境界条件 → `zoom <= 0` をエプシロン固定、`min(max(...))` で画角外を防ぐ。
- 音量の均一化 → ラウドネス二段正規化（BGM 先処理→全体最終処理）で破綻を防止。
- MP4 再生互換 → `yuv420p`, `bt709` タグ, `+faststart` を固定。

---

## まとめ

longVideoAI の FFmpeg 実装は、
- シーン分割→シーン内での Ken Burns＋テキスト合成→高速 concat→BGM ミックス、という段階化で高速・堅牢。
- ASS によるタイピング効果や、R128 ベースの音量最適化など、SaaS でそのまま役立つパターンが揃っています。

本書のテンプレートとパラメータ整理をベースに API 化すれば、可搬性の高い「長尺自動生成 SaaS」を構築できます。

---

## config.yaml 詳解（キーごとの役割と実装対応）

全体の読み込みは `config_loader.load_config()` が担当し、`AppConfig.raw` に YAML を保持します。ディレクトリ等の解決（出力先・ログ・一時ディレクトリ）は `AppConfig` が行い、以降の各コンポーネントへ `raw` を渡す構造です。

【renderer】
- `renderer: moviepy | ffmpeg`
- 役割: レンダラーの切替（`renderer_factory.make_renderer()`）。
- 推奨: SaaS では FFmpeg を既定に。切替はデバッグ用途とする。

【apis.pollinations】（画像生成/取得）
- キー: `model`, `width`, `height`, `aspect_ratio`, `retries`, `retry_backoff_base`, `timeout_connect`, `timeout_read`
- 使用箇所: `pollinations_client.PollinationsClient`
  - `width/height` は生成画像のピクセルサイズクエリに直結。
  - リトライ／バックオフ、接続/読み取りタイムアウトを requests に適用。
- 注意: `aspect_ratio` は現行実装では未使用（将来のプロンプト/サイズ調整用）。

【apis.deepl】（プロンプト英訳）
- キー: `api_key`
- 使用箇所: `prompt_translator.PromptTranslator`
  - 未設定時は原文のまま（ログ DEBUG）。
- セキュア運用: SaaS では `.env`／Secret Manager 等から注入し、平文 YAML に書かない（AGENTS.md 準拠）。

【apis.voicevox】（ローカル音声合成）
- キー: `host`, `port`, `speaker_id`, `speed_scale`, `volume_scale`, `intonation_scale`, `pitch_scale`, `output_sampling_rate?`, `output_stereo?`
- 使用箇所: `voicevox_client.VoicevoxClient`
  - `/version` で起動確認→`/audio_query`→`/synthesis` の順で合成。
  - 話速/音量/抑揚/ピッチは audio_query に上書き挿入。
  - 失敗時は無音 WAV をフォールバック生成（パイプライン継続）。

【video】（映像プロファイル）
- キー: `width`, `height`, `fps`, `format`, `codec`, `bitrate`, `quality`, `audio_bitrate`
- 使用箇所:
  - MoviePy 実装: `video_generator.VideoGenerator.RenderConfig`
  - FFmpeg 実装: `long_form/ffmpeg/renderer.RenderConfig` → `_encode_args()`
- 備考:
  - `format` はラッパーでは未使用（実質 MP4 固定）。
  - `quality` は現行未使用（将来の CRF/プリセット推奨値選択用）。
  - `codec` 既定は `libx264`、ピクセルフォーマット/色空間タグは `yuv420p + bt709` を固定。

【text】（フォントと色）
- キー: `font_family`, `font_path`, `default_size`, `colors.default`, `colors.highlight`, `colors.background_box`
- 使用箇所: 両レンダラーのオーバーレイ PNG 生成、ASS 生成（フォント名と太字判定）。
- 備考:
  - `font_path` が無効でも `fonts/NotoSansJP-*.ttf` → システムフォントへ順次フォールバック。
  - `background_box` は 8 桁 hex を推奨（RGBA）。

【animation】（Ken Burns・転換・タイピング）
- キー: `typewriter_speed`, `fade_duration_frames`, `default_transition`, `ken_burns_*`
- 使用箇所: 主に FFmpeg 実装（`long_form/ffmpeg/renderer.py`）
  - `ken_burns_mode: pan_only|zoompan`（既定 pan_only）
  - `ken_burns_zoom/offset/margin/motion_scale/max_margin`（移動量の総合パラメータ）
  - `ken_burns_full_travel`（可動域 100% 強制）
  - `ken_burns_pan_extent`（pan_only 時の移動割合）
  - `ken_burns_intro_relief/ken_burns_intro_seconds`（導入フレームの緩和）
- 備考: `typewriter_speed` は ASS タイピング時の速度倍率として利用（`long_video_main.py --typing-speed` と併用）。

【simple_mode】（長尺の粗い制御と画像プロンプト生成）
- キー: `duration_mode`, `padding_seconds`, `auto_voice`, `auto_images`, `default_image_prompt`, `default_image_prompt_template`, `prompt_constants`
- 使用箇所:
  - 時間設計: `timeline_builder.TimelineBuilder`（`duration_mode=voice` で音声尺ベース、`padding_seconds` は行間の余白秒）
  - 画像プロンプト: `asset_pipeline.AssetPipeline`（template と constants を合成して prompt を作成）
- 備考: `auto_voice/auto_images` は現実装では分岐に未使用（将来のトグル用）。

【sections】（シーン分割ポリシー）
- キー: `default_duration_seconds`, `min_duration_seconds`, `max_duration_seconds`, `max_chunks_per_scene`
- 使用箇所: `timeline_builder.TimelineBuilder`
  - テキストブロックをチャンク化し、`max_chunks_per_scene` までを 1 シーンに束ねる。
  - `duration_mode` が `voice` の場合、実音声尺＋行間パディングで見積り、`min/max` でクランプ。

【bgm】（BGM ライブラリ定義）
- キー: `library[] = {id, file_path, default_volume}`, `narration_boost`, `bgm_boost`
- 使用箇所:
  - `timeline_builder` は `bgm_track_id` をシーンへ割当て（ラウンドロビン）。
  - FFmpeg ミックスは現状 `background_music/Vandals.mp3` を固定参照（`long_form/ffmpeg/renderer._mix_bgm`）。
- 備考: ライブラリ連携は未結線。SaaS では `bgm_track_id -> 実ファイル` 解決ロジックを追加するとよい。

【output】（出力/一時/サムネイル）
- キー: `directory`, `temp_directory`, `keep_temp_files`, `subtitles`, `thumbnail_directory`
- 使用箇所: `config_loader.AppConfig`（各パスの解決）、`thumbnail_generator`
- 備考: `subtitles` は現実装で未参照（グローバル ON/OFF 用として将来拡張余地）。

【youtube】（アップロード設定）
- キー: `default_privacy`, `default_category`, `use_shorts_mode`, `title_template`, `description_template`, `credentials_file?`, `token_file?`
- 使用箇所: `youtube_uploader.YouTubeUploader`、`long_video_main._build_description()`
  - `description_template` は `{title}`, `{description}`, `{duration_seconds}` を想定。欠損キーは警告ログ。
  - `use_shorts_mode` は現実装で未使用（将来の縦型書き出し分岐等）。

【thumbnail】（サムネイル）
- キー: `width`, `height`, `title_font_size`, `subtitle_font_size`, `overlay_color`, `top_band_ratio`, `gap`
- 使用箇所: `thumbnail_generator.ThumbnailGenerator`
  - `overlay_color` は `rgba(r,g,b,a)` または 8 桁 hex 形式。α>0 でヒーロー画像に半透明オーバーレイを重畳。

【logging】（ログ出力）
- キー: `level`, `file`
- 使用箇所: `logging_utils.configure_logging()`
  - ルートロガーをコンソール＋ファイルの二系統で初期化。

---

### SaaS 運用のベストプラクティス（config まわり）

- シークレットは平文 YAML に置かず、`.env` または Secret Manager 経由で起動時に注入し、`config.raw` にマージする。
- 機能未結線キー（例: `video.quality`, `output.subtitles`, `youtube.use_shorts_mode`, `bgm.library`）は仕様策定のうえ段階的に結線。
- バリデーション層を用意（型・範囲・存在チェック）。不正値は安全側の既定値へフォールバックし、WARN ログを残す。
- 互換性のため、既定値は本プロジェクトのデフォルトを踏襲（変更はメジャーバージョン扱い）。

## 動画デザイン設計（テキスト帯・フォント・色・配置）

デザインは MoviePy/FFmpeg の両実装で同じ見た目になるよう、PNG オーバーレイ生成ロジックを共有しています。

【テキスト帯（ボトムバンド）の形状と余白】
- 角丸長方形を動画幅いっぱいに描き、下端に固定配置（FFmpeg では `overlay=y=H-h`）。
- パラメータ（フォントサイズ `size` を基準に算出）
  - 行間: `size * 0.42`（複数行）/ `size * 0.25`（単一行）
  - 外側マージン: 上 `max(size*0.12, 6)`、下 `max(size*0.35, 18)`
  - 内側パディング: 上 `max(size*0.45, 20)`、下 `max(size*0.7, 28)`
  - 左右マージン: `max(width*0.018, 18)`
  - 角丸半径: `max(size*0.42, 18)`
- 実装参照
  - FFmpeg 実装（PNG 生成）: `long_form/ffmpeg/renderer.py:530`
  - MoviePy 実装（同等の PNG 生成）: `video_generator.py:370`

【色とフォント】
- テキスト色: `text.colors.default`（既定 `#FFFFFF`）。 `config.yaml:43`
- バンド色: `text.colors.background_box`（8桁 hex で透過含む。既定 `#000000FF`）。`config.yaml:45`
- フォント: `text.font_path` 優先、なければ `fonts/NotoSansJP-*.ttf`、更に最終手段としてシステム `DejaVuSans` 系へフォールバック。
  - FFmpeg 実装: `long_form/ffmpeg/renderer.py:489`
  - MoviePy 実装: `video_generator.py:474`

【文字組みと中央寄せ】
- バンド内でテキスト塊の高さを算出し、上下パディング内の中央に垂直配置。各行は横中央揃え。
- 実装: FFmpeg `renderer.py:568` 近辺、MoviePy `video_generator.py:412` 近辺。

【オープニング（中央タイトル）】
- 画面中央に複数行テキストを垂直センタリング。行間は `font.size * 0.6`。
- フォントは太字（ExtraBold）を優先、サイズは実装既定で 75。
- 実装参照
  - FFmpeg: `long_form/ffmpeg/renderer.py:655`
  - MoviePy: `video_generator.py:434`

【タイピング演出（任意）】
- `overlay.type = typing` の場合、
  - PNG は「バンドのみ」（文字なし）を生成。
  - テキストは ASS を生成し `subtitles` フィルタで重ねる（位置は `\pos(x,y)` 指定）。
  - 行ごとに開始時刻・終端・cps（characters per second）を計算して 1 文字ずつ出す。
- ASS 生成: `long_form/ass_timeline.py:220`（`build_ass_centered_lines_typing`）。
- FFmpeg フィルタチェーンでは最終段で `subtitles` を適用し、バンドの上に文字が来るようにする。
  - 実装参照: `long_form/ffmpeg/renderer.py:846` 付近。

【Ken Burns と帯の重なり順】
- ベース映像（ズーム・パン）→（必要なら ASS）→帯 PNG 群 →（必要なら最終 ASS を再適用）→`format=yuv420p` の順。
- これにより、文字は常に半透明バンドより手前に描かれて読みやすさを確保。

【デザインを SaaS から操作する主要キー】
- `text.default_size`（本文フォントサイズ）・`text.font_path`・`text.colors.*`
- `overlay.type=static|typing`、`overlay.typing_speed`
- 角丸や余白はコード内の比率で決定（必要なら将来パラメータ化）。

---

## サムネイル生成ロジック（トップバンド＋ヒーロー）

生成器は `ThumbnailGenerator`。タイトル帯（上部）とヒーロー画像（下部）を 1 枚に合成します。

【サイズと基本レイアウト】
- 既定サイズ: 1280×720（`thumbnail.width/height`）。`config.yaml:118`
- トップバンド高: `max(height * top_band_ratio, title_font_size * 1.6)`、既定 `top_band_ratio=0.28`。`config.yaml:124`
- ヒーロー領域: 残りの高さから `gap`（既定 6px）を引いた範囲にフィット。`config.yaml:125`
- 実装: `thumbnail_generator.py:74`（`generate`）

【ヒーロー画像のフィット＆オーバーレイ】
- 画像は cover（中央トリミング）でフィット。`thumbnail_generator.py:233`（`_fit_image`）
- `thumbnail.overlay_color` を RGBA で受け取り、α>0 の場合はヒーロー全体に合成（暗幕など）。既定は透明。`config.yaml:123` / `thumbnail_generator.py:134`

【タイトル文字の折り返しとサイズ調整】
- 最大幅（左右 40px ずつ余白 = 幅 - 80）でグリフ単位に折り返し。`thumbnail_generator.py:152`（`_fit_text_lines`）
- 行数上限 3。超える場合はフォントを段階的に 0.9 倍まで縮小し、それでも溢れれば末尾行に詰め込み。`thumbnail_generator.py:152`
- 描画はトップバンドの垂直中央に複数行をセンタリング。行間は `title_font_size * 0.3`。`thumbnail_generator.py:199`

【フォント解決】
- タイトル: `thumbnail.title_font_path` → `text.font_path` → `fonts/NotoSansJP-ExtraBold.ttf`
- サブタイトル: `thumbnail.subtitle_font_path` → `text.font_path` → `fonts/NotoSansJP-Bold.ttf`
- 実装: `thumbnail_generator.py:56`

【サブタイトルの扱い】
- 現実装ではサブタイトルは未使用（コメントで明示）。必要なら `_draw_text_block` に行列を渡せば拡張可能。`thumbnail_generator.py:116`

【出力先】
- `output/thumbnails/`（設定 `output.thumbnail_directory` で変更可）。`config.yaml:105`

---

## スクリプト→デザインへのマッピング（字幕テキストと尺）

- スクリプトはブロックごとにセクション化し、冒頭は Opening、その後は Content シーンにグルーピング。`script_parser.py:1` / `timeline_builder.py:1`
- 音声合成（VOICEVOX）で各チャンクの実尺を取得し、その `start_offset`/`duration` を字幕帯の表示タイミングに使用（テキストは行配列で保持）。`asset_pipeline.py:148`
- Content シーンのテキストセグメントは、この音声区間に合わせてボトムバンド PNG（または ASS）を `enable='between(t,start,end)'` で出し入れ。`long_form/ffmpeg/renderer.py:840`

---

## デザイン面の SaaS 仕様化ポイント（提案）

- API 入力例（抜粋）
  - `design: { font_path, text_color, band_color_rgba, default_size }`
  - `overlay: { type, typing_speed }`
  - `thumbnail: { width, height, top_band_ratio, gap, overlay_rgba }`
  - `ken_burns: { mode, pan_extent, margin, offset, motion_scale }`
- 返却メタデータ
  - バンド PNG/ASS の幾何情報（`band_height`, `horizontal_margin`, `text_top_y`）
  - サムネイルの実フォントサイズ・行分割結果（A/B テスト分析に有用）

---

## 既知の制約・拡張余地（デザイン）

- バンドの比率・角丸・余白はコード定数（比例）で決定。将来的に設定化するとブランド別テーマが捌きやすい。
- ASS と PNG を併用する構成は柔軟だが、フォントの同梱と libass 有効化が前提（Docker イメージで固定化推奨）。
- サムネイルは現在サブタイトル非表示。`thumbnail.subtitle_*` を使った二段見出し対応は容易。

---

## FFmpeg ロング動画生成における重要ファイル 詳細

この章では、FFmpeg ベースの長尺生成に直結する重要モジュールを用途別に掘り下げます。SaaS 実装での責務分割や監視ポイント設計に活用してください。

【long_form/ffmpeg/renderer.py】（FFmpeg レンダラー中核）
- 役割: MoviePy 等価の `VideoGenerator` を FFmpeg で再実装。Opening/Content 各シーンの MP4 書き出し→`concat`→BGM ミックスまでを担当。
- 公開 API: `render(run_dir, scenes, output_path, thumbnail_title) -> Path`。
- 主な内部フロー:
  - `_render_opening_scene` … 黒背景＋中央タイトル PNG＋ナレーションを合成（`-filter_complex` は中央 overlay → fps → yuv420p）。
  - `_render_content_scene` … ベース静止画の Ken Burns（`pan_only|zoompan`）→ボトム帯 PNG を `between(t,st,en)` で出し入れ。`overlay.type=typing` 時は ASS を発行し `subtitles` で重畳。
  - `concat_mp4_streamcopy` … シーン群を ffconcat でコピー連結。
  - `_mix_bgm` … 連結済み映像に BGM をループ/トリム→`loudnorm(-30)`→`volume/afade`→ナレーションと `amix`→全体を `loudnorm(-14)` で仕上げ。映像は `-c:v copy`。
- フィルタグラフ生成:
  - `_overlay_center_filter(w,h,fps)` … Opening 用の中央配置。
  - `_build_content_filter(...)` … Ken Burns の式生成（`pan_only` は `crop` の x(t),y(t)、`zoompan` は `zoom` のステップ加算＋オフセット移動）。PNG 帯 overlay、ASS の適用順もここで決定。
- エンコード既定（`_encode_args`）: `-r {fps} -c:v {codec} -pix_fmt yuv420p -profile:v high -level 4.1 -color_primaries bt709 -color_trc bt709 -colorspace bt709 -movflags +faststart -c:a {audio_codec} -ar {sr}`。CRF/bitrate/preset/audio_bitrate は設定に応じ追加。
- キャッシュ/生成物: `overlays/`（帯PNG/タイトルPNG）、`ass/`（タイピングASS）、`ffmpeg_scenes/`、`temp_concat.mp4`。フォントは `fonts/` を優先し、なければシステムフォールバック。
- 進捗: シーンは静かに実行、本書き出し時のみ `run_ffmpeg_stream(..., expected_duration, label="Render")` を用い 1 本のバーを表示。

【long_form/ffmpeg/runner.py】（FFmpeg 実行ラッパ）
- `run_ffmpeg(args, cwd=None)` … `ffmpeg -hide_banner -loglevel error -nostats` に引数連結し実行。非 0 終了時は STDERR の末尾 ~50 行を `logger.error` し例外送出。
- `run_ffmpeg_stream(args, expected_duration_sec, label, on_draw=None, cwd=None, external_bar=None, offset_seconds=0.0)` … 進捗版。`-progress pipe:1` を付与し、`progress.ProgressParser` で `out_time_ms` をパース→`ConsoleBar` を更新。外部バー合流とステップラベル付けに対応。

【long_form/ffmpeg/progress.py】（進捗・バー描画）
- `ConsoleBar(total_seconds, label, width=24)` … 10fps レート制限付きで描画。経過/総時間、ETA を表示。`finish()` で改行し確定。
- `ProgressParser(on_time)` … `key=value` 行から `out_time_ms` を秒化してコールバック。`run_ffmpeg_stream` から利用。

【long_form/ffmpeg/concat.py】（MP4 セグメント連結）
- 入力検証: 存在/サイズ>0 をチェック。不正時は件数・例示を ERROR ログ。
- 単独入力はストリームコピーの最短経路。
- 複数入力は `ffconcat version 1.0` のリストを作成し、`-safe 0 -f concat -i list.txt -c copy -movflags +faststart`。失敗時はリストの head/tail をデバッグ出力。

【long_form/typing_overlay.py】（ASS＋FFmpeg のタイピング合成 CLI）
- 役割: 入力動画にテキスト（静的 or タイピング）を ASS で重畳して出力。`--ass-only` で ASS 作成のみも可。
- 構成: 画面解像度は `ffprobe` で自動取得→`build_ass()` で ASS を生成→`run_ffmpeg()` で `-vf subtitles=filename='...':fontsdir='fonts'` を適用。フォント/色/位置/余白は CLI で調整可能。
- 出力: `output/`, ログは `logs/typing_*.log`。

【long_form/ass_timeline.py】（ASS 文字列ビルダー）
- `build_ass_for_scene(...)` … ボトム中央寄せ（Alignment=2）。静的 or タイピング（1 文字ごとイベント）の両対応。
- `build_ass_for_content_scene_pos(...)` … 各セグメントに座標（左上）を与える版。`\pos(x,y)` を行頭に付し、帯 PNG に合わせて正確に配置。
- `build_ass_centered_lines_typing(...)` … 行ごとのタイピング（`LineTypingSpec`）を受け取り、最終整列が中央になるよう左上座標を事前計算したうえで 1 文字出し。
- エスケープ規則: `{`/`}`/`\` は全角に置換、改行は `\N`。

【renderer_factory.py】（レンダラー切替）
- `make_renderer(config)` が `config['renderer']` を見て `VideoGenerator`（MoviePy）/`FFmpegVideoGenerator` を遅延 import。SaaS ではフラグで強制切替可能。

【long_video_main.py】（CLI エントリ・実行時上書き）
- `--type static|typing`, `--typing-speed <float>` を受け取り、`config.raw['overlay']` に反映（`overlay.type`, `overlay.typing_speed`）。
- `--output-dir` の指定は `config.output_dir` と `config.raw['output']['directory']` を同時に上書き。
- `--upload` 時は `youtube_uploader` を経由（本 FFmpeg 生成とは独立）。

【long_pipeline.py】（オーケストレーション）
- スクリプト→タイムライン→アセット生成（音声/画像）→レンダリング→サムネイル→plan/timeline の書き出しまでの流れを統括。
- `renderer_factory.make_renderer(config.raw)` で FFmpeg 実装を選択し、`ScenePlan` 群を渡す。Opening/Content の `ken_burns_vector` はシーン ID を種に疑似乱数から決定（再現可能性）。

【timeline_builder.py】（長尺のシーン化）
- ブロックごとの語数/行数から秒数を見積り、`min/max/default` の枠に収めつつチャンクを束ねて 1 シーン化。`max_chunks_per_scene` で上限。`padding_seconds` は行間の隙間時間。

【asset_pipeline.py】（素材作成）
- VOICEVOX でチャンク音声を合成→結合→区間配列（`start_offset`/`duration`）を生成。これが FFmpeg の PNG 帯/ASS の表示タイミングになる。
- 画像は Pollinations から取得（失敗時は `default_img/` → さらにプレースホルダー）。プロンプトは `default_image_prompt_template` と `prompt_constants` で生成。

---

## 補遺: 追加の重要ファイル（見落とし防止）

【config_loader.py】（設定ローダー／パス解決）
- YAML を読み取り `AppConfig.raw` に保持。`output.directory`／`temp_directory`／`logging.file` などの実パスを解決し、`LongFormPipeline` 等へ供給。
- `AppConfig.logging_level` でログレベルを正規化。SaaS では起動時の設定検証・既定値適用の中心。

【logging_utils.py】（ロギング初期化）
- `configure_logging(level, log_file)` でコンソール＋ファイルの二系統を初期化。重複ハンドラ抑止や統一フォーマット（時刻/レベル/モジュール/メッセージ）。
- FFmpeg 実行ラッパのエラー尾部・進捗ログもここに集約されるため、SaaS の可観測性（収集・検索）に直結。

【script_parser.py】（原稿→セクション分割）
- 冒頭メタ（`s"タイトル"`, `tags"..."`, `description"..."`）を抽出し、残りを空行区切りでセクション化。
- `word_count` は英語系は空白分割、日本語等は「約3文字=1語」で見積る。これが `timeline_builder` の尺見積りベースに。

【speech_sanitizer.py】（VOICEVOX向け前処理）
- 引用符/括弧類を除去し、連続スペース/タブを圧縮。句読点や改行は保持。
- 目的: 合成時の不自然なポーズや読み崩れを抑止し、音声尺の安定性を向上（字幕タイミングのブレ抑制）。

【pollinations_client.py】（画像取得クライアント）
- `model/width/height` をクエリとして付与し画像を取得。`retries`・指数バックオフ・接続/読み取りタイムアウトを持つ。
- 404 は即失敗、429/5xx はリトライ。成功時は `images/` 配下に保存（キャッシュ）。

【prompt_translator.py】（DeepL 翻訳）
- `apis.deepl.api_key` が無い場合は原文を返す。存在時は英訳してプロンプト品質を底上げ。レスポンスは簡易キャッシュ。

【thumbnail_generator.py】（サムネイル合成）
- 先述の「サムネイル生成ロジック」詳細に対応。`output.thumbnail_directory` に PNG を保存。

【video_generator.py】（MoviePy 版の等価レンダラー）
- FFmpeg 実装の比較対象・フォールバック。Ken Burns・帯PNG・BGM 混合など同等の見た目を MoviePy で実装。
- SaaS では互換検証やデバッグ用バックエンドとして温存可能。

【ffmpeg-generator.md】（設計メモ）
- MoviePy→FFmpeg 段階移行の方針・効果測定の目標・実装範囲を記録。SaaS の設計判断やドキュメント化に有用。

---

## 補遺: ランタイム要件・依存・アセット運用

【voicevox_client.py】（ローカル TTS クライアントの詳細）
- 接続検証: `/version` で疎通確認。失敗時は無音WAVを生成してパイプラインを継続（堅牢性重視）。
- 合成フロー: `/audio_query`（話速・音量・抑揚・ピッチ・出力サンプリングレート/ステレオ等を上書き）→`/synthesis`。
- ポーズ短縮: `prePhonemeLength`/`postPhonemeLength` を 0.06 に設定し、不要な間を圧縮（字幕帯のズレ抑制）。
- 出力WAVの尺: 実際の `frames / samplerate` から秒数を算出し、セグメントの `start_offset`/`duration` を確定。

【requirements.txt と任意依存】
- 必須: `moviepy`, `imageio(-ffmpeg)`, `numpy`, `pillow`, `requests`, `PyYAML`, `pyloudnorm`（MoviePy 経路の正規化用）。
- 任意: `fontTools`（ASS の `FontName` を PS 名で強制する際の精度向上）。未導入でもファイル名 stem にフォールバック。
- YouTube 連携は `google-api-python-client` 一式が任意（`youtube_uploader.py`）。

【FFmpeg バイナリ要件】
- 機能: `--enable-libass --enable-libfreetype --enable-libx264` が必要（`subtitles`, `drawtext`系, H.264）。
- チェック例: `ffmpeg -hide_banner -filters | grep subtitles`、`ffmpeg -hide_banner -buildconf`。
- PATH: ランタイムは `subprocess` から `ffmpeg/ffprobe` を直接呼ぶため、`PATH` で解決可能にしておく。

【アセット/ディレクトリ指針】
- `fonts/`: 日本語含む多言語フォント（例: `NotoSansJP-Bold.ttf`, `NotoSansJP-ExtraBold.ttf`）。ASS 合成時は `fontsdir=fonts` を指定。
- `background_music/`: 既定 `Vandals.mp3` を参照。SaaS ではユーザー BGM とライブラリIDの双方に対応できる抽象化を設計。
- `default_img/`: 画像取得失敗時のフォールバック素材ディレクトリ。最終手段としてプレースホルダーを生成。
- `output/`, `temp/`, `logs/`: 書き出し先と一時、ログ保存先。`logs/run.log` は `logging_utils` で初期化。
- `credentials/`: YouTube OAuth クレデンシャル/トークン格納先。秘匿前提でバージョン管理から除外。

【運用チェックリスト（SaaS 用）】
- フォント: 必要言語の TTF/OTF を `fonts/` に配置し、権利を確認。
- FFmpeg: `libass` が有効なビルドを配布し、CIで `subtitles` 可用性を検証。
- BGM: 音量正規化を二段で行うため、入力音源のピークが高すぎても破綻しにくいが、無音/モノラルも受け入れ可能かテストする。
- TTS: VOICEVOX 非稼働時のフォールバック（サイレンス）でもパイプラインが終了まで通ることを確認。
