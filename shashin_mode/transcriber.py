"""Audio transcription using Whisper for shashin_mode.

Supports both faster-whisper (recommended) and openai-whisper.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass

from logging_utils import get_logger

logger = get_logger(__name__)


@dataclass
class TranscriptSegment:
    """文字起こしのセグメント"""
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    """文字起こし結果"""
    text: str
    segments: List[TranscriptSegment]
    language: str
    duration: float


class Transcriber:
    """Whisperを使った音声文字起こし
    
    faster-whisperを優先的に使用し、インストールされていなければopenai-whisperを使用
    """
    
    SUPPORTED_MODELS = ["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"]
    
    def __init__(
        self,
        model_size: str = "medium",
        language: str = "ja",
        device: Optional[str] = None,  # "cuda" or "cpu"
        compute_type: str = "float16",  # "float16", "int8", "float32"
    ) -> None:
        if model_size not in self.SUPPORTED_MODELS:
            logger.warning("Unknown model size '%s', using 'medium'", model_size)
            model_size = "medium"
        
        self.model_size = model_size
        self.language = language
        self.device = device or self._detect_device()
        self.compute_type = compute_type if self.device == "cuda" else "float32"
        self._model = None
        self._backend = None
    
    def _detect_device(self) -> str:
        """利用可能なデバイスを検出"""
        try:
            import torch
            if torch.cuda.is_available():
                logger.info("CUDA GPU detected")
                return "cuda"
        except ImportError:
            pass
        
        logger.info("Using CPU for transcription")
        return "cpu"
    
    def _load_model(self) -> None:
        """モデルをロード"""
        if self._model is not None:
            return
        
        # faster-whisperを優先
        try:
            from faster_whisper import WhisperModel
            
            logger.info("Loading faster-whisper model: %s (device=%s)", self.model_size, self.device)
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
            self._backend = "faster-whisper"
            logger.info("faster-whisper model loaded successfully")
            return
        except ImportError:
            logger.info("faster-whisper not installed, trying openai-whisper...")
        
        # openai-whisperにフォールバック
        try:
            import whisper
            
            logger.info("Loading openai-whisper model: %s", self.model_size)
            self._model = whisper.load_model(self.model_size, device=self.device)
            self._backend = "openai-whisper"
            logger.info("openai-whisper model loaded successfully")
            return
        except ImportError:
            pass
        
        raise RuntimeError(
            "Neither faster-whisper nor openai-whisper is installed. "
            "Install with: pip install faster-whisper (recommended) or pip install openai-whisper"
        )
    
    def transcribe(
        self,
        audio_path: Path,
        *,
        max_duration: Optional[float] = None,
    ) -> TranscriptResult:
        """音声ファイルを文字起こし
        
        Args:
            audio_path: 音声ファイルのパス
            max_duration: 最大処理時間（秒）。Noneの場合は全体を処理
            
        Returns:
            TranscriptResult: 文字起こし結果
        """
        self._load_model()
        
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        logger.info("文字起こし開始: %s (backend=%s)", audio_path.name, self._backend)
        
        if self._backend == "faster-whisper":
            return self._transcribe_faster_whisper(audio_path, max_duration)
        else:
            return self._transcribe_openai_whisper(audio_path, max_duration)
    
    def _transcribe_faster_whisper(
        self,
        audio_path: Path,
        max_duration: Optional[float] = None,
    ) -> TranscriptResult:
        """faster-whisperで文字起こし"""
        segments_iter, info = self._model.transcribe(
            str(audio_path),
            language=self.language,
            beam_size=5,
            vad_filter=True,  # 無音部分をスキップ
        )
        
        segments: List[TranscriptSegment] = []
        texts: List[str] = []
        
        for segment in segments_iter:
            if max_duration and segment.start > max_duration:
                break
            
            segments.append(TranscriptSegment(
                start=segment.start,
                end=segment.end,
                text=segment.text.strip(),
            ))
            texts.append(segment.text.strip())
        
        full_text = " ".join(texts)
        duration = segments[-1].end if segments else 0.0
        
        logger.info("文字起こし完了: %d文字, %.1f秒", len(full_text), duration)
        
        return TranscriptResult(
            text=full_text,
            segments=segments,
            language=info.language,
            duration=duration,
        )
    
    def _transcribe_openai_whisper(
        self,
        audio_path: Path,
        max_duration: Optional[float] = None,
    ) -> TranscriptResult:
        """openai-whisperで文字起こし"""
        import whisper
        
        result = self._model.transcribe(
            str(audio_path),
            language=self.language,
            verbose=False,
        )
        
        segments: List[TranscriptSegment] = []
        texts: List[str] = []
        
        for segment in result.get("segments", []):
            if max_duration and segment["start"] > max_duration:
                break
            
            segments.append(TranscriptSegment(
                start=segment["start"],
                end=segment["end"],
                text=segment["text"].strip(),
            ))
            texts.append(segment["text"].strip())
        
        full_text = " ".join(texts)
        duration = segments[-1].end if segments else 0.0
        
        logger.info("文字起こし完了: %d文字, %.1f秒", len(full_text), duration)
        
        return TranscriptResult(
            text=full_text,
            segments=segments,
            language=result.get("language", self.language),
            duration=duration,
        )
    
    def transcribe_to_text(
        self,
        audio_path: Path,
        *,
        max_duration: Optional[float] = None,
    ) -> str:
        """音声ファイルをテキストに変換（簡易版）"""
        result = self.transcribe(audio_path, max_duration=max_duration)
        return result.text


def check_whisper_available() -> tuple[bool, str]:
    """Whisperが利用可能かチェック
    
    Returns:
        tuple[bool, str]: (利用可能かどうか, バックエンド名またはエラーメッセージ)
    """
    try:
        from faster_whisper import WhisperModel
        return True, "faster-whisper"
    except ImportError:
        pass
    
    try:
        import whisper
        return True, "openai-whisper"
    except ImportError:
        pass
    
    return False, "Whisper is not installed"

