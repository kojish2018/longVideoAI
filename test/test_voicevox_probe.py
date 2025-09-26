#!/usr/bin/env python3
"""Voicevox単体検証スクリプト（本番モジュール利用）。"""
from __future__ import annotations

import argparse
import logging
import sys
import wave
from pathlib import Path
from typing import Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LOAD_CONFIG_ERROR = None
try:  # config_loaderはPyYAML前提なので読み込み失敗を吸収する
    from config_loader import load_config  # type: ignore
except RuntimeError as exc:  # PyYAML未インストールなど
    load_config = None  # type: ignore
    LOAD_CONFIG_ERROR = exc
except ModuleNotFoundError:  # 念のため
    load_config = None  # type: ignore

from voicevox_client import VoicevoxClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VOICEVOX合成の単体検証を行います")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="使用する設定ファイルのパス",
    )
    parser.add_argument(
        "--text",
        default="テスト用の文章です。音声の歪みをチェックしましょう。",
        help="合成するテキスト",
    )
    parser.add_argument(
        "--text-file",
        type=Path,
        help="テキストを読み込むファイルパス（指定時は--textより優先）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/test_voicevox.wav"),
        help="出力先のWAVファイルパス",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存の出力ファイルがある場合に上書きする",
    )
    return parser.parse_args()


def load_text(args: argparse.Namespace) -> str:
    if args.text_file:
        try:
            return args.text_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SystemExit(f"テキストファイルの読み込みに失敗しました: {exc}") from exc
    return args.text.strip()


def simple_voicevox_config(path: Path) -> Dict[str, Any]:
    """PyYAMLなしでvoicevox設定の最小部分を抽出する。"""
    if not path.exists():
        raise SystemExit(f"設定ファイルが見つかりません: {path}")

    voicevox: Dict[str, Any] = {}
    text = path.read_text(encoding="utf-8")
    in_section = False
    base_indent = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        line = raw_line.strip()
        if line.startswith("voicevox:"):
            in_section = True
            base_indent = indent
            continue
        if in_section:
            if indent <= (base_indent or 0):
                break
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            voicevox[key] = _parse_scalar(value)
    if not voicevox:
        raise SystemExit("voicevoxセクションを設定ファイルから取得できませんでした")
    return {"apis": {"voicevox": voicevox}}


def _parse_scalar(text: str) -> Any:  # noqa: ANN401 - 単純パーサ
    if not text:
        return ""
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    if text.startswith("\"") and text.endswith("\""):
        return text.strip("\"")
    if text.startswith("'") and text.endswith("'"):
        return text.strip("'")
    try:
        if "_" not in text:
            return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def load_voicevox_settings(config_path: Path) -> Dict[str, Any]:
    if load_config is not None:
        app_config = load_config(config_path)
        return app_config.raw
    logging.warning("PyYAML未インストールのため簡易パーサでvoicevox設定を読み込みます: %s", LOAD_CONFIG_ERROR)
    return simple_voicevox_config(config_path)


def dump_wav_metadata(path: Path) -> None:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        frames = wav.getnframes()
        duration = frames / float(sample_rate) if sample_rate else 0.0
    logging.info(
        "WAV情報: channels=%s sample_rate=%s duration=%.2fsec frames=%s",
        channels,
        sample_rate,
        duration,
        frames,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    args = parse_args()
    text = load_text(args)
    if not text:
        raise SystemExit("合成対象のテキストが空です")

    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not args.overwrite:
        raise SystemExit("出力ファイルが既に存在します。--overwrite を指定してください")

    config = load_voicevox_settings(args.config)
    client = VoicevoxClient(config)

    logging.info("VOICEVOXへ合成リクエストを送信します")
    wav_path, duration = client.synthesize(text, output_path)
    logging.info("合成完了: %s (%.2f秒)", wav_path, duration)
    dump_wav_metadata(wav_path)


if __name__ == "__main__":
    main()
