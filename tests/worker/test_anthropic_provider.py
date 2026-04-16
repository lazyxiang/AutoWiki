from worker.llm.anthropic_provider import _segments_to_anthropic_content
from worker.llm.prompt_segment import PromptSegment


def test_segments_to_anthropic_content_marks_last_cacheable_block():
    """Verify cache_control marker on last segment of cacheable run."""
    segs = [
        PromptSegment(text="stage system", cacheable=False),
        PromptSegment(text="big context", cacheable=True),
    ]
    content = _segments_to_anthropic_content(segs)
    assert isinstance(content, list)
    # Last block should carry cache_control
    assert content[-1].get("cache_control") == {"type": "ephemeral"}


def test_segments_to_anthropic_content_plain_string_when_no_cacheable():
    """Plain string returned when no segments are cacheable."""
    segs = [
        PromptSegment(text="part1", cacheable=False),
        PromptSegment(text="part2", cacheable=False),
    ]
    content = _segments_to_anthropic_content(segs)
    assert isinstance(content, str)
    assert content == "part1part2"


def test_segments_to_anthropic_content_multiple_cacheable_runs():
    """Multiple contiguous cacheable runs each get cache_control."""
    segs = [
        PromptSegment(text="non-cache1", cacheable=False),
        PromptSegment(text="cache1", cacheable=True),
        PromptSegment(text="cache2", cacheable=True),
        PromptSegment(text="non-cache2", cacheable=False),
        PromptSegment(text="cache3", cacheable=True),
    ]
    content = _segments_to_anthropic_content(segs)
    assert isinstance(content, list)
    # Find blocks with cache_control
    cached_blocks = [b for b in content if b.get("cache_control")]
    assert len(cached_blocks) == 2  # Two separate cacheable runs
    # All cached blocks should have type="ephemeral"
    for block in cached_blocks:
        assert block["cache_control"] == {"type": "ephemeral"}


def test_segments_to_anthropic_content_respects_max_breakpoints():
    """Respects _MAX_CACHE_BREAKPOINTS limit."""
    # Create more than 4 cacheable runs
    segs = []
    for i in range(6):
        segs.append(PromptSegment(text=f"cache{i}", cacheable=True))
        if i < 5:
            segs.append(PromptSegment(text=f"non{i}", cacheable=False))

    content = _segments_to_anthropic_content(segs)
    assert isinstance(content, list)
    # Should have at most 4 cache_control markers
    cached_blocks = [b for b in content if b.get("cache_control")]
    assert len(cached_blocks) == 4
