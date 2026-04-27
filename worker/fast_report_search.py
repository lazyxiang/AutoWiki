"""Helpers for fast-report search planning."""

from __future__ import annotations

import re

_CJK_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]"
)


def detect_question_language(question: str) -> str:
    """Detect the question language for fast reports.

    The detector maps CJK-script questions onto the Chinese prompt/rendering
    path because fast reports currently only have ``en`` and ``zh`` language
    instructions.
    """

    if _CJK_RE.search(question):
        return "zh"
    return "en"


def normalize_fast_report_language(language: str | None) -> str:
    """Normalize planner or detector language labels to supported values."""

    value = (language or "").strip().lower().replace("_", "-")
    if not value:
        return "en"
    if (
        value.startswith("zh")
        or value.startswith("ja")
        or value.startswith("ko")
        or "chinese" in value
        or "japanese" in value
        or "korean" in value
    ):
        return "zh"
    if value.startswith("en") or "english" in value:
        return "en"
    return "en"
