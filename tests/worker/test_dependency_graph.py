import pytest
from pathlib import Path
from worker.pipeline.dependency_graph import (
    build_dependency_graph,
    summarize_dependencies,
    _extract_imports,
    DependencyGraph,
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
    f.write_text("import React from 'react';\nimport { User } from './models';\nconst fs = require('fs');\n")
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
    (tmp_path / "main.py").write_text("from models import User\nfrom utils import greet\n")
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


def test_summarize_dependencies(tmp_path):
    (tmp_path / "main.py").write_text("from models import User\n")
    (tmp_path / "models.py").write_text("class User:\n    pass\n")

    files = [tmp_path / "main.py", tmp_path / "models.py"]
    graph = build_dependency_graph(files, tmp_path)

    module_files = {
        ".": ["main.py", "models.py"],
    }
    summary = summarize_dependencies(graph, module_files)
    assert "." in summary


def test_summarize_dependencies_cross_module():
    """Test cross-module dependency detection."""
    graph = DependencyGraph(
        edges={"src/main.py": ["lib/utils.py"]},
        clusters=[],
        external_deps={},
    )
    module_files = {
        "src": ["src/main.py"],
        "lib": ["lib/utils.py"],
    }
    summary = summarize_dependencies(graph, module_files)
    assert "lib" in summary["src"]["depends_on"]
    assert "src" in summary["lib"]["depended_by"]


def test_unsupported_extension_returns_no_imports(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("a,b,c\n1,2,3\n")
    imports = _extract_imports(f, f.read_text())
    assert imports == []
