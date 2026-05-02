from worker.utils.tokenize import tokenize_text


def test_ascii_lowercased_and_min_length():
    assert tokenize_text("Hello WORLD ab a1") == {"hello", "world"}


def test_camel_and_snake_split():
    assert {"wiki", "planner"} <= tokenize_text("WikiPlanner wiki_planner")


def test_path_segments_split():
    assert {"web", "components", "wiki", "page"} <= tokenize_text(
        "web/components/WikiPage.tsx"
    )


def test_cjk_runs_extracted():
    tokens = tokenize_text("依赖图谱构建")
    assert "依赖图谱构建" in tokens or {"依赖", "图谱", "构建"} & tokens


def test_cjk_mixed_with_ascii():
    tokens = tokenize_text("前端 web app 路由")
    assert "web" in tokens and "app" in tokens
    assert any("前" in t or "路" in t for t in tokens)


def test_no_short_tokens():
    assert "a" not in tokenize_text("a ab abc")
    assert "ab" not in tokenize_text("ab abc")
