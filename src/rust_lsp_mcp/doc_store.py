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
    ``state`` is one of ``"building"`` / ``"ready"`` / ``"error"``; ``is_ready``
    (kept for backward compatibility) is ``True`` only when ``state ==
    "ready"``.  ``search_docs`` gates on this so a caller never receives a
    misleading empty/partial answer mid-rebuild, and surfaces a permanent
    failure (``"error"``) distinctly from a transient one (``"building"``).

Startup split (DS-08):
    ``init_doc_store`` (synchronous) does the cheap part — construct the
    store, set the module singleton, and attempt to adopt an already-built
    on-disk collection — then, only if nothing was adopted, blocks on
    ``rebuild()``.  ``init_doc_store_background`` instead does the cheap part
    synchronously and offloads ``rebuild()`` to a worker thread via an
    ``asyncio.Task``, so the FastMCP lifespan can ``yield`` (start serving
    ``status``/navigation tools) without waiting for the embedding pass.  Both
    entry points share the singleton-before-rebuild ordering, so
    ``get_doc_store()``/``doc_store_state()`` observe the in-flight store the
    moment either one is called.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
import threading
from typing import Any, cast

import chromadb
from chromadb.api.types import Metadatas
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import NotFoundError

from rust_lsp_mcp.doc_chunking import chunk_markdown
from rust_lsp_mcp.settings import Settings

_log = logging.getLogger(__name__)

_ADD_BATCH_SIZE = 500

# Doc-store readiness tri-state — mirrors the analyzer's STATE_* constants.
DOC_STATE_BUILDING = "building"
DOC_STATE_READY = "ready"
DOC_STATE_ERROR = "error"


def _project_fingerprint(settings: Settings) -> str:
    """Return a stable identity string for ``settings.project_root``.

    Resolved to an absolute, symlink-free path so different spellings of the
    same directory (relative vs absolute, trailing slash, symlink hops) match,
    while genuinely different projects fingerprint differently.  Stored in
    collection metadata at build time (DS-05) so a collection built for one
    project is never silently adopted by a server pointed at another.
    """
    return str(pathlib.Path(settings.project_root).resolve())


class DocStore:
    """ChromaDB-backed documentation search store.

    Args:
        settings: Runtime settings (``chroma_path``, ``project_root``,
            ``doc_collection``, ``doc_glob_patterns`` are the relevant fields).
        embedding_function: Optional ChromaDB embedding function.  ``None``
            means use ChromaDB's bundled ``DefaultEmbeddingFunction``
            (all-MiniLM-L6-v2, ONNX, downloaded once to the model-cache mount).
            Fast tests inject a deterministic fake so they neither download the
            model nor hit the network.
    """

    def __init__(self, settings: Settings, embedding_function: Any | None = None) -> None:
        self._settings = settings
        self._ef = embedding_function
        # NOTE: ChromaDB hardcodes the ONNX model cache to Path.home()/.cache/chroma
        # regardless of any setting; persist that path at the container level (a
        # devcontainer / docker-compose bind mount, or the image's /data volume).
        # anonymized_telemetry=False: this is a single-host stdio service — disable
        # ChromaDB's telemetry so it neither emits output nor makes a network call.
        self._client = chromadb.PersistentClient(
            path=settings.chroma_path,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection: Any = None
        self._state: str = DOC_STATE_BUILDING
        self._error: str | None = None
        # Guards rebuild() against concurrent invocation (e.g. the DS-08
        # background build task racing a synchronous refresh() rebuild).
        self._build_lock = threading.Lock()

    @property
    def is_ready(self) -> bool:
        """``True`` only when the collection is fully built and not mid-rebuild.

        Kept for backward compatibility; equivalent to ``state == "ready"``.
        """
        return self._state == DOC_STATE_READY

    @property
    def state(self) -> str:
        """One of ``"building"`` / ``"ready"`` / ``"error"``."""
        return self._state

    @property
    def error_message(self) -> str | None:
        """``"{ExceptionType}: {message}"`` if the last ``rebuild()`` failed, else ``None``."""
        return self._error

    def rebuild(self) -> int:
        """Wholesale rebuild: drop + recreate the collection, re-index every
        matching ``*.md``.  Flips ``state`` to ``"building"`` during the rebuild
        and to ``"ready"`` on completion (or ``"error"`` on failure, re-raising
        the exception).  Returns the number of chunks indexed.

        Synchronous/blocking — callers that must not block the event loop run it
        via a worker thread (e.g. ``anyio.to_thread.run_sync`` or
        ``asyncio.to_thread``).  Serialized behind ``_build_lock`` so a
        concurrent caller cannot interleave with an in-flight rebuild.
        """
        with self._build_lock:
            try:
                return self._rebuild_locked()
            except Exception as exc:
                self._state = DOC_STATE_ERROR
                self._error = f"{type(exc).__name__}: {exc}"
                raise

    def _rebuild_locked(self) -> int:
        """The actual rebuild body; must only be called while holding ``_build_lock``."""
        # Mark not ready at the very start — never ready mid-build.  Clearing
        # _error here (rather than only on success) means a rebuild that is
        # itself interrupted mid-flight (e.g. process killed) leaves the
        # store honestly "building" rather than replaying a stale error from
        # a previous failed attempt.
        self._state = DOC_STATE_BUILDING
        self._error = None
        self._collection = None

        collection_name = self._settings.doc_collection
        # Computed once up front; stamped into collection metadata below so a
        # later init_doc_store() can refuse to adopt a collection built for a
        # different project_root (DS-05).
        project_fingerprint = _project_fingerprint(self._settings)

        # Drop existing collection if present.
        try:
            self._client.delete_collection(collection_name)
            _log.debug("doc_store: deleted existing collection %r", collection_name)
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
        collection = self._client.create_collection(collection_name, **create_kwargs)
        self._collection = collection

        # Glob markdown files.
        src_root = pathlib.Path(self._settings.project_root)
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

        # Compute the exclusion set from doc_exclude_patterns and remove those paths.
        exclude_patterns = [
            p.strip() for p in self._settings.doc_exclude_patterns.split(",") if p.strip()
        ]
        if exclude_patterns:
            excluded: set[pathlib.Path] = set()
            for pattern in exclude_patterns:
                excluded.update(src_root.glob(pattern))
            before_count = len(unique_files)
            unique_files = [f for f in unique_files if f not in excluded]
            removed = before_count - len(unique_files)
            _log.debug("doc_store: excluded %d files matching doc_exclude_patterns", removed)

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
            # Write completion sentinel so the adopt branch recognises an
            # intentionally-empty corpus as a completed (not interrupted) build.
            # project_root is stamped alongside it (DS-05) — modify() REPLACES
            # metadata wholesale (verified against chromadb 1.5.9), so both keys
            # must be written together in one call.
            collection.modify(
                metadata={"build_complete": True, "project_root": project_fingerprint}
            )
            self._state = DOC_STATE_READY
            return 0

        # Batch-add to avoid ChromaDB memory issues with large corpora.
        for batch_start in range(0, total, _ADD_BATCH_SIZE):
            batch_end = batch_start + _ADD_BATCH_SIZE
            collection.add(
                ids=ids[batch_start:batch_end],
                documents=documents[batch_start:batch_end],
                metadatas=cast(Metadatas, metadatas[batch_start:batch_end]),
            )

        # Write completion sentinel BEFORE flipping is_ready — a hard-killed build
        # will lack this sentinel, causing init_doc_store to rebuild rather than
        # adopt a silently-partial collection.  project_root is stamped alongside
        # it (DS-05) so a later init_doc_store() can detect cross-project reuse.
        collection.modify(metadata={"build_complete": True, "project_root": project_fingerprint})

        # Only mark ready after ALL adds AND the sentinel are written.
        self._state = DOC_STATE_READY
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

# Set when _prepare_store fails to construct the DocStore itself (e.g. a bad
# chroma_path); read by doc_store_state() when there is no store singleton to
# ask.  Cleared at the start of every _prepare_store call and by
# clear_doc_store().
_init_error: str | None = None

# The background rebuild task spawned by init_doc_store_background(), or
# None if no background build is in flight (either none was started, or the
# store was adopted synchronously with nothing to build).
_build_task: asyncio.Task[None] | None = None


def get_doc_store() -> DocStore | None:
    """Return the current :class:`DocStore` singleton, or ``None`` if not started.

    Tools must handle ``None`` (return ``not_ready``) — the store is absent
    before lifespan startup and after shutdown.
    """
    return _doc_store


def doc_store_state() -> tuple[str, str | None]:
    """Return ``(state, error_message)`` for the doc store without requiring a store.

    Mirrors :attr:`DocStore.state` / :attr:`DocStore.error_message`, but is
    usable even before the singleton exists (e.g. while the background build
    task's ``_prepare_store`` call is still constructing it) or if
    construction itself failed.

    - If the singleton is set, defer to it (``store.state``, ``store.error_message``).
    - Else, if construction previously failed, report ``("error", _init_error)``.
    - Else, report ``("building", None)`` — construction/build has not
      reached the singleton-assignment point yet.
    """
    if _doc_store is not None:
        return _doc_store.state, _doc_store.error_message
    if _init_error is not None:
        return DOC_STATE_ERROR, _init_error
    return DOC_STATE_BUILDING, None


def _try_adopt(store: DocStore, settings: Settings, embedding_function: Any | None) -> bool:
    """Attempt to adopt an existing, fully-built ChromaDB collection onto *store*.

    Cheap (no embedding work): a single ``get_collection`` metadata check.
    "Build once" persistence: if the ChromaDB collection already exists with
    >0 items, was built for the SAME ``project_root`` (DS-05 identity check),
    and carries the build-complete sentinel, adopt it (``store._collection``
    and ``store._state = DOC_STATE_READY``) and return ``True`` WITHOUT
    re-embedding.  Returns ``False`` on any failure to adopt (not found,
    interrupted build, cross-project mismatch, or an unexpected exception) —
    mirroring the original ``init_doc_store``'s fall-through-to-rebuild
    behaviour exactly.

    Args:
        store: The just-constructed :class:`DocStore` to adopt onto.
        settings: Runtime settings (for ``doc_collection`` / fingerprinting).
        embedding_function: Same embedding function passed to ``DocStore()``;
            forwarded to ``get_collection`` so an adopted collection queries
            with the SAME EF it was built with.
    """
    current_fingerprint = _project_fingerprint(settings)
    try:
        # get_collection's default embedding_function is chromadb's
        # DefaultEmbeddingFunction() (NOT None) — verified against chromadb
        # 1.5.9. Passing embedding_function=None explicitly is NOT equivalent
        # to omitting the kwarg for a non-default EF: a custom (test) EF must
        # be passed through unchanged so the adopted collection queries with
        # the SAME EF it was built with, entirely offline. So only pass the
        # kwarg when it is not None, mirroring rebuild()'s create_kwargs
        # pattern.
        get_kwargs: dict[str, Any] = {}
        if embedding_function is not None:
            get_kwargs["embedding_function"] = embedding_function
        existing = store._client.get_collection(settings.doc_collection, **get_kwargs)
        meta = existing.metadata or {}
        existing_project_root = meta.get("project_root")
        if (
            existing.count() > 0
            and meta.get("build_complete")
            and existing_project_root == current_fingerprint
        ):
            _log.info(
                "doc_store: adopting existing collection (%d chunks) — skipping rebuild",
                existing.count(),
            )
            store._collection = existing
            store._state = DOC_STATE_READY
            return True
        if existing.count() > 0 and meta.get("build_complete"):
            # Sentinel present but built for a different project_root (or a
            # pre-DS-05 collection with no project_root at all, which compares
            # unequal to current_fingerprint and is safely treated the same
            # way — backward compatible: it just triggers a rebuild instead of
            # silently adopting an unidentified collection).
            _log.info(
                "doc_store: collection was built for project_root=%r but current is %r"
                " — rebuilding",
                existing_project_root,
                current_fingerprint,
            )
        elif existing.count() > 0:
            _log.info(
                "doc_store: existing collection has %d chunks but missing build_complete sentinel"
                " (interrupted build) — will rebuild",
                existing.count(),
            )
        # count() == 0 with no sentinel also falls through to rebuild.
        return False
    except NotFoundError:
        # No existing collection — will rebuild below.
        return False
    except Exception as exc:
        _log.debug("doc_store: get_collection check raised %r — will rebuild", exc)
        return False


def _prepare_store(settings: Settings, embedding_function: Any | None = None) -> DocStore:
    """Construct the :class:`DocStore`, publish it as the singleton, attempt adopt.

    The cheap half of startup (DS-08): no embedding work happens here — only
    object construction (a ChromaDB client open, no collection I/O beyond
    ``_try_adopt``'s single metadata read) and, if a completed same-project
    collection already exists on disk, an in-place adopt.  Callers
    (``init_doc_store`` / ``init_doc_store_background``) decide whether to
    follow up with a (possibly backgrounded) ``rebuild()``.

    The singleton is set BEFORE any build is attempted — intentional (DS-14):
    ``get_doc_store()`` / ``doc_store_state()`` observe the in-flight
    ``"building"`` store immediately rather than ``None``.

    Raises whatever :class:`DocStore` construction raises (after recording it
    in the module-level ``_init_error`` for ``doc_store_state()`` to report);
    callers are responsible for catching it.
    """
    global _doc_store, _init_error
    _init_error = None
    try:
        store = DocStore(settings, embedding_function=embedding_function)
    except Exception as exc:
        _init_error = f"{type(exc).__name__}: {exc}"
        raise
    _doc_store = store
    _try_adopt(store, settings, embedding_function)
    return store


def init_doc_store(settings: Settings, embedding_function: Any | None = None) -> DocStore:
    """Construct the :class:`DocStore`, build it (if not adopted), return it.

    Synchronous/blocking — used by tests and by ``refresh`` (which already
    runs off the event loop via ``run_sync``).  Not used directly by lifespan
    startup any more; see ``init_doc_store_background`` for the non-blocking
    entry point.

    "Build once" persistence: if the ChromaDB collection already exists with
    >0 items, was built for the SAME ``project_root`` (DS-05 identity check),
    and carries the build-complete sentinel, adopt it and mark ready WITHOUT
    re-embedding.  Otherwise rebuild from scratch.  This avoids re-embedding on
    every server restart when the bind-mount data is already populated, while
    refusing to silently serve a previous project's docs when
    ``RLM_PROJECT_ROOT`` is repointed at a different project but the same
    ``chroma_path``/``doc_collection`` (the documented shared-volume,
    repo-agnostic flow).

    Args:
        settings: Runtime settings.
        embedding_function: Optional ChromaDB embedding function forwarded to
            the underlying :class:`DocStore` AND to the adopt-path
            ``get_collection`` call.  ``None`` (production default) means "use
            ChromaDB's default EF" — tests inject a deterministic fake so the
            real adopt gate, interrupted-build fallback, and singleton
            assignment run fully offline (DS-06).
    """
    store = _prepare_store(settings, embedding_function)
    if not store.is_ready:
        _log.info("doc_store: no usable existing collection — rebuilding")
        count = store.rebuild()
        _log.info("doc_store: rebuild complete, %d chunks indexed", count)
    return store


async def init_doc_store_background(
    settings: Settings, embedding_function: Any | None = None
) -> None:
    """Non-blocking startup entry point (DS-08): prepare the store, build in the background.

    Never raises — this is called directly from the FastMCP lifespan, which
    must not fail startup over a doc-index problem (nav tools are unaffected).
    Any failure is logged and swallowed; ``doc_store_state()`` /
    ``get_doc_store()`` remain the source of truth for callers that care.

    Sequence:
        1. ``_prepare_store`` — cheap: construct + publish singleton + adopt
           attempt.  On exception, log and return (singleton stays unset or is
           whatever ``_prepare_store`` left behind; ``_init_error`` is set).
        2. If adopted (``store.is_ready``), we're done — no build needed.
        3. Otherwise spawn ``rebuild()`` on a worker thread as a tracked
           background ``asyncio.Task`` (module global ``_build_task``) so the
           lifespan can return immediately; the task's own exception handling
           logs failures (which ``rebuild()`` has already recorded on the
           store via ``state``/``error_message``) without ever propagating.
    """
    try:
        store = _prepare_store(settings, embedding_function)
    except Exception:
        _log.exception("doc_store: init failed during background startup")
        return

    if store.is_ready:
        return

    global _build_task

    async def _build() -> None:
        try:
            count = await asyncio.to_thread(store.rebuild)
            _log.info("doc_store: background rebuild complete, %d chunks indexed", count)
        except Exception:
            _log.exception("doc_store: background rebuild failed")

    _build_task = asyncio.create_task(_build(), name="doc-store-build")


def clear_doc_store() -> None:
    """Drop the module singleton (lifespan teardown).

    Does NOT cancel an in-flight ``_build_task`` — the task holds its own
    reference to the (now-orphaned) store and will finish or fail on its own;
    cancelling a rebuild mid-ChromaDB-write is not obviously safer.
    """
    global _doc_store, _init_error, _build_task
    _doc_store = None
    _init_error = None
    _build_task = None
