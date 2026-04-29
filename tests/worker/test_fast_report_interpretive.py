from worker.fast_report_interpretive import (
    InterpretiveBundle,
    build_interpretive_bundle,
)


def test_auto_attached_payload_caps_at_8k_tokens_dropping_lowest_score():
    big = "x" * 40_000
    selected = [
        {
            "file": "a.py",
            "name": "f1",
            "score": 1.0,
            "docstring": big,
            "module_docstring": "Mod A.",
            "leading_comment": None,
        },
        {
            "file": "b.py",
            "name": "f2",
            "score": 100.0,
            "docstring": "ok",
            "module_docstring": "Mod B.",
            "leading_comment": None,
        },
    ]

    bundle = build_interpretive_bundle(
        selected_entities=selected, index={"readme_sections": []}, intent_tokens=set()
    )

    assert any(
        item["source"] == "entity_docstring" and "ok" in item["text"]
        for item in bundle.entries
    )
    assert not any(
        item["source"] == "entity_docstring" and item["text"].startswith("xxxx")
        for item in bundle.entries
    )


def test_auto_attaches_entity_and_module_context_once_per_file():
    selected = [
        {
            "file": "a.py",
            "name": "f1",
            "score": 3.0,
            "docstring": "Entity doc.",
            "module_docstring": "Module doc.",
            "leading_comment": "Leading comment.",
        },
        {
            "file": "a.py",
            "name": "f2",
            "score": 2.0,
            "docstring": None,
            "module_docstring": "Module doc.",
            "leading_comment": None,
        },
    ]

    bundle = build_interpretive_bundle(
        selected_entities=selected, index={"readme_sections": []}, intent_tokens=set()
    )

    sources = [entry["source"] for entry in bundle.entries]
    assert "entity_docstring" in sources
    assert "entity_leading_comment" in sources
    assert sources.count("module_docstring") == 1


def test_readme_section_ranking_top_5_with_overlap_score_and_body_cap():
    sections = [
        {"heading": "Deployment", "body": "Use docker-compose."},
        {"heading": "Architecture", "body": "AutoWiki uses a 6-stage pipeline."},
        {"heading": "Pipeline Details", "body": "pipeline stage " + ("x" * 900)},
        {"heading": "Stage A", "body": "stage"},
        {"heading": "Stage B", "body": "stage"},
        {"heading": "Stage C", "body": "stage"},
        {"heading": "Stage D", "body": "stage"},
        {"heading": "Random", "body": "Unrelated text."},
    ]

    bundle = build_interpretive_bundle(
        selected_entities=[],
        index={"readme_sections": sections},
        intent_tokens={"pipeline", "stage"},
    )

    readme_entries = [
        entry for entry in bundle.entries if entry["source"] == "readme_section"
    ]
    assert [entry["heading"] for entry in readme_entries][:2] == [
        "Architecture",
        "Pipeline Details",
    ]
    assert len(readme_entries) == 5
    pipeline_details = next(
        entry for entry in readme_entries if entry["heading"] == "Pipeline Details"
    )
    assert len(pipeline_details["text"]) == 800


def test_interpretive_bundle_has_no_citations():
    bundle = build_interpretive_bundle(
        selected_entities=[
            {
                "file": "a.py",
                "name": "f",
                "score": 1.0,
                "docstring": "doc",
                "module_docstring": None,
                "leading_comment": None,
            }
        ],
        index={"readme_sections": []},
        intent_tokens=set(),
    )

    assert isinstance(bundle, InterpretiveBundle)
    assert not hasattr(bundle, "citations")
