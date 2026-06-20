"""Documentation RAG store over ChromaDB (Phase 5).

INTERFACE CONTRACT (orchestrator-owned stub).
=============================================
This file defines the public surface that the ``search_docs`` tool and the
``refresh`` tool code against.  Wave-2 agent **B** replaces the
``NotImplementedError`` bodies with the real ChromaDB implementation; the
singleton accessors below are already final and must keep their signatures.

The store wraps a ChromaDB ``PersistentClient`` (cosine collection) over the
chunks produced by :func:`rust_lsp_mcp.doc_chunking.chunk_markdown`.  It is
built once at lifespan startup and rebuilt wholesale by ``refresh``.

Readiness contract (load-bearing — mirrors the analyzer's readiness gate):
    ``is_ready`` is ``False`` while a (re)build is in flight and ``True`` only
    once the collection is fully populated.  ``search_docs`` gates on this so a
    caller never receives a misleading empty/partial answer mid-rebuild.
"""

from __future__ import annotations

from typing import Any

from rust_lsp_mcp.settings import Settings


class DocStore:
    """ChromaDB-backed documentation search store.

    Args:
        settings: Runtime settings (``chroma_path``, ``ripgrep_src``,
            ``doc_glob_patterns`` are the relevant fields).
        embedding_function: Optional ChromaDB embedding function.  ``None``
            means use ChromaDB's bundled ``DefaultEmbeddingFunction``
            (all-MiniLM-L6-v2, ONNX, downloaded once to the model-cache mount).
            Fast tests inject a deterministic fake so they neither download the
            model nor hit the network.
    """

    def __init__(self, settings: Settings, embedding_function: Any | None = None) -> None:
        raise NotImplementedError  # agent B

    @property
    def is_ready(self) -> bool:
        """``True`` only when the collection is fully built and not mid-rebuild."""
        raise NotImplementedError  # agent B

    def rebuild(self) -> int:
        """Wholesale rebuild: drop + recreate the collection, re-index every
        matching ``*.md``.  Flips ``is_ready`` False during the rebuild and back
        to True on completion.  Returns the number of chunks indexed.

        Synchronous/blocking — callers that must not block the event loop run it
        via a worker thread (e.g. ``anyio.to_thread.run_sync``).
        """
        raise NotImplementedError  # agent B

    def search(self, query: str, n_results: int = 5) -> list[dict[str, Any]]:
        """Return up to ``n_results`` best-matching chunks, best-first.

        Each result dict has EXACTLY this shape (other agents depend on it)::

            {
                "file":       str,    # workspace-relative path of the source .md
                "breadcrumb": str,    # e.g. "GUIDE.md > Configuration > Ignoring files"
                "text":       str,    # the chunk text that was embedded
                "distance":   float,  # cosine distance (0 = identical), lower = closer
            }

        Returns an empty list only if the collection is empty.  (Semantic search
        over a non-empty collection always returns the top-k nearest neighbours.)
        """
        raise NotImplementedError  # agent B


# ---------------------------------------------------------------------------
# Module-level singleton — set during lifespan startup, cleared on exit.
# These accessors are FINAL (agent B implements DocStore, not these).
# ---------------------------------------------------------------------------

_doc_store: DocStore | None = None


def get_doc_store() -> DocStore | None:
    """Return the current :class:`DocStore` singleton, or ``None`` if not started.

    Tools must handle ``None`` (return ``not_ready``) — the store is absent
    before lifespan startup and after shutdown.
    """
    return _doc_store


def init_doc_store(settings: Settings) -> DocStore:
    """Construct the :class:`DocStore`, build it, set the singleton, return it.

    Called once from the FastMCP lifespan on startup.
    """
    raise NotImplementedError  # agent B


def clear_doc_store() -> None:
    """Drop the module singleton (lifespan teardown)."""
    global _doc_store
    _doc_store = None
