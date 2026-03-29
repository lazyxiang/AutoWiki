from worker.pipeline.dependency_graph import (
    DependencyGraph,
    _extract_imports,
    build_dependency_graph,
    format_for_llm_prompt,
    summarize_page_deps,
)


def test_extract_python_imports(tmp_path):
    f = tmp_path / "main.py"
    f.write_text("import os\nfrom models import User\nfrom utils import greet\n")
    imports = _extract_imports(f, f.read_text())
    assert "os" in imports
    assert "models" in imports
    assert "utils" in imports


def test_extract_js_imports(tmp_path):
    f = tmp_path / "app.js"
    f.write_text(
        "import React from 'react';\n"
        "import { User } from './models';\n"
        "const fs = require('fs');\n"
    )
    imports = _extract_imports(f, f.read_text())
    assert "react" in imports
    assert "./models" in imports
    assert "fs" in imports


def test_extract_go_imports(tmp_path):
    f = tmp_path / "main.go"
    f.write_text('package main\n\nimport "fmt"\nimport "myapp/models"\n')
    imports = _extract_imports(f, f.read_text())
    assert "fmt" in imports
    assert "myapp/models" in imports


def test_extract_rust_imports(tmp_path):
    f = tmp_path / "main.rs"
    f.write_text("use std::io;\nmod models;\nuse crate::utils;\n")
    imports = _extract_imports(f, f.read_text())
    assert "std::io" in imports
    assert "models" in imports
    assert "crate::utils" in imports


def test_extract_c_includes(tmp_path):
    f = tmp_path / "main.c"
    f.write_text('#include <stdio.h>\n#include "utils.h"\n#include "models.h"\n')
    imports = _extract_imports(f, f.read_text())
    # Only local includes (double-quoted) should be extracted
    assert "utils.h" in imports
    assert "models.h" in imports


def test_build_dependency_graph(tmp_path):
    (tmp_path / "main.py").write_text(
        "from models import User\nfrom utils import greet\n"
    )
    (tmp_path / "models.py").write_text("class User:\n    pass\n")
    (tmp_path / "utils.py").write_text("import os\ndef greet():\n    pass\n")

    files = [tmp_path / "main.py", tmp_path / "models.py", tmp_path / "utils.py"]
    graph = build_dependency_graph(files, tmp_path)

    assert isinstance(graph, DependencyGraph)
    # main.py should depend on models.py and utils.py
    assert "main.py" in graph.edges
    deps = graph.edges["main.py"]
    assert "models.py" in deps
    assert "utils.py" in deps


def test_build_dependency_graph_clusters(tmp_path):
    (tmp_path / "a.py").write_text("from b import foo\n")
    (tmp_path / "b.py").write_text("from a import bar\n")
    (tmp_path / "c.py").write_text("x = 1\n")

    files = [tmp_path / "a.py", tmp_path / "b.py", tmp_path / "c.py"]
    graph = build_dependency_graph(files, tmp_path)

    # a.py and b.py should be in the same cluster
    assert len(graph.clusters) >= 1
    found_ab_cluster = False
    for cluster in graph.clusters:
        if "a.py" in cluster and "b.py" in cluster:
            found_ab_cluster = True
            break
    assert found_ab_cluster


def test_format_for_llm_prompt(tmp_path):
    (tmp_path / "main.py").write_text("from models import User\n")
    (tmp_path / "models.py").write_text("class User:\n    pass\n")

    files = [tmp_path / "main.py", tmp_path / "models.py"]
    graph = build_dependency_graph(files, tmp_path)

    result = format_for_llm_prompt(graph)
    assert isinstance(result, str)
    assert "→" in result
    assert "main.py" in result
    assert "models.py" in result


def test_format_for_llm_prompt_no_deps(tmp_path):
    (tmp_path / "standalone.py").write_text("x = 1\n")
    graph = build_dependency_graph([tmp_path / "standalone.py"], tmp_path)
    result = format_for_llm_prompt(graph)
    assert "(no internal dependencies detected)" in result


def test_format_for_llm_prompt_truncation(tmp_path):
    """When edges exceed max_edges, a '...more edges' line is appended."""
    # Create a hub file with many deps
    deps = "\n".join(f"from mod{i} import x" for i in range(10))
    (tmp_path / "hub.py").write_text(deps + "\n")
    for i in range(10):
        (tmp_path / f"mod{i}.py").write_text("x = 1\n")

    all_files = list(tmp_path.glob("*.py"))
    graph = build_dependency_graph(all_files, tmp_path)

    result = format_for_llm_prompt(graph, max_edges=3)
    assert "more edges not shown" in result


def test_summarize_page_deps(tmp_path):
    (tmp_path / "main.py").write_text("from models import User\n")
    (tmp_path / "models.py").write_text("class User:\n    pass\n")
    (tmp_path / "utils.py").write_text("import os\ndef greet():\n    pass\n")

    files = [tmp_path / "main.py", tmp_path / "models.py", tmp_path / "utils.py"]
    graph = build_dependency_graph(files, tmp_path)

    # "main.py" page depends on "models.py" (which is outside this page)
    result = summarize_page_deps(["main.py"], graph)
    assert "depends_on" in result
    assert "depended_by" in result
    assert "external_deps" in result
    assert "models.py" in result["depends_on"]

    # "models.py" page is depended on by "main.py"
    result2 = summarize_page_deps(["models.py"], graph)
    assert "main.py" in result2["depended_by"]


def test_unsupported_extension_returns_no_imports(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("a,b,c\n1,2,3\n")
    imports = _extract_imports(f, f.read_text())
    assert imports == []
