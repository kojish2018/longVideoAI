"""CLI entry for shashin_mode pipeline."""
from __future__ import annotations

import argparse
import random
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from config_loader import load_config
from logging_utils import configure_logging, get_logger
from youtube_uploader import YouTubeUploader

from .config import LayoutConfig, ModePaths, TimingConfig, load_renderer_settings
from .pipeline import ShashinPipeline
from .script_loader import load_script
from .thumbnail_builder import generate_thumbnail
from .youtube_support import (
    apply_youtube_channel_profile,
    build_youtube_metadata,
    resolve_publish_at_string,
)

logger = get_logger(__name__)

SHIKOKU_METAN_AMAAAMA_ID = 0

# ヒラギノ明朝のパス候補（macOS標準）
HIRAGINO_MINCHO_CANDIDATES = [
    "/System/Library/Fonts/ヒラギノ明朝 ProN.ttc",
    "/System/Library/Fonts/ヒラギノ明朝 Pro.ttc",
    "/Library/Fonts/ヒラギノ明朝 ProN.ttc",
]


def _resolve_subtitle_font(user_specified: Optional[str]) -> Optional[str]:
    """字幕フォントパスを解決。未指定ならヒラギノ明朝を試す。"""
    if user_specified:
        return user_specified
    
    # ヒラギノ明朝の候補を順に試す
    for candidate in HIRAGINO_MINCHO_CANDIDATES:
        candidate_path = Path(candidate)
        if candidate_path.exists():
            logger.info("Using default font: %s", candidate)
            return candidate
    
    # 見つからない場合はNone（フォールバックに任せる）
    logger.info("ヒラギノ明朝が見つかりません。デフォルトフォントを使用します。")
    return None


def _apply_shikoku_metan_voice(config) -> None:
    """Shashin mode 専用で VOICEVOX を四国めたん「あまあま」に固定する。"""
    raw = getattr(config, "raw", None)
    if not isinstance(raw, dict):
        logger.warning("config.raw が dict ではないため VOICEVOX 上書きをスキップします")
        return

    apis = raw.setdefault("apis", {})
    if not isinstance(apis, dict):
        logger.warning("apis セクションが dict ではないため VOICEVOX 上書きをスキップします")
        return

    voicevox_cfg = dict(apis.get("voicevox") or {})
    voicevox_cfg.update(
        {
            "speaker_id": SHIKOKU_METAN_AMAAAMA_ID,
            "profile": "shashin_metan_amaama",
        }
    )
    apis["voicevox"] = voicevox_cfg

    profiles = apis.setdefault("voicevox_profiles", {})
    if isinstance(profiles, dict):
        profiles.setdefault(
            "shashin_metan_amaama",
            {
                "speaker_id": SHIKOKU_METAN_AMAAAMA_ID,
                "speed_scale": voicevox_cfg.get("speed_scale", 0.95),
                "volume_scale": voicevox_cfg.get("volume_scale", 1.4),
                "intonation_scale": voicevox_cfg.get("intonation_scale", 1.3),
                "pitch_scale": voicevox_cfg.get("pitch_scale", -0.05),
            },
        )

    logger.info(
        "Shashin mode voice override applied: 四国めたん(あまあま) speaker_id=%s",
        SHIKOKU_METAN_AMAAAMA_ID,
    )


def _interactive_select_thumbnail_images(image_candidates: list[Path]) -> tuple[Path, Path]:
    """インタラクティブにサムネイル用の2枚の画像を選択する。"""
    if len(image_candidates) < 2:
        logger.warning("画像候補が2枚未満のため、自動選択します")
        if len(image_candidates) == 1:
            return image_candidates[0], image_candidates[0]
        raise ValueError("画像候補がありません")

    # 画像フォルダをFinderで開く
    if image_candidates:
        image_dir = image_candidates[0].parent
        try:
            subprocess.run(["open", str(image_dir)], check=True)
            print(f"\nFinderで画像フォルダを開きました: {image_dir}")
            print("画像を確認してから、ターミナルに戻って選択してください。")
        except subprocess.CalledProcessError:
            logger.warning("Finderでフォルダを開けませんでした。続行します。")
        except Exception as e:
            logger.warning("Finderでフォルダを開く際にエラーが発生しました: %s", e)

    # inquirerライブラリを使って矢印キーで選択できるメニューを表示
    try:
        import inquirer
        
        # 左側用の画像を選択
        print("\nサムネイル用の画像を選択してください")
        choices = [
            (f"{img_path.name} ({img_path})", idx)
            for idx, img_path in enumerate(image_candidates)
        ]
        
        questions = [
            inquirer.List(
                "left",
                message="左側用の画像を選択してください",
                choices=choices,
                default=choices[0] if choices else None,
            ),
        ]
        left_answer = inquirer.prompt(questions)
        left_index = left_answer["left"] if left_answer else 0
        
        # 右側用の画像を選択
        questions = [
            inquirer.List(
                "right",
                message="右側用の画像を選択してください",
                choices=choices,
                default=choices[1] if len(choices) > 1 else choices[0],
            ),
        ]
        right_answer = inquirer.prompt(questions)
        right_index = right_answer["right"] if right_answer else (1 if len(choices) > 1 else 0)
        
    except ImportError:
        # inquirerがインストールされていない場合は、シンプルな選択メニューにフォールバック
        print("\nサムネイル用の画像を選択してください:")
        print("利用可能な画像候補:")
        for idx, img_path in enumerate(image_candidates):
            print(f"  [{idx}] {img_path.name} ({img_path})")
        
        def _get_image_index(prompt: str) -> int:
            while True:
                try:
                    user_input = input(f"\n{prompt} [0-{len(image_candidates)-1}]: ").strip()
                    if not user_input:
                        continue
                    index = int(user_input)
                    if 0 <= index < len(image_candidates):
                        return index
                    print(f"エラー: 0から{len(image_candidates)-1}の範囲で入力してください")
                except ValueError:
                    print("エラー: 数値を入力してください")
        
        left_index = _get_image_index("左側用の画像番号")
        right_index = _get_image_index("右側用の画像番号")

    selected_left = image_candidates[left_index]
    selected_right = image_candidates[right_index]
    print(f"\n選択した画像:")
    print(f"  左側: {selected_left.name}")
    print(f"  右側: {selected_right.name}")

    return selected_left, selected_right


DEFAULT_BACKGROUND = Path("shashin_mode/assets/sakura.mp4")


def _normalise_bgm_name(value: str) -> str:
    """Normalize BGM filename, adding .mp3 extension if missing."""
    trimmed = str(value).strip()
    if not trimmed:
        raise ValueError("Background music name cannot be empty.")
    if not trimmed.lower().endswith(".mp3"):
        trimmed = f"{trimmed}.mp3"
    return trimmed


def _apply_background_music(*, config, override: Optional[str] = None) -> tuple[str, str]:
    """Apply background music settings from config, similar to long_video_main.py"""
    if not isinstance(config.raw, dict):
        raise ValueError("Configuration root must be a mapping to select background music.")

    bgm_cfg = config.raw.setdefault("bgm", {})
    if not isinstance(bgm_cfg, dict):
        bgm_cfg = {}
        config.raw["bgm"] = bgm_cfg

    directory = str(bgm_cfg.get("directory") or "background_music").strip()
    if not directory:
        directory = "background_music"
    bgm_cfg["directory"] = directory

    if override:
        selected = _normalise_bgm_name(override)
    else:
        selected_raw = bgm_cfg.get("selected") or "Countrysky.mp3"
        selected = _normalise_bgm_name(str(selected_raw))

    bgm_cfg["selected"] = selected
    return directory, selected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shashin mode video generator")
    parser.add_argument("script", help="Path to the input TXT script")
    parser.add_argument(
        "--background",
        default=str(DEFAULT_BACKGROUND),
        help=f"Path to background GIF/MP4 (default: {DEFAULT_BACKGROUND})",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml (VOICEVOX設定読み込み用)")
    parser.add_argument(
        "--output",
        help=(
            "Output video path (.mp4). If you pass a file path, run_id is appended to avoid overwrite "
            "(sample.mp4 -> sample_<run_id>.mp4). If you pass a directory, run_id.mp4 is created inside."
        ),
    )
    parser.add_argument(
        "--image-source",
        choices=["openverse", "bing_api", "google", "bing", "local", "none"],
        default="openverse",
        help="画像取得プロバイダ。デフォルトは Openverse（無料・ライセンス付き）。Bing API はオプション。",
    )
    parser.add_argument("--image-prefix", default="", help="画像検索用の先頭キーワード (例: '猫 写真')")
    parser.add_argument("--fallback-image", help="検索失敗時に使うローカル画像パス")
    parser.add_argument("--wrap", type=int, help="1行あたりの最大文字数。超えたら自動改行")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--subtitle-font", help="字幕用フォントファイルへのパス")
    parser.add_argument("--subtitle-size", type=int, default=60)
    parser.add_argument("--chunk-padding", type=float, default=0.35, help="音声長に足す余白秒数")
    parser.add_argument("--min-chunk-duration", type=float, default=1.4, help="チャンクの最小秒数")
    parser.add_argument("--upload", action="store_true", help="レンダリング後に YouTube へアップロード")
    parser.add_argument(
        "--publish-at",
        help=(
            "YouTube の予約投稿時刻 (RFC3339 例: 2025-10-01T12:30:00+09:00 または 'YYYY-MM-DD HH:MM' ローカル時間)"
        ),
    )
    parser.add_argument(
        "--youtube-channel",
        help="config.yaml の youtube.channel_profiles で定義されたチャンネルを選択",
    )
    parser.add_argument("--youtube-title", help="YouTube タイトルを手動指定")
    parser.add_argument("--youtube-description", help="YouTube 説明文を手動指定")
    parser.add_argument("--youtube-tags", help="カンマ区切りでハッシュタグ/タグを指定")
    parser.add_argument(
        "--skip-thumbnail",
        action="store_true",
        help="サムネイル生成をスキップ",
    )
    parser.add_argument(
        "--thumbnail-output",
        help="サムネイルPNGの出力パス。未指定なら run_dir/thumbnail.png",
    )
    parser.add_argument(
        "--thumbnail-image1",
        help="サムネイル左側用の画像パス（手動指定）",
    )
    parser.add_argument(
        "--thumbnail-image2",
        help="サムネイル右側用の画像パス（手動指定）",
    )
    parser.add_argument(
        "--random-thumbnail",
        action="store_true",
        help="サムネイル画像をランダムに選択（自動化パイプライン用）",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(args.config, project_root=Path.cwd())
    _apply_shikoku_metan_voice(config)

    if args.upload or args.youtube_channel:
        try:
            apply_youtube_channel_profile(config=config, requested_channel=args.youtube_channel)
        except ValueError as exc:
            parser.error(str(exc))

    run_id = datetime.utcnow().strftime("shashin_%Y%m%d_%H%M%S")
    base_dir = Path(".")
    paths = ModePaths.build(base_dir, run_id)
    logger = configure_logging(config.logging_level, paths.log_path)
    logger.info("Shashin mode start: run_id=%s", run_id)

    layout = LayoutConfig(
        width=args.width,
        height=args.height,
        fps=args.fps,
        subtitle_font_path=_resolve_subtitle_font(args.subtitle_font),
        subtitle_font_size=args.subtitle_size,
    )
    timing = TimingConfig(
        min_chunk_duration=args.min_chunk_duration,
        padding_seconds=args.chunk_padding,
    )
    renderer_settings = load_renderer_settings()
    if renderer_settings.config_path and renderer_settings.config_path.exists():
        logger.info(
            "Renderer override loaded: %s (name=%s)",
            renderer_settings.config_path,
            renderer_settings.name,
        )

    fallback_image = Path(args.fallback_image).expanduser() if args.fallback_image else None

    # BGM設定を読み取り
    try:
        bgm_directory, bgm_selected = _apply_background_music(config=config)
        logger.info("Background music selected: %s (directory: %s)", bgm_selected, bgm_directory)
    except ValueError as exc:
        logger.warning("BGM設定の読み取りに失敗しました: %s。デフォルト値を使用します。", exc)
        bgm_directory = "background_music"
        bgm_selected = "Countrysky.mp3"

    pipeline = ShashinPipeline(
        layout=layout,
        timing=timing,
        paths=paths,
        image_provider=args.image_source,
        image_query_prefix=args.image_prefix,
        fallback_image=fallback_image,
        voicevox_config=config.raw,
        renderer_settings=renderer_settings,
        bgm_directory=bgm_directory,
        bgm_selected=bgm_selected,
        auto_mode=args.random_thumbnail,  # --random-thumbnailが指定されている場合は自動モード
    )

    script_path = Path(args.script).expanduser()
    script_doc = load_script(script_path, wrap_chars=args.wrap)
    background_path = Path(args.background).expanduser()
    if not background_path.exists():
        parser.error(
            f"背景ファイルが見つかりません: {background_path}\n"
            f"デフォルトを使う場合は {DEFAULT_BACKGROUND} に GIF/MP4 を置くか、--background でパスを指定してください。"
        )

    output_path = Path(args.output).expanduser() if args.output else None
    result = pipeline.run(
        script_path=script_path,
        background_path=background_path,
        wrap_chars=args.wrap,
        output_path=output_path,
        script_doc=script_doc,
    )
    thumbnail_candidates: list[Path] = (
        result.shared_image_paths if result.shared_image_paths else result.chunk_image_paths
    )

    thumbnail_path: Optional[Path] = None
    if not args.skip_thumbnail:
        # mains(旧s2) -> 黄色ボックス、subs(旧s1) -> 白バナー
        yellow_text = script_doc.thumbnail_banner
        banner_text = script_doc.thumbnail_title
        if not yellow_text or not banner_text:
            logger.info("サムネイル生成をスキップ: mains/subs テキストが不足しています")
        elif not thumbnail_candidates:
            logger.warning("サムネイル生成をスキップ: 画像候補がありません")
        else:
            thumbnail_output = (
                Path(args.thumbnail_output).expanduser()
                if args.thumbnail_output
                else result.run_dir / "thumbnail.png"
            )
            
            # 画像選択: 手動指定があればそれを使用、ランダム選択フラグがあればランダム選択、なければインタラクティブ選択
            if args.thumbnail_image1 and args.thumbnail_image2:
                selected_images = [
                    Path(args.thumbnail_image1).expanduser(),
                    Path(args.thumbnail_image2).expanduser(),
                ]
                logger.info("手動指定された画像を使用: %s, %s", selected_images[0], selected_images[1])
            elif args.random_thumbnail and len(thumbnail_candidates) >= 2:
                # ランダム選択（自動化パイプライン用）
                selected_images = random.sample(thumbnail_candidates, min(2, len(thumbnail_candidates)))
                if len(selected_images) == 1:
                    selected_images.append(selected_images[0])
                logger.info("ランダム選択された画像を使用: %s, %s", selected_images[0].name, selected_images[1].name)
            elif len(thumbnail_candidates) >= 2:
                # デフォルトでインタラクティブ選択
                selected_left, selected_right = _interactive_select_thumbnail_images(thumbnail_candidates)
                selected_images = [selected_left, selected_right]
            else:
                # 候補が2枚未満の場合は自動選択
                selected_images = thumbnail_candidates[:2]
                if len(selected_images) == 1:
                    selected_images.append(selected_images[0])
                logger.info("画像候補が少ないため自動選択しました")
            
            generated = generate_thumbnail(
                title_text=yellow_text,
                banner_text=banner_text,
                image_candidates=selected_images,
                output_path=thumbnail_output,
            )
            if generated:
                thumbnail_path = generated
                result.thumbnail_path = generated
                logger.info("サムネイルを生成しました: %s", generated)
            else:
                logger.warning("サムネイル生成に失敗しました")

    youtube_video_id: Optional[str] = None
    if args.upload:
        resolved_publish_at = resolve_publish_at_string(args.publish_at, config)
        metadata = build_youtube_metadata(
            script_doc=script_doc,
            config=config,
            total_duration=result.total_duration,
            title_override=args.youtube_title,
            description_override=args.youtube_description,
            tags_override=args.youtube_tags,
        )

        uploader = YouTubeUploader(config=config.raw, credentials_dir=config.credentials_dir)
        if not uploader.authenticate():
            logger.error("YouTube 認証に失敗したためアップロードをスキップします。")
        else:
            youtube_video_id = uploader.upload(
                video_path=result.video_path,
                title=metadata.title,
                description=metadata.description,
                tags=metadata.tags,
                publish_at=resolved_publish_at,
                thumbnail_path=thumbnail_path,
            )
            if youtube_video_id:
                logger.info("YouTube へのアップロードが完了しました: https://www.youtube.com/watch?v=%s", youtube_video_id)
            else:
                logger.error("YouTube へのアップロードに失敗しました。")

    logger.info("Shashin mode completed: %s", result.video_path)
    logger.info("字幕: %s", result.subtitle_path)
    logger.info("plan: %s", result.plan_path)
    print(f"完成: {result.video_path}")
    print(f"字幕: {result.subtitle_path}")
    print(f"plan: {result.plan_path}")
    if thumbnail_path:
        print(f"thumbnail: {thumbnail_path}")
    if youtube_video_id:
        print(f"YouTube: https://www.youtube.com/watch?v={youtube_video_id}")


if __name__ == "__main__":
    main()
