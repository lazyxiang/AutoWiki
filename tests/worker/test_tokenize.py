from worker.utils.tokenize import tokenize_text


def test_ascii_lowercased_and_min_length():
    assert tokenize_text("Hello WORLD ab a1") == {"hello", "world"}


def test_ascii_min_length_can_include_two_character_tokens():
    assert tokenize_text("UI DB S3 Go ab", min_ascii_len=2) == {
        "ui",
        "db",
        "s3",
        "go",
        "ab",
    }


def test_camel_and_snake_split():
    assert {"wiki", "planner"} <= tokenize_text("WikiPlanner wiki_planner")


def test_path_segments_split():
    assert {"web", "components", "wiki", "page"} <= tokenize_text(
        "web/components/WikiPage.tsx"
    )


def test_cjk_runs_extracted():
    tokens = tokenize_text("依赖图谱构建")
    assert "依赖图谱构建" in tokens
    assert {"依赖", "赖图", "图谱", "谱构", "构建"} <= tokens
    assert {"依赖图", "赖图谱", "图谱构", "谱构建"} <= tokens


def test_cjk_mixed_with_ascii():
    tokens = tokenize_text("前端 web app 路由")
    assert "web" in tokens and "app" in tokens
    assert {"前端", "路由"} <= tokens


def test_adjacent_cjk_ascii_extracts_both_signal_types():
    tokens = tokenize_text("前端API路由 Graph依赖Builder")

    assert {"api", "graph", "builder"} <= tokens
    assert {"前端", "路由", "依赖"} <= tokens


def test_no_short_tokens():
    assert "a" not in tokenize_text("a ab abc")
    assert "ab" not in tokenize_text("ab abc")


def test_preserves_accented_latin_words_without_ascii_fragments():
    tokens = tokenize_text("café naïve")
    assert "café" in tokens
    assert "naïve" in tokens
    assert "caf" not in tokens
    assert "na" not in tokens
    assert "ve" not in tokens


def test_preserves_cyrillic_words():
    tokens = tokenize_text("привет сервис")
    assert "привет" in tokens
    assert "сервис" in tokens
