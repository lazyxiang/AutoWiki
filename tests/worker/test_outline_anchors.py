"""Tests for the outline-anchor helpers (Layer C1)."""

from __future__ import annotations

from pathlib import Path

from worker.pipeline.planner.outline_anchors import (
    build_directory_tree,
    extract_package_docstrings,
    extract_readme_sections,
    format_anchors_for_prompt,
)


def test_build_directory_tree_counts_and_depth():
    files = [
        "worker/pipeline/wiki_planner.py",
        "worker/pipeline/page_generator.py",
        "worker/pipeline/ast_analysis.py",
        "worker/jobs.py",
        "worker/llm/base.py",
        "worker/llm/anthropic_provider.py",
        "api/routers/repos.py",
        "api/routers/wiki.py",
        "api/main.py",
        "web/app/page.tsx",
        "README.md",
    ]
    tree = build_directory_tree(files, max_depth=3)
    # Top-level dirs and their subtree file counts
    assert "worker/ (6)" in tree
    assert "api/ (3)" in tree
    assert "web/ (1)" in tree
    # Root-level files listed under a synthetic "(root)" bucket
    assert "(root) (1)" in tree
    # Depth-2 sub-directories appear with counts
    assert "pipeline/ (3)" in tree
    assert "routers/ (2)" in tree
    # Depth-4 files are NOT expanded individually (max_depth=3)
    assert "wiki_planner.py" not in tree


def test_build_directory_tree_is_stable_ordered():
    """Output order is deterministic (alphabetical at each level)."""
    files = ["z/a.py", "a/z.py", "a/a.py", "m/b.py"]
    tree = build_directory_tree(files, max_depth=3)
    a_idx = tree.index("a/")
    m_idx = tree.index("m/")
    z_idx = tree.index("z/")
    assert a_idx < m_idx < z_idx


async def test_extract_package_docstrings_python_init(tmp_path: Path):
    pkg = tmp_path / "worker" / "pipeline"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        '"""Pipeline stages for the wiki generator.\n\n'
        "Exports the public entry points used by worker/jobs.py.\n"
        '"""\n'
        "from .wiki_planner import generate_wiki_plan\n"
    )
    # Non-package file that should NOT be picked up
    (tmp_path / "worker" / "jobs.py").write_text("'just a module'\nx = 1\n")

    result = await extract_package_docstrings(
        clone_root=tmp_path,
        rel_paths=["worker/pipeline/__init__.py", "worker/jobs.py"],
        max_entries=10,
    )
    # Only the __init__.py is surfaced
    assert len(result) == 1
    assert result[0].package == "worker/pipeline"
    assert "Pipeline stages" in result[0].docstring
    # Docstring is trimmed (no triple quotes)
    assert '"""' not in result[0].docstring


async def test_extract_package_docstrings_rust_and_ts(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.rs").write_text(
        "//! Graph-index helpers.\n"
        "//! Used by the ingest pipeline.\n"
        "pub fn build() {}\n"
    )
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "index.ts").write_text(
        "/**\n * UI kit barrel entrypoint.\n */\nexport { Button } from './button';\n"
    )
    result = await extract_package_docstrings(
        clone_root=tmp_path,
        rel_paths=["src/mod.rs", "ui/index.ts"],
        max_entries=10,
    )
    packages = {r.package: r.docstring for r in result}
    assert "Graph-index helpers" in packages["src"]
    assert "UI kit barrel" in packages["ui"]


async def test_extract_package_docstrings_caps_results(tmp_path: Path):
    for i in range(30):
        pkg = tmp_path / f"pkg{i:02d}"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(f'"""Package {i}."""\n')
    rels = [f"pkg{i:02d}/__init__.py" for i in range(30)]
    result = await extract_package_docstrings(
        clone_root=tmp_path, rel_paths=rels, max_entries=5
    )
    assert len(result) == 5


def test_extract_readme_sections_returns_h2_and_h3():
    readme = (
        "# My Project\n\n"
        "Intro paragraph.\n\n"
        "## Architecture\n\n"
        "Some text.\n\n"
        "### Components\n\n"
        "More text.\n\n"
        "### Data Flow\n\n"
        "## Installation\n\n"
        "```\n"
        "## not a heading (inside code fence)\n"
        "```\n"
        "## Development\n"
    )
    sections = extract_readme_sections(readme)
    assert sections == [
        ("##", "Architecture"),
        ("###", "Components"),
        ("###", "Data Flow"),
        ("##", "Installation"),
        ("##", "Development"),
    ]


def test_extract_readme_sections_none_returns_empty():
    assert extract_readme_sections(None) == []
    assert extract_readme_sections("") == []


def test_format_anchors_for_prompt_omits_empty_sections():
    out = format_anchors_for_prompt(
        directory_tree="",
        package_docstrings=[],
        readme_sections=[],
    )
    assert out == ""


def test_format_anchors_for_prompt_includes_each_section():
    from worker.pipeline.planner.outline_anchors import PackageDoc

    out = format_anchors_for_prompt(
        directory_tree="worker/ (3)\n  pipeline/ (2)",
        package_docstrings=[
            PackageDoc(package="worker/pipeline", docstring="Pipeline stages.")
        ],
        readme_sections=[("##", "Architecture"), ("###", "Components")],
    )
    assert "## Directory layout" in out
    assert "worker/ (3)" in out
    assert "## Package docstrings" in out
    assert "worker/pipeline: Pipeline stages." in out
    assert "## README sections" in out
    assert "## Architecture" in out
    assert "### Components" in out
