from __future__ import annotations

import re

# 句読点は維持し、引用符や括弧類のみ除去する
_REMOVE_CHARS = (
    "「」『』【】《》〈〉"  # 和文引用符/かぎ括弧
    "（）()［］[]"        # 丸括弧/角括弧（全角/半角）
    "\"＂“”'＇‘’"        # ダブル/シングルクォート各種
)

_REMOVE_RE = re.compile("[" + re.escape(_REMOVE_CHARS) + "]")
# 連続スペースやタブは1個に（改行は温存）
_SPACE_COMPRESS_RE = re.compile(r"[\t ]{2,}")


def sanitize_for_voicevox(text: str) -> str:
    """Remove quote-like symbols that cause awkward pauses in VOICEVOX.

    - Keeps punctuation such as 、。！？…
    - Preserves newlines; compresses only consecutive spaces/tabs.
    """
    if not text:
        return text
    # Drop inline markers like '%%START' / '%%END' entirely (line-based)
    marker_lines = []
    for ln in text.splitlines(keepends=False):
        if ln.strip().startswith("%%"):
            # Skip all lines that start with '%%' for safety
            marker_lines.append("")
        else:
            marker_lines.append(ln)
    text = "\n".join(marker_lines)

    stripped = _REMOVE_RE.sub("", text)
    # 圧縮は改行を壊さないようにスペース/タブのみ対象
    stripped = _SPACE_COMPRESS_RE.sub(" ", stripped)
    # 行頭末の余計なスペースだけトリム（改行は保持）
    lines = stripped.splitlines(keepends=True)
    lines = [re.sub(r"^[ \t]+|[ \t]+$", "", ln) for ln in lines]
    return "".join(lines)
