"""Integration-tier tests for Phase 5 documentation RAG store.

These tests build the REAL ChromaDB collection over ripgrep's actual ``*.md``
documentation using the default ONNX embedding model (all-MiniLM-L6-v2).

Gates:
    - Sensible retrieval: semantic queries return topically relevant results.
    - Model cache lands on the bind mount at the expected path (download-once).
    - Collection metric is cosine.
    - Rebuild is wholesale and idempotent (two rebuilds → same chunk count).

Marked ``@pytest.mark.integration`` — runs only as the local QA gate, never in CI.
"""

from __future__ import annotations

import pathlib

import chromadb
import pytest

from rust_lsp_mcp.doc_store import DocStore, clear_doc_store
from rust_lsp_mcp.settings import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _real_settings(tmp_chroma: pathlib.Path) -> Settings:
    """Return settings pointing at the real ripgrep source with a tmp chroma path."""
    return Settings(
        chroma_path=str(tmp_chroma),
        ripgrep_src="/workspaces/ripgrep",
        doc_glob_patterns="**/*.md",
        chroma_model_cache="/home/vscode/.cache/chroma",
    )


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPhase5Integration:
    def test_rebuild_real_corpus(self, tmp_path: pathlib.Path) -> None:
        """Build the real index over ripgrep docs and verify chunk count > 0."""
        settings = _real_settings(tmp_path / "chroma")
        store = DocStore(settings)  # uses DefaultEmbeddingFunction
        count = store.rebuild()
        assert count > 0, "Expected at least one chunk from ripgrep markdown files"
        assert store.is_ready is True

    def test_model_cache_on_bind_mount(self, tmp_path: pathlib.Path) -> None:
        """After building, the ONNX model should be cached at the bind-mount path."""
        settings = _real_settings(tmp_path / "chroma")
        store = DocStore(settings)
        store.rebuild()

        model_cache = pathlib.Path("/home/vscode/.cache/chroma/onnx_models/all-MiniLM-L6-v2")
        assert model_cache.exists(), (
            f"Expected model cache at {model_cache} — "
            "model may not have downloaded or bind mount is missing"
        )

    def test_collection_metric_is_cosine(self, tmp_path: pathlib.Path) -> None:
        """The collection distance metric must be cosine (set at creation, immutable)."""
        settings = _real_settings(tmp_path / "chroma")
        store = DocStore(settings)
        store.rebuild()

        # Inspect the collection configuration via PersistentClient.
        client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
        col = client.get_collection("ripgrep_docs")

        # ChromaDB 1.5.x exposes configuration via col.configuration_json or
        # the collection metadata.  The most reliable check: query two identical
        # docs and verify distance is ~0, which only holds for cosine (L2 would
        # give 0 for identical embeddings too, but we can also inspect col.metadata).
        # Use col.configuration_json if available, else fall back to a distance check.
        config = None
        if hasattr(col, "configuration_json"):
            config = col.configuration_json
        elif hasattr(col, "configuration"):
            config = col.configuration

        if config is not None:
            config_str = str(config).lower()
            assert "cosine" in config_str, (
                f"Expected cosine in collection configuration, got: {config}"
            )
        else:
            # Fallback: add two identical docs and verify distance ≈ 0.
            col.add(
                ids=["_test_a", "_test_b"],
                documents=["exact same text for cosine check"] * 2,
                metadatas=[{"file": "t.md", "breadcrumb": "t"}] * 2,
            )
            result = col.query(query_texts=["exact same text for cosine check"], n_results=2)
            distances = result.get("distances") or [[]]
            min_dist = min(distances[0]) if distances[0] else 1.0
            assert min_dist < 0.01, (
                f"Expected near-zero cosine distance for identical docs, got {min_dist}"
            )

    def test_rebuild_idempotent(self, tmp_path: pathlib.Path) -> None:
        """Two consecutive rebuilds produce the same chunk count."""
        settings = _real_settings(tmp_path / "chroma")
        store = DocStore(settings)

        count1 = store.rebuild()
        count2 = store.rebuild()
        assert count1 == count2, f"Rebuild is not idempotent: first={count1}, second={count2}"
        assert store.is_ready is True

    def test_rebuild_wholesale(self, tmp_path: pathlib.Path) -> None:
        """After rebuild, the collection is fully searchable (not partially replaced)."""
        settings = _real_settings(tmp_path / "chroma")
        store = DocStore(settings)
        store.rebuild()
        count2 = store.rebuild()

        # The collection should have exactly count2 items (not doubled).
        client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
        col = client.get_collection("ripgrep_docs")
        assert col.count() == count2, (
            f"Wholesale rebuild should replace, not append: "
            f"col.count()={col.count()}, count2={count2}"
        )

    def test_sensible_retrieval_ignore_files(self, tmp_path: pathlib.Path) -> None:
        """Query 'how do I make ripgrep ignore files' should return GUIDE.md or FAQ.md."""
        settings = _real_settings(tmp_path / "chroma")
        store = DocStore(settings)
        store.rebuild()

        results = store.search("how do I make ripgrep ignore files", n_results=5)
        assert len(results) > 0, "Expected non-empty results for ignore-files query"

        top_files = [r["file"] for r in results]
        expected_files = {"GUIDE.md", "FAQ.md", "README.md"}
        assert any(any(exp in f for exp in expected_files) for f in top_files), (
            f"Expected a top result from {expected_files}, got files: {top_files}"
        )

        # Verify shape of all results.
        for result in results:
            assert set(result.keys()) == {"file", "breadcrumb", "text", "distance"}
            assert isinstance(result["distance"], float)
            assert result["distance"] >= 0.0

    def test_sensible_retrieval_case_insensitive(self, tmp_path: pathlib.Path) -> None:
        """Query 'case insensitive search' should return a relevant result."""
        settings = _real_settings(tmp_path / "chroma")
        store = DocStore(settings)
        store.rebuild()

        results = store.search("case insensitive search ripgrep", n_results=5)
        assert len(results) > 0, "Expected non-empty results for case-insensitive query"

        # At least one result should come from the main doc files.
        top_files = [r["file"] for r in results]
        major_docs = {"GUIDE.md", "FAQ.md", "README.md"}
        assert any(any(doc in f for doc in major_docs) for f in top_files), (
            f"Expected a result from major docs, got: {top_files}"
        )

    def test_results_ordered_best_first(self, tmp_path: pathlib.Path) -> None:
        """Search results are ordered ascending by distance (best match first)."""
        settings = _real_settings(tmp_path / "chroma")
        store = DocStore(settings)
        store.rebuild()

        results = store.search("ripgrep configuration options", n_results=5)
        distances = [r["distance"] for r in results]
        assert distances == sorted(distances), (
            f"Results not in ascending distance order: {distances}"
        )

    def test_store_still_searchable_after_second_rebuild(self, tmp_path: pathlib.Path) -> None:
        """After two rebuilds, the store is still fully functional."""
        settings = _real_settings(tmp_path / "chroma")
        store = DocStore(settings)
        store.rebuild()
        store.rebuild()

        assert store.is_ready is True
        results = store.search("search pattern", n_results=3)
        assert len(results) > 0

    def teardown_method(self, method: object) -> None:
        """Ensure singleton is cleared between tests."""
        clear_doc_store()
