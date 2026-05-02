import importlib
import json
import sys
from pathlib import Path

import pytest


def test_load_repo_index_renames_legacy_artifact(tmp_path: Path):
    from worker.pipeline.retrieval.repo_index import REPO_INDEX_VERSION
    from worker.pipeline.retrieval.repo_index_io import load_repo_index

    ast_dir = tmp_path / "ast"
    ast_dir.mkdir()
    legacy_path = ast_dir / "fast_report_index.json"
    new_path = ast_dir / "repo_index.json"
    expected = {"index_version": REPO_INDEX_VERSION, "files": {"main.py": {}}}
    legacy_path.write_text(json.dumps(expected))

    loaded = load_repo_index(tmp_path)

    assert loaded == expected
    assert new_path.exists()
    assert not legacy_path.exists()
    assert json.loads(new_path.read_text()) == expected


def test_load_repo_index_raises_missing_when_no_artifact(tmp_path: Path):
    from worker.pipeline.retrieval.repo_index_io import (
        RepoIndexMissingError,
        load_repo_index,
    )

    with pytest.raises(RepoIndexMissingError, match="repo_index.json"):
        load_repo_index(tmp_path)


def test_fast_report_index_import_warns_and_exports_compat_aliases():
    sys.modules.pop("worker.pipeline.fast_report_index", None)

    with pytest.warns(DeprecationWarning, match="repo_index"):
        module = importlib.import_module("worker.pipeline.fast_report_index")

    assert module.INDEX_VERSION == module.REPO_INDEX_VERSION
    assert module.build_fast_report_index is module.build_repo_index
