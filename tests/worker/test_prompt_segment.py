from worker.llm.prompt_segment import PromptSegment, normalize_prompt, segments_to_text


def test_prompt_segment_defaults():
    seg = PromptSegment(text="hello")
    assert seg.text == "hello"
    assert seg.cacheable is False


def test_prompt_segment_cacheable():
    seg = PromptSegment(text="context", cacheable=True)
    assert seg.cacheable is True


def test_normalize_prompt_from_string():
    result = normalize_prompt("plain text")
    assert result == [PromptSegment(text="plain text", cacheable=False)]


def test_normalize_prompt_from_list():
    segments = [
        PromptSegment(text="cached", cacheable=True),
        PromptSegment(text="variable"),
    ]
    result = normalize_prompt(segments)
    assert result is segments


def test_normalize_prompt_empty_string():
    result = normalize_prompt("")
    assert result == [PromptSegment(text="", cacheable=False)]


def test_segments_to_text():
    segments = [
        PromptSegment(text="System: ", cacheable=True),
        PromptSegment(text="Hello world"),
    ]
    assert segments_to_text(segments) == "System: Hello world"


def test_segments_to_text_empty():
    assert segments_to_text([]) == ""
