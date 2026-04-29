from pathlib import Path

import pytest

from worker.fast_report_slices import SliceResult, extract_source_slice


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_happy_path_returns_real_source_with_five_line_context(tmp_path):
    _write(tmp_path, "m.py", "\n".join(f"line{i}" for i in range(1, 13)))

    result = extract_source_slice(
        clone_root=tmp_path,
        rel_path="m.py",
        anchor_start=6,
        anchor_end=8,
        line_cap=10,
    )

    assert result == SliceResult(
        snippet_start=6,
        snippet_end=8,
        full_start=1,
        full_end=12,
        code="line6\nline7\nline8",
        truncated_lines=0,
    )


def test_missing_file_returns_none(tmp_path):
    assert (
        extract_source_slice(
            clone_root=tmp_path,
            rel_path="missing.py",
            anchor_start=1,
            anchor_end=3,
            line_cap=10,
        )
        is None
    )


def test_utf8_errors_are_replaced(tmp_path):
    path = tmp_path / "bad.py"
    path.write_bytes(b"ok\n\xff\n")

    result = extract_source_slice(
        clone_root=tmp_path,
        rel_path="bad.py",
        anchor_start=1,
        anchor_end=2,
        line_cap=10,
    )

    assert result is not None
    assert result.code == "ok\n\ufffd"


def test_full_end_clamped_to_file_length(tmp_path):
    _write(tmp_path, "m.py", "a\nb\nc\n")

    result = extract_source_slice(
        clone_root=tmp_path,
        rel_path="m.py",
        anchor_start=2,
        anchor_end=3,
        line_cap=10,
    )

    assert result.full_end == 3


def test_over_cap_appends_truncation_marker_python(tmp_path):
    body = "\n".join(f"line{i}" for i in range(1, 21))
    _write(tmp_path, "m.py", body)

    result = extract_source_slice(
        clone_root=tmp_path,
        rel_path="m.py",
        anchor_start=1,
        anchor_end=20,
        line_cap=5,
    )

    assert result.truncated_lines == 15
    assert result.code.endswith("# ... 15 more lines truncated")


@pytest.mark.parametrize(
    ("ext", "marker_prefix"),
    [
        ("py", "#"),
        ("rb", "#"),
        ("sh", "#"),
        ("bash", "#"),
        ("zsh", "#"),
        ("js", "//"),
        ("jsx", "//"),
        ("ts", "//"),
        ("tsx", "//"),
        ("go", "//"),
        ("rs", "//"),
        ("java", "//"),
        ("c", "//"),
        ("h", "//"),
        ("cpp", "//"),
        ("hpp", "//"),
        ("cc", "//"),
        ("cs", "//"),
    ],
)
def test_truncation_marker_per_language(tmp_path, ext, marker_prefix):
    body = "\n".join(f"line{i}" for i in range(1, 11))
    _write(tmp_path, f"m.{ext}", body)

    result = extract_source_slice(
        clone_root=tmp_path,
        rel_path=f"m.{ext}",
        anchor_start=1,
        anchor_end=10,
        line_cap=3,
    )

    assert result.code.endswith(f"{marker_prefix} ... 7 more lines truncated")
