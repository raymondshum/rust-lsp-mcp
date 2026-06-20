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

import logging
import pathlib
from typing import Any, cast

import chromadb
from chromadb.api.types import Metadatas
from chromadb.errors import NotFoundError

from rust_lsp_mcp.doc_chunking import chunk_markdown
from rust_lsp_mcp.settings import Settings

_log = logging.getLogger(__name__)

_COLLECTION_NAME = "ripgrep_docs"
_ADD_BATCH_SIZE = 500


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
        self._settings = settings
        self._ef = embedding_function
        self._client = chromadb.PersistentClient(path=settings.chroma_path)
        self._collection: Any = None
        self._ready: bool = False

    @property
    def is_ready(self) -> bool:
        """``True`` only when the collection is fully built and not mid-rebuild."""
        return self._ready

    def rebuild(self) -> int:
        """Wholesale rebuild: drop + recreate the collection, re-index every
        matching ``*.md``.  Flips ``is_ready`` False during the rebuild and back
        to True on completion.  Returns the number of chunks indexed.

        Synchronous/blocking — callers that must not block the event loop run it
        via a worker thread (e.g. ``anyio.to_thread.run_sync``).
        """
        # Mark not ready at the very start — never ready mid-build.
        self._ready = False
        self._collection = None

        # Drop existing collection if present.
        try:
            self._client.delete_collection(_COLLECTION_NAME)
            _log.debug("doc_store: deleted existing collection %r", _COLLECTION_NAME)
        except NotFoundError:
            # Expected on first build.
            pass
        except Exception as exc:
            _log.debug("doc_store: delete_collection raised %r (ignored)", exc)

        # Recreate with cosine distance.
        # Note: passing embedding_function=None explicitly disables the default EF in
        # chromadb 1.5.x.  When no custom EF is provided, omit the parameter entirely so
        # create_collection uses its default (DefaultEmbeddingFunction / all-MiniLM-L6-v2).
        create_kwargs: dict[str, Any] = {
            "configuration": {"hnsw": {"space": "cosine"}},
        }
        if self._ef is not None:
            create_kwargs["embedding_function"] = self._ef
        collection = self._client.create_collection(_COLLECTION_NAME, **create_kwargs)
        self._collection = collection

        # Glob markdown files.
        src_root = pathlib.Path(self._settings.ripgrep_src)
        patterns = [p.strip() for p in self._settings.doc_glob_patterns.split(",") if p.strip()]

        all_files: list[pathlib.Path] = []
        for pattern in patterns:
            all_files.extend(src_root.glob(pattern))

        # Deduplicate (multiple patterns may match same file) and filter to files only.
        seen: set[pathlib.Path] = set()
        unique_files: list[pathlib.Path] = []
        for f in all_files:
            if f not in seen and f.is_file():
                seen.add(f)
                unique_files.append(f)

        _log.debug("doc_store: found %d markdown files to index", len(unique_files))

        # Chunk and collect all docs.
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, str]] = []

        for filepath in unique_files:
            rel_path = str(filepath.relative_to(src_root))
            try:
                text = filepath.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                _log.warning("doc_store: could not read %s: %s", filepath, exc)
                continue

            chunks = chunk_markdown(text, rel_path)
            for chunk in chunks:
                ids.append(chunk.id)
                documents.append(chunk.text)
                metadatas.append({"file": chunk.file, "breadcrumb": chunk.breadcrumb})

        total = len(ids)
        _log.debug("doc_store: indexing %d chunks", total)

        # Handle empty corpus without crashing.
        if total == 0:
            self._ready = True
            return 0

        # Batch-add to avoid ChromaDB memory issues with large corpora.
        for batch_start in range(0, total, _ADD_BATCH_SIZE):
            batch_end = batch_start + _ADD_BATCH_SIZE
            collection.add(
                ids=ids[batch_start:batch_end],
                documents=documents[batch_start:batch_end],
                metadatas=cast(Metadatas, metadatas[batch_start:batch_end]),
            )

        # Only mark ready after ALL adds complete.
        self._ready = True
        return total

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
        if self._collection is None:
            return []

        count = self._collection.count()
        if count == 0:
            return []

        # Clamp n_results to the number of available documents.
        effective_n = min(n_results, count)

        result = self._collection.query(
            query_texts=[query],
            n_results=effective_n,
        )

        # Map row 0 of each list-of-lists into the documented shape.
        docs_row = result.get("documents", [[]])[0]
        metas_row = result.get("metadatas", [[]])[0]
        dists_row = result.get("distances", [[]])[0]

        output: list[dict[str, Any]] = []
        for doc_text, meta, dist in zip(docs_row, metas_row, dists_row, strict=False):
            output.append(
                {
                    "file": meta.get("file", ""),
                    "breadcrumb": meta.get("breadcrumb", ""),
                    "text": doc_text,
                    "distance": float(dist),
                }
            )

        return output


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

    "Build once" persistence: if the ChromaDB collection already exists with
    >0 items, adopt it and mark ready WITHOUT re-embedding.  Otherwise rebuild
    from scratch.  This avoids re-embedding on every server restart when the
    bind-mount data is already populated.
    """
    global _doc_store
    store = DocStore(settings)

    # Check whether a populated collection already exists (build-once persistence).
    adopted = False
    try:
        existing = store._client.get_collection(_COLLECTION_NAME)
        if existing.count() > 0:
            _log.info(
                "doc_store: adopting existing collection (%d chunks) — skipping rebuild",
                existing.count(),
            )
            store._collection = existing
            store._ready = True
            adopted = True
    except NotFoundError:
        # No existing collection — will rebuild below.
        pass
    except Exception as exc:
        _log.debug("doc_store: get_collection check raised %r — will rebuild", exc)

    if not adopted:
        _log.info("doc_store: no usable existing collection — rebuilding")
        count = store.rebuild()
        _log.info("doc_store: rebuild complete, %d chunks indexed", count)

    _doc_store = store
    return store


def clear_doc_store() -> None:
    """Drop the module singleton (lifespan teardown)."""
    global _doc_store
    _doc_store = None
