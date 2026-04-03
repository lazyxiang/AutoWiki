"""Language instruction helpers for multi-language wiki generation.

Maps language codes to prompt suffixes that instruct the LLM to generate
content in the target language.  English (``"en"``) returns an empty string
so no extra tokens are consumed for the default case.
"""

from __future__ import annotations

_CONTENT_INSTRUCTIONS: dict[str, str] = {
    "en": "",
    "zh": (
        "\n\nIMPORTANT: Write ALL wiki content in Chinese (简体中文). "
        "Use Chinese for all section headers, descriptions, explanations, "
        "and narrative text. "
        "Keep code identifiers, function names, class names, file paths, "
        "URLs, and established technical terms (e.g. API, HTTP, REST, FAISS, "
        "Docker, JSON) in their original English form."
    ),
}

_PLANNER_INSTRUCTIONS: dict[str, str] = {
    "en": "",
    "zh": (
        "\n\nIMPORTANT: Write page titles and purpose descriptions in Chinese "
        "(简体中文). The JSON keys (title, purpose, parent, files) must remain "
        "in English exactly as shown in the schema. File paths in the 'files' "
        "array must be exact original paths — never translate them."
    ),
}


def get_language_instruction(wiki_language: str) -> str:
    """Return the LLM prompt suffix for content generation in *wiki_language*.

    Returns an empty string for English (the default) so that no extra tokens
    are consumed when no language override is needed.

    Args:
        wiki_language: ISO-639-1 language code (e.g. ``"en"``, ``"zh"``).

    Returns:
        A prompt suffix string to append to the LLM system prompt, or ``""``
        if no instruction is needed.
    """
    return _CONTENT_INSTRUCTIONS.get(wiki_language, "")


def get_planner_language_instruction(wiki_language: str) -> str:
    """Return the LLM prompt suffix for wiki *planning* in *wiki_language*.

    Similar to :func:`get_language_instruction` but tailored for the wiki
    planner, which must still output valid JSON with English keys while
    writing page titles and descriptions in the target language.

    Args:
        wiki_language: ISO-639-1 language code (e.g. ``"en"``, ``"zh"``).

    Returns:
        A prompt suffix string to append to the planner system prompt.
    """
    return _PLANNER_INSTRUCTIONS.get(wiki_language, "")
