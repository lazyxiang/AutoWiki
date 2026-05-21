"""Shared error types for the AutoWiki worker and API."""


class FeatureDisabledError(RuntimeError):
    """Raised when an indexing feature is temporarily disabled.

    Used by Deep Research after B2.5 (FAISS removal) until its retrieval is
    migrated to KeywordIndex. See issue #43.
    """
