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
    legacy_path.write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")

    loaded = load_repo_index(tmp_path)

    assert loaded == expected
    assert new_path.exists()
    assert not legacy_path.exists()
    assert json.loads(new_path.read_text(encoding="utf-8")) == expected


def test_load_repo_index_reads_utf8_payload(tmp_path: Path):
    from worker.pipeline.retrieval.repo_index import REPO_INDEX_VERSION
    from worker.pipeline.retrieval.repo_index_io import load_repo_index

    ast_dir = tmp_path / "ast"
    ast_dir.mkdir()
    expected = {
        "index_version": REPO_INDEX_VERSION,
        "files": {"模块.py": {"tokens": ["配置"]}},
    }
    (ast_dir / "repo_index.json").write_text(
        json.dumps(expected, ensure_ascii=False), encoding="utf-8"
    )

    assert load_repo_index(tmp_path) == expected


def test_validate_repo_index_version_rejects_bool_and_reports_versions():
    from worker.pipeline.retrieval.repo_index import REPO_INDEX_VERSION
    from worker.pipeline.retrieval.repo_index_io import (
        RepoIndexOutdatedError,
        validate_repo_index_version,
    )

    with pytest.raises(RepoIndexOutdatedError) as excinfo:
        validate_repo_index_version({"index_version": True})

    message = str(excinfo.value)
    assert "found=True" in message
    assert f"expected={REPO_INDEX_VERSION}" in message


def test_symbol_path_handles_dotfiles_without_leading_dot():
    from worker.pipeline.retrieval.repo_index import _symbol_path

    assert _symbol_path(".env", "load") == "env.load"
    assert _symbol_path(".env", "") == "env"


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
