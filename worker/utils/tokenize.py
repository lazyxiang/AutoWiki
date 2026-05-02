"""Shared tokenization helpers for retrieval, planner scoring, and search."""

from __future__ import annotations

import re

_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_CJK_RUN_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+"
)
_SEPARATOR_RE = re.compile(r"[/_\-.]")


def tokenize_text(text: str) -> set[str]:
    """Return lowercased ASCII tokens plus CJK runs and n-grams.

    ASCII identifiers are split on path separators, punctuation, snake case,
    and camel case. Tokens shorter than three characters are ignored except for
    CJK n-grams, where two-character compounds are useful search signals.
    """

    if not text:
        return set()

    normalized = _CAMEL_SPLIT_RE.sub(" ", text)
    normalized = _SEPARATOR_RE.sub(" ", normalized)
    tokens = {
        token
        for token in _ASCII_TOKEN_RE.findall(normalized.lower())
        if len(token) >= 3
    }

    for run in _CJK_RUN_RE.findall(text):
        tokens.add(run)
        max_ngram = min(3, len(run))
        for size in range(2, max_ngram + 1):
            for idx in range(len(run) - size + 1):
                tokens.add(run[idx : idx + size])

    return tokens
