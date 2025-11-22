"""CLI entry for shashin_mode pipeline."""
from __future__ import annotations

import argparse
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


DEFAULT_BACKGROUND = Path("shashin_mode/assets/sakura.mp4")


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
    parser.add_argument("--subtitle-size", type=int, default=40)
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
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(args.config, project_root=Path.cwd())

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
        subtitle_font_path=args.subtitle_font,
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

    pipeline = ShashinPipeline(
        layout=layout,
        timing=timing,
        paths=paths,
        image_provider=args.image_source,
        image_query_prefix=args.image_prefix,
        fallback_image=fallback_image,
        voicevox_config=config.raw,
        renderer_settings=renderer_settings,
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

    thumbnail_path: Optional[Path] = None
    if not args.skip_thumbnail:
        # mains(旧s2) -> 黄色ボックス、subs(旧s1) -> 白バナー
        yellow_text = script_doc.thumbnail_banner
        banner_text = script_doc.thumbnail_title
        if not yellow_text or not banner_text:
            logger.info("サムネイル生成をスキップ: mains/subs テキストが不足しています")
        elif not result.chunk_image_paths:
            logger.warning("サムネイル生成をスキップ: 画像候補がありません")
        else:
            thumbnail_output = (
                Path(args.thumbnail_output).expanduser()
                if args.thumbnail_output
                else result.run_dir / "thumbnail.png"
            )
            generated = generate_thumbnail(
                title_text=yellow_text,
                banner_text=banner_text,
                image_candidates=result.chunk_image_paths,
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
                thumbnail_path=None,
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
