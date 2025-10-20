# longVideoAI SaaS インフラ開発計画（MVP）

## 1. 全体アーキテクチャ概要
- **利用者フロー**: クライアントがWeb UIで台本・設定を登録 → APIがジョブIDを発行 → バックエンドのジョブオーケストレーターがCloud Run Jobsへ動画生成を投入 → 生成成果物とログをCloudflare R2へ保存 → 完了通知とダウンロードURLを返却。

## 2. フロントエンド
- **ホスティング**: Cloudflare Pages（無料枠で500ビルド/月、無制限帯域）。
- **機能**:
  - ジョブ登録フォーム（台本テキスト、生成パラメータ、顧客IDなど）。
  - 進捗表示と結果ダウンロード（Workers APIからポーリング）。
  - 認証はメールリンク等の軽量方式。
- **ポイント**: Pages Functionsも利用可能だが、主処理はWorkers側で担当。環境変数はPagesプロジェクト設定で管理。

## 3. バックエンド（API・制御プレーン）
- **実装**: Cloudflare Workers Paidプラン（月5ドル）。
- **役割**:
  - `POST /jobs`: 入力バリデーション、ジョブID発行、Cloud Run Jobs起動。
  - `GET /jobs/{id}`: 進捗・ログの取得（D1メタデータ）。
  - `GET /jobs/{id}/result`: 署名付きURL返却（R2）。
  - Webhook処理（完成通知、失敗通知）。
- **データストア**:
  - Cloudflare D1にジョブ状態やメタ情報を保存。
- **セキュリティ**: Workers SecretsでAPIキー・署名用鍵を管理。ジョブIDはUUID、結果URLは短時間の署名付きURL。

## 4. 動画生成ワークロード
- **メイン**: Google Cloud Run Jobs
  - Dockerイメージ: Python 3.11 + FFmpeg + VOICEVOX Engine（CPU版）+ longvideoai ソース。
  - エントリポイントでVOICEVOXを起動→ヘルスチェック→`long_video_main.py`実行。
  - 同時実行数（例: 2）で制御、最大実行時間は168時間まで設定可。
  - ジョブ完了後に生成物（MP4/サムネ/log JSON）をR2へアップロードし、メタ情報をD1へ記録する。
  - Cloud Loggingへ構造化ログを出力（処理時間、失敗理由、音声生成時間など）。
- **ストレージ**: Cloudflare R2（$0.015/GB、イグレス無料）。クライアントごとに最新1本のみを24時間保持し、Lifecycle設定・Workersバッチで自動削除する。
- **音声合成**: コンテナ内VOICEVOXを使用し、エンドポイントURLは環境変数で管理する。

## 5. 補助ツール・オペレーション
- **ログ/監視**: Cloud Logging + Error Reporting（Run Jobs）、Workers Analytics。
- **CI/CD**: Cloud BuildまたはGitHub ActionsでDockerビルド→Artifact Registry→Cloud Run Jobsへデプロイ。Pages/WorkersはGitHub連携で自動デプロイ。
- **構成管理**: TerraformまたはPulumiでCloud Run Jobs、R2バケット、Workers設定をIaC化。

## 6. MVP実装のTODOサマリ
1. longvideoai のDockerfile整備（FFmpeg・VOICEVOX同梱、設定を環境変数化）。
2. Cloud Run Jobs へデプロイ、テスト用ジョブ実行で処理時間とログ確認。
3. R2アップロード & 署名URL返却ロジック追加、Lifecycle設定。
4. Workers API（ジョブ投入・ステータス・結果URL）実装、Pagesから疎通テスト。
5. モニタリングとコスト記録テンプレート整備（1本あたりCPU秒/生成サイズ）。


## 7. 追加メモ（2025-10-17 更新）
- **解像度ポリシー**: 動画解像度を1280x720 (720p) に固定し、レンダリング負荷とファイルサイズを抑える。
- **成果物の保持方針**: クライアントごとに最新1本のみをCloudflare R2へアップロードし、24時間で自動削除。署名付きURL経由でダウンロードできる仕組みにし、再取得は期限内に限定。新しい動画が完成した時点で旧オブジェクトを削除する。
- **軽量DB戦略**: Cloudflare D1でジョブメタ情報を管理し、Workersから直接読み書きする。
- **ログ運用**: 動画ファイルは長期保存しないため、Cloud Run Jobsの構造化ログとWorkersログをCloud Logging/Workers Analyticsに残し、トラブル時はログから原因を追う。
- **ダウンロード通知**: Workersで完了通知メール（またはダッシュボード表示）に「24時間以内に保存してください」と明記し、期限切れ後は再生成で対応する運用に統一。
- **画像生成のポリシー**: デフォルトはPollinations APIを利用し、レスポンス遅延や失敗率が閾値を超えた場合はFireworks AI（FLUX.1など）へフォールバックする二段構成とする。
- **BGMの取り扱い**: 楽曲ファイルはリポジトリに含めず、Cloudflare R2 の `bgm/{client_id}/current.mp3` のようなプレフィックスにアップロードして参照する。YouTube Audio Libraryなど外部ソースからユーザーが取得したファイルを配置し、標準ライセンス/CC BYに応じてクレジット追加を行う。ローカル開発時は `background_music/.gitignore` で各自のファイルを管理。
- **プラン管理**: 固定プラン（例: 月300/500/1000分）に加え、Super Adminがクライアント単位で上限や料金をカスタマイズできる仕組みを持つ。ユーザーごとに`plan_id`と`custom_limits`を保持し、`custom_limits`が設定されている場合はそちらを優先する。月初に自動リセットするクォータ管理を用意し、上限超過時は生成ボタンを無効化する。
- **ローカル実行**: Cloud Run Jobs用Dockerイメージをそのままローカルで`docker run`し、`sample_txt`や`output`をボリュームマウントして動作確認する。外部API接続先は環境変数で切り替え、VOICEVOXはコンテナ内起動で完結させる。

## 8. リポジトリ構成（モノレポ方針）
```
.
├── frontend/               # Cloudflare Pages Fronend (Next.js)
├── workers/                # Cloudflare Workers (API)
├── jobs/                   # Cloud Run Jobs + longvideoai コンテナ
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/                # Python/FFmpegロジック
│   ├── scripts/
│   └── assets/
├── shared/                 # 共通の型/スキーマ/ユーティリティ
├── db/                     # Cloudflare D1 スキーマ管理
│   ├── schema/
│   ├── migrations/
│   └── wrangler.toml
├── infra/                  # Terraform/Pulumi、環境構築ドキュメント
├── saas_ideas/             # development_plan.md 等
├── docs/                   # 要件・仕様メモ
├── background_music/       # ローカル配置用（.gitignore）
└── .github/workflows/      # CI/CD（Pages/Workers/Run Jobs/D1 等）
```

## 9. データベーススキーマ（Cloudflare D1）
```
users (
  id TEXT PRIMARY KEY,
  email TEXT UNIQUE,
  password_hash TEXT,
  role TEXT,
  plan_id TEXT,
  custom_limit_minutes INTEGER NULL,
  is_active INTEGER DEFAULT 1,
  created_at TEXT,
  updated_at TEXT
)

plans (
  id TEXT PRIMARY KEY,
  name TEXT,
  monthly_limit_minutes INTEGER,
  description TEXT
)

usage_counters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT,
  period_start TEXT,
  consumed_minutes INTEGER,
  generated_jobs INTEGER,
  last_job_id TEXT,
  updated_at TEXT,
  UNIQUE(user_id, period_start)
)

jobs (
  id TEXT PRIMARY KEY,
  user_id TEXT,
  status TEXT,
  script_title TEXT,
  duration_minutes REAL,
  style TEXT,
  thumbnail_template TEXT,
  voicevox_speaker TEXT,
  voice_speed REAL,
  voice_intonation REAL,
  result_url TEXT,
  expires_at TEXT,
  created_at TEXT,
  completed_at TEXT,
  error_message TEXT
)

audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT,
  action TEXT,
  payload JSON,
  created_at TEXT
)
```
- 月間上限は `plans` と `users.custom_limit_minutes` で管理。`usage_counters` は月初にリセット。
- D1 のマイグレーションを `db/migrations` でコード管理し、GitHub Actions (`wrangler d1 migrations apply`) で自動適用。
- **追加補助テーブル**
```
job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  user_id TEXT,
  event TEXT,                -- queued / started / completed / failed など
  payload JSON,
  created_at TEXT
)

style_presets (
  id TEXT PRIMARY KEY,
  name TEXT,
  provider TEXT,             -- pollinations / fireworks など
  prompt TEXT,
  negative_prompt TEXT,
  fallback_order INTEGER,
  is_active BOOLEAN DEFAULT 1,
  created_at TEXT,
  updated_at TEXT
)

voice_profiles (
  id TEXT PRIMARY KEY,
  provider TEXT,
  name TEXT,
  voicevox_speaker TEXT,
  speed_min REAL,
  speed_max REAL,
  pitch_min REAL,
  pitch_max REAL,
  default_speed REAL,
  default_intonation REAL,
  created_at TEXT,
  updated_at TEXT
)

bgm_assets (
  id TEXT PRIMARY KEY,
  user_id TEXT,
  r2_object_key TEXT,
  license_type TEXT,
  credit_text TEXT,
  is_active BOOLEAN DEFAULT 1,
  uploaded_at TEXT
)

notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT,
  type TEXT,                 -- job_complete / quota_warning など
  payload JSON,
  status TEXT,               -- pending / sent / failed
  attempt_count INTEGER DEFAULT 0,
  created_at TEXT,
  sent_at TEXT
)

metadata_annotations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  keywords TEXT,
  summary TEXT,
  captions JSON,
  thumbnail_text TEXT,
  created_at TEXT,
  updated_at TEXT
)
```
- ジョブ監査、テンプレ/音声/背景音源管理、通知キュー、動画メタデータ検索を支える補助テーブルとして追加。
- **フロント/Workers実装スタック**: フロントエンドは Next.js(TypeScript) を Cloudflare Pages にデプロイし、APIレイヤーは TypeScript 製の Cloudflare Workers(Wrangler) で実装する。共通の型定義は `shared/` ディレクトリで管理し、Pages/Workers 双方から利用する。
- **MVPでの必須運用項目**
  1. ジョブ失敗時の自動リトライと利用者通知（失敗理由と再実行案内）。
  2. 台本・タグ・BGMファイルの入力バリデーション（サイズ/文字数制限）。
  3. 生成完了／失敗通知の一次経路（メールまたはUI上の明示的ステータス表示）。
  4. `usage_counters`・`job_events` 等へのログ記録で月間利用量と成功/失敗履歴を残す。
- **リアルタイムキャッシュ**: MVPではCloudflare D1のみでジョブ状態・利用量を管理し、Workersから直接参照する。高頻度ポーリングが問題化した段階でUpstash RedisやDurable Objectsによるキャッシュ層を追加検討する。
- **UI骨格**: 画面上部にロゴ・ダッシュボードリンク・ログアウトを含むヘッダー、下部に規約・お問い合わせリンク等をまとめたフッターを配置。サイドバーはMVPでは設けず、主コンテンツを1カラムで構成する。
- **アカウント有効化フロー**: Super Admin画面で `users.is_active` を切り替え、支払い確認後に手動で有効化。`is_active=0` のユーザーはログイン不可とし、契約終了時は手動で無効化する。
