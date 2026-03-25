import pytest
from pathlib import Path
from worker.pipeline.ast_analysis import (
    analyze_file, build_module_tree, build_enhanced_module_tree, SUPPORTED_LANGUAGES,
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

def test_analyze_python_file_entities_have_type():
    result = analyze_file(FIXTURE / "models.py")
    for entity in result["entities"]:
        assert "type" in entity   # "class" or "function"
        assert "name" in entity

def test_build_module_tree_groups_by_dir(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth").mkdir()
    files = [tmp_path / "src" / "main.py", tmp_path / "src" / "auth" / "handler.py"]
    for f in files:
        f.write_text("x = 1")
    tree = build_module_tree(tmp_path, files)
    modules = [m["path"] for m in tree]
    assert "src" in modules or any("src" in m for m in modules)

def test_unsupported_language_returns_none():
    from pathlib import Path
    import tempfile
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


def test_build_enhanced_module_tree(tmp_path):
    (tmp_path / "src").mkdir()
    src_file = tmp_path / "src" / "main.py"
    src_file.write_text("class App:\n    pass\n\ndef run():\n    pass\n")
    root_file = tmp_path / "setup.py"
    root_file.write_text("x = 1\n")

    files = [src_file, root_file]
    tree = build_enhanced_module_tree(tmp_path, files)

    assert len(tree) >= 1
    # Find the src module
    src_mod = [m for m in tree if m["path"] == "src"]
    assert len(src_mod) == 1
    assert src_mod[0]["class_count"] == 1
    assert src_mod[0]["function_count"] == 1
    assert "App" in src_mod[0]["summary"]
    assert len(src_mod[0]["classes"]) == 1
    assert src_mod[0]["classes"][0]["name"] == "App"


def test_build_enhanced_module_tree_empty_entities(tmp_path):
    f = tmp_path / "data.json"
    f.write_text('{"key": "value"}')
    tree = build_enhanced_module_tree(tmp_path, [f])
    assert len(tree) >= 1
    mod = tree[0]
    assert mod["class_count"] == 0
    assert mod["function_count"] == 0
    assert mod["summary"] == "(no named entities)"
