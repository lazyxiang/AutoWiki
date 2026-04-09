from pathlib import Path

from worker.pipeline.ast_analysis import (
    SUPPORTED_LANGUAGES,
    FileAnalysis,
    FileInfo,
    analyze_all_files,
    analyze_file,
)

FIXTURE = Path("tests/fixtures/simple-repo")


def test_supported_languages_count():
    # 15 extension entries
    # .py .js .jsx .ts .tsx .java .kt .kts .go .rs .c .h .cpp .cc .cs
    assert len(SUPPORTED_LANGUAGES) == 15


def test_analyze_kotlin_file(tmp_path):
    f = tmp_path / "Main.kt"
    f.write_text("""
        class HelloWorld {
            fun sayHello() {
                println("Hello")
            }
        }
        fun main() {
            println("Top-level")
        }
    """)
    result = analyze_file(f)
    assert result is not None
    names = [e["name"] for e in result["entities"]]
    assert "HelloWorld" in names
    assert "sayHello" in names
    assert "main" in names


def test_analyze_javascript_file(tmp_path):
    f = tmp_path / "index.js"
    f.write_text("""
        class App {
            render() {
                console.log("App");
            }
        }
        function init() {
            return new App();
        }
    """)
    result = analyze_file(f)
    assert result is not None
    names = [e["name"] for e in result["entities"]]
    assert "App" in names
    assert "render" in names
    assert "init" in names


def test_analyze_typescript_file(tmp_path):
    f = tmp_path / "types.ts"
    f.write_text("""
        interface User {
            id: number;
            name: string;
        }
        class UserService {
            getUser(id: number): User {
                return { id, name: "Test" };
            }
        }
    """)
    result = analyze_file(f)
    assert result is not None
    names = [e["name"] for e in result["entities"]]
    assert "User" in names
    assert "UserService" in names
    assert "getUser" in names


def test_analyze_python_file():
    result = analyze_file(FIXTURE / "models.py")
    assert result is not None
    names = [e["name"] for e in result["entities"]]
    assert "User" in names
    assert "Post" in names
    for entity in result["entities"]:
        assert "type" in entity  # "class" or "function"
        assert "name" in entity


def test_unsupported_language_returns_none():
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(suffix=".rb", mode="w", delete=False) as f:
        f.write("puts 'hello'")
        fname = f.name
    result = analyze_file(Path(fname))
    assert result is None  # Ruby not supported in Phase 1 AST


# ── New tests for enhanced features ──────────────────────────────────────────


def test_extract_python_docstring(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text('class Foo:\n    """A foo class."""\n    pass\n')
    result = analyze_file(f)
    assert result is not None
    foo = [e for e in result["entities"] if e["name"] == "Foo"][0]
    assert foo.get("docstring") is not None
    assert "foo class" in foo["docstring"].lower()


def test_extract_python_function_signature(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("def greet(name: str) -> str:\n    return f'Hello {name}'\n")
    result = analyze_file(f)
    assert result is not None
    greet = [e for e in result["entities"] if e["name"] == "greet"][0]
    assert greet.get("signature") is not None
    assert "name" in greet["signature"]


def test_analyze_all_files_basic(tmp_path):
    f1 = tmp_path / "main.py"
    f1.write_text("class App:\n    pass\n\ndef run():\n    pass\n")
    f2 = tmp_path / "utils.py"
    f2.write_text("def helper():\n    pass\n\ndef greet():\n    pass\n")

    result = analyze_all_files(tmp_path, [f1, f2])

    assert isinstance(result, FileAnalysis)
    assert "main.py" in result.files
    assert "utils.py" in result.files
    assert isinstance(result.files["main.py"], FileInfo)
    assert isinstance(result.files["utils.py"], FileInfo)
    # main.py has 1 class and 1 function
    assert result.files["main.py"].class_count == 1
    assert result.files["main.py"].function_count == 1
    # utils.py has 2 functions
    assert result.files["utils.py"].function_count == 2


def test_analyze_all_files_to_llm_summary(tmp_path):
    f1 = tmp_path / "main.py"
    f1.write_text("class App:\n    pass\n\ndef run():\n    pass\n")
    f2 = tmp_path / "utils.py"
    f2.write_text("def helper():\n    pass\n")

    result = analyze_all_files(tmp_path, [f1, f2])
    summary = result.to_llm_summary()

    assert isinstance(summary, str)
    assert "main.py" in summary
    assert "utils.py" in summary
    # main.py line should report counts and entity names
    main_line = next(line for line in summary.splitlines() if "main.py" in line)
    assert "1 classes" in main_line
    assert "1 functions" in main_line
    assert "App" in main_line  # entity names in summary
    # utils.py line should report 0 classes
    utils_line = next(line for line in summary.splitlines() if "utils.py" in line)
    assert "0 classes" in utils_line


def test_analyze_all_files_to_llm_summary_truncation(tmp_path):
    for i in range(3):
        (tmp_path / f"mod{i}.py").write_text(f"def f{i}(): pass\n")

    files = list(tmp_path.glob("*.py"))
    result = analyze_all_files(tmp_path, files)
    summary = result.to_llm_summary(max_files=2)

    assert "... and 1 more files (paths only):" in summary


def test_to_llm_summary_with_dep_graph(tmp_path):
    """to_llm_summary includes dep and docstring context when dep_graph provided."""
    from worker.pipeline.dependency_graph import DependencyGraph

    f1 = tmp_path / "main.py"
    f1.write_text(
        '"""Entry point for the app."""\n'
        "import os\nfrom models import User\ndef run():\n    pass\n"
    )
    f2 = tmp_path / "models.py"
    f2.write_text('class User:\n    """A user model."""\n    pass\n')

    result = analyze_all_files(tmp_path, [f1, f2])

    graph = DependencyGraph(
        edges={"main.py": ["models.py"]},
        clusters=[["main.py", "models.py"]],
        external_deps={"main.py": ["os"]},
    )
    summary = result.to_llm_summary(dep_graph=graph)

    # main.py should have import and external dep info
    lines = summary.splitlines()
    main_idx = next(i for i, line in enumerate(lines) if line.startswith("main.py"))
    # Next line(s) should include imports info
    dep_line = lines[main_idx + 1]
    assert "models.py" in dep_line
    assert "os" in dep_line

    # models.py should have docstring line
    models_idx = next(i for i, line in enumerate(lines) if line.startswith("models.py"))
    # Check that a docstring line exists within the next 2 lines
    docstring_found = any(
        "user model" in lines[models_idx + j].lower()
        for j in range(1, min(3, len(lines) - models_idx))
    )
    assert docstring_found


def test_to_llm_summary_no_limit_by_default(tmp_path):
    """max_files=0 (default) means no truncation."""
    for i in range(10):
        (tmp_path / f"mod{i}.py").write_text(f"def f{i}(): pass\n")

    files = list(tmp_path.glob("*.py"))
    result = analyze_all_files(tmp_path, files)
    summary = result.to_llm_summary()

    # All 10 files present, no truncation message
    assert "more files" not in summary
    for i in range(10):
        assert f"mod{i}.py" in summary


def test_to_llm_summary_safety_cap(tmp_path):
    """Files beyond 800 are listed as bare paths."""
    # Create a FileAnalysis with 802 entries directly to avoid disk I/O
    files = {}
    for i in range(802):
        rel = f"mod{i:04d}.py"
        files[rel] = FileInfo(
            rel_path=rel,
            entities=[
                {"type": "function", "name": f"f{i}", "start_line": 1, "end_line": 1}
            ],
            class_count=0,
            function_count=1,
            summary=f"f{i}",
        )
    analysis = FileAnalysis(files=files)
    summary = analysis.to_llm_summary()

    # First 800 should have full detail
    assert "mod0000.py: 0 classes, 1 functions" in summary
    # Files beyond 800 should appear as bare paths
    assert "mod0800.py" in summary
    # The bare-path section should NOT have entity details
    last_lines = summary.splitlines()[-5:]
    # At least one bare path line should lack "classes"
    bare_lines = [ln for ln in last_lines if "mod080" in ln and "classes" not in ln]
    assert len(bare_lines) >= 1


def test_analyze_all_files_no_entities(tmp_path):
    f = tmp_path / "config.json"
    f.write_text('{"key": "value"}')

    result = analyze_all_files(tmp_path, [f])

    assert "config.json" in result.files
    info = result.files["config.json"]
    assert info.class_count == 0
    assert info.function_count == 0
