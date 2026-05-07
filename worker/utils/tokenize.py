"""Shared tokenization helpers for retrieval, planner scoring, and search."""

from __future__ import annotations

import re
import unicodedata

_WORD_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_CJK_RUN_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+"
)
_SEPARATOR_RE = re.compile(r"[/_\-.]")


def tokenize_text(text: str, *, min_ascii_len: int = 3) -> set[str]:
    """Return lowercased ASCII tokens plus CJK runs and n-grams.

    ASCII identifiers are split on path separators, punctuation, snake case,
    and camel case. ASCII tokens shorter than ``min_ascii_len`` are ignored
    except for CJK n-grams, where two-character compounds are useful search
    signals. Non-CJK Unicode words are preserved as whole tokens.
    """

    if not text:
        return set()

    text = unicodedata.normalize("NFKC", text)
    normalized = _CAMEL_SPLIT_RE.sub(" ", text)
    normalized = _SEPARATOR_RE.sub(" ", normalized)
    word_text = _CJK_RUN_RE.sub(" ", normalized)
    tokens: set[str] = set()
    for token in _WORD_TOKEN_RE.findall(word_text):
        normalized_token = token.lower()
        if token.isascii() and len(normalized_token) < min_ascii_len:
            continue
        tokens.add(normalized_token)

    for run in _CJK_RUN_RE.findall(text):
        tokens.add(run)
        max_ngram = min(3, len(run))
        for size in range(2, max_ngram + 1):
            for idx in range(len(run) - size + 1):
                tokens.add(run[idx : idx + size])

    return tokens
