"""Interpretive context assembly for fast reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from worker.fast_report.search import _tokenize as _shared_tokenize

_AUTO_ATTACH_TOKEN_CAP = 8_000
_README_BODY_CAP = 800
_README_TOP_K = 5
_README_TOTAL_TOKEN_CAP = 10_000


@dataclass(slots=True)
class InterpretiveBundle:
    entries: list[dict[str, Any]] = field(default_factory=list)


def build_interpretive_bundle(
    *,
    selected_entities: list[dict[str, Any]],
    index: dict[str, Any],
    intent_tokens: set[str],
) -> InterpretiveBundle:
    entries: list[dict[str, Any]] = []
    entries.extend(_auto_attached_entries(selected_entities))
    entries.extend(_ranked_readme_entries(index, intent_tokens))
    return InterpretiveBundle(entries=entries)


def _auto_attached_entries(
    selected_entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[tuple[float, int, dict[str, Any]]] = []
    seen_module_files: set[str] = set()
    ordinal = 0
    for entity in selected_entities:
        score = float(entity.get("score") or 0.0)
        file_path = str(entity.get("file") or "")
        name = entity.get("name")
        for source, field_name in (
            ("entity_docstring", "docstring"),
            ("entity_leading_comment", "leading_comment"),
        ):
            text = entity.get(field_name)
            if text:
                candidates.append(
                    (
                        score,
                        ordinal,
                        {
                            "source": source,
                            "file": file_path,
                            "name": name,
                            "text": str(text),
                        },
                    )
                )
                ordinal += 1
        module_docstring = entity.get("module_docstring")
        if module_docstring and file_path not in seen_module_files:
            seen_module_files.add(file_path)
            candidates.append(
                (
                    score,
                    ordinal,
                    {
                        "source": "module_docstring",
                        "file": file_path,
                        "name": None,
                        "text": str(module_docstring),
                    },
                )
            )
            ordinal += 1

    candidates.sort(key=lambda item: (-item[0], item[1]))
    entries: list[dict[str, Any]] = []
    total_tokens = 0
    for _score, _ordinal, entry in candidates:
        cost = _approx_tokens(entry["text"])
        if total_tokens + cost > _AUTO_ATTACH_TOKEN_CAP:
            continue
        entries.append(entry)
        total_tokens += cost
    return entries


def _ranked_readme_entries(
    index: dict[str, Any], intent_tokens: set[str]
) -> list[dict[str, Any]]:
    normalized_intent_tokens = {token.lower() for token in intent_tokens if token}
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for position, section in enumerate(index.get("readme_sections") or []):
        heading = str(section.get("heading") or "")
        body = str(section.get("body") or "")
        overlap = len(normalized_intent_tokens & _tokenize(f"{heading} {body}"))
        if overlap:
            ranked.append((overlap, position, section))
    ranked.sort(key=lambda item: (-item[0], item[1]))

    entries: list[dict[str, Any]] = []
    total_tokens = 0
    for _overlap, _position, section in ranked[:_README_TOP_K]:
        heading = str(section.get("heading") or "")
        body = str(section.get("body") or "")[:_README_BODY_CAP]
        cost = _approx_tokens(heading) + _approx_tokens(body)
        if total_tokens + cost > _README_TOTAL_TOKEN_CAP:
            break
        entries.append(
            {
                "source": "readme_section",
                "heading": heading,
                "text": body,
            }
        )
        total_tokens += cost
    return entries


def _approx_tokens(text: str) -> int:
    return max(0, len(text or "") // 4)


def _tokenize(text: str) -> set[str]:
    return _shared_tokenize(text)
