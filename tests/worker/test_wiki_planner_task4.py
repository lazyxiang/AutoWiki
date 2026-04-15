import pytest
from worker.pipeline.wiki_planner import _validate_assignments, validate_wiki_plan, WikiPageSpec, WikiPlan

def test_validate_assignments_validates_reference_files():
    """Reference files must be valid paths from all_files."""
    result = {
        "Overview": {
            "primary": ["main.py"],
            "reference": ["non_existent.py"]
        }
    }
    outline = [{"title": "Overview", "purpose": "..."}]
    all_files = ["main.py"]
    
    with pytest.raises(ValueError, match="invalid reference files"):
        _validate_assignments(result, outline, all_files=all_files)

def test_validate_assignments_enforces_primary_coverage():
    """Every file in all_files must be assigned as primary exactly once."""
    result = {
        "Overview": {
            "primary": ["main.py"],
            "reference": []
        }
    }
    outline = [{"title": "Overview", "purpose": "..."}]
    all_files = ["main.py", "missing.py"]
    
    with pytest.raises(ValueError, match="Primary Coverage violation"):
        _validate_assignments(result, outline, all_files=all_files)

def test_validate_wiki_plan_empty_non_overview_requires_primary():
    """A non-overview page must have primary files, even if it has reference files."""
    raw = {
        "pages": [
            {
                "title": "Overview",
                "purpose": "...",
                "primary_files": ["main.py"]
            },
            {
                "title": "Details",
                "purpose": "...",
                "primary_files": [],
                "reference_files": ["main.py"]
            }
        ]
    }
    with pytest.raises(ValueError, match="Page 'Details' has no primary files"):
        validate_wiki_plan(raw, all_files=["main.py"])

def test_validate_wiki_plan_orphan_to_primary():
    """Orphaned files should be appended to primary_files."""
    raw = {
        "pages": [
            {
                "title": "Overview",
                "purpose": "...",
                "primary_files": ["main.py"]
            }
        ]
    }
    all_files = ["main.py", "orphan.py"]
    plan = validate_wiki_plan(raw, all_files=all_files)
    assert "orphan.py" in plan.pages[0].primary_files
    # Ensure it's not in reference_files
    assert "orphan.py" not in plan.pages[0].reference_files

def test_fallback_plan_uses_primary_files():
    """Fallback plan should use primary_files explicitly."""
    from worker.pipeline.wiki_planner import _fallback_plan
    all_files = ["a.py", "b.py"]
    clusters = [["a.py"], ["b.py"]]
    plan = _fallback_plan("test", all_files, clusters)
    
    assert len(plan.pages) > 0
    for p in plan.pages:
        assert isinstance(p.primary_files, list)
        # reference_files should be empty in fallback
        assert p.reference_files == []
    
    # Check Overview page
    overview = next(p for p in plan.pages if p.title == "Overview")
    # In fallback with clusters, Overview gets files not in clusters. 
    # Here all are in clusters, so Overview is empty primary.
    assert overview.primary_files == []
    
    # Check component pages
    comp1 = next(p for p in plan.pages if p.title == "Component 1")
    assert comp1.primary_files == ["a.py"]
