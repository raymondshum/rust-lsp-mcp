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

Read/write coordination (DS-12):
    A fast-path ``is_ready``/``state`` check on the event loop followed by an
    ``await run_sync(store.search)`` on a worker thread leaves a window where
    a concurrent ``rebuild()`` can flip ``state``/``_collection`` out from
    under an in-flight ``search()`` — the two genuinely race on separate
    threads (mcp 1.12.4 dispatches tool requests concurrently and runs sync
    tools inline).  ``DocStore`` closes that window with two locks:
    ``_build_lock`` (serializes whole rebuilds, held for the entire body) and
    ``_read_lock`` (guards only the readiness check + collection snapshot +
    count/query in ``search()``, and the two brief state transitions at the
    start/end of ``_rebuild_locked``).  ``search()`` raises
    :class:`DocStoreNotReady` — a control-flow signal, not an error — instead
    of returning a misleading ``[]``/partial result when it observes the
    store mid-rebuild; ``search_docs`` translates that into ``not_ready``.

Project identity (SW-01):
    Every project gets its own identity (:func:`project_identity`): the
    explicit ``RLM_PROJECT_ID`` when set, else a hash of the resolved
    ``project_root``, its git ``remote.origin.url`` and its root
    ``Cargo.toml``'s ``[package].name``.  The identity is part of the adopt fingerprint and
    of the ChromaDB collection name (:func:`doc_collection_name`), so two
    projects that share one ``/data`` volume, even at the same mount path,
    keep separate collections and never adopt each other's index.

Inter-process start lock (SW-02, KI-13):
    One exclusive ``fcntl.flock`` on ``<chroma_path>/.rlm-doc-store.lock``
    covers the whole decide-and-build critical section: ``PersistentClient``
    construction, the adopt check and, when nothing is adoptable, the
    rebuild.  Two containers starting together on one volume therefore
    serialise: the second waits, then adopts the first one's collection
    instead of racing ChromaDB's schema creation or deleting a live
    collection.  The thread that acquires the flock is the thread that
    releases it, and no code path opens a second descriptor on the lock
    file while it holds the first (``flock`` binds to the open file
    description, so a second descriptor in the same process would block on
    the first).  A ``rebuild()`` called on a live store (``refresh``) takes
    the store's ``_build_lock`` first and then its own flock.

Startup split (DS-08):
    ``init_doc_store`` (synchronous) runs :func:`_prepare_store` on the
    calling thread: take the flock, construct the store, set the module
    singleton, attempt to adopt an already-built on-disk collection and,
    only if nothing was adopted, rebuild under the same flock.
    ``init_doc_store_background`` instead runs the whole of
    ``_prepare_store`` (flock wait included) on a worker thread inside a
    tracked ``asyncio.Task``, so the FastMCP lifespan can ``yield`` (start
    serving ``status``/navigation tools) without waiting for another
    process's build or for the embedding pass.  While that worker waits on
    the flock the singleton is ``None`` and ``doc_store_state()`` reports
    ``"building"``; once it publishes the store, ``get_doc_store()`` /
    ``doc_store_state()`` observe the in-flight store.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import logging
import os
import pathlib
import subprocess
import threading
import tomllib
from collections.abc import Iterator
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


class DocStoreNotReady(Exception):
    """Raised by :meth:`DocStore.search` when the store is not in a queryable state.

    This is a CONTROL-FLOW SIGNAL, not an error: it means "the readiness check
    (or an atomic snapshot taken immediately after it) found the store mid-rebuild
    or without a collection yet" — i.e. exactly the race DS-12 closes.  Callers
    (``search_docs``) must translate this into a ``not_ready`` envelope, distinct
    from a genuine exception (which maps to ``error``).
    """


# Lock file guarding the decide-and-build critical section across processes
# (SW-02).  Created next to the ChromaDB files and never deleted: the kernel
# drops the flock when the holder's descriptor closes or the process exits.
_LOCK_FILENAME = ".rlm-doc-store.lock"

# A wait on the lock longer than this is logged once at info.
_LOCK_WAIT_LOG_SECONDS = 2.0

# Timeout for the ``git config`` call in project_identity().
_GIT_TIMEOUT_SECONDS = 5.0


def _git_remote_url(project_root: pathlib.Path) -> str:
    """Return ``remote.origin.url`` for *project_root*, or ``""`` on any failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "config", "--get", "remote.origin.url"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _root_package_name(project_root: pathlib.Path) -> str:
    """Return ``[package].name`` of ``<project_root>/Cargo.toml``, or ``""``.

    Empty when the file is absent or unreadable, when ``tomllib`` cannot parse
    it, or when it has no ``[package]`` table (a workspace-only root) or no
    string ``name``.  Only the name is used, never the file bytes, so a
    version bump or a dependency edit does not change the project identity
    (which would rename the collection, force a full re-embed and orphan the
    old collection on the volume).
    """
    try:
        with (project_root / "Cargo.toml").open("rb") as fh:
            manifest = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return ""
    package = manifest.get("package")
    if not isinstance(package, dict):
        return ""
    name = package.get("name")
    return name if isinstance(name, str) else ""


def project_identity(settings: Settings) -> str:
    """Return the identity of the target project for the doc index (SW-01).

    ``settings.project_id`` is stripped of surrounding whitespace first; when
    the stripped value is non-empty it is the identity (empty or
    whitespace-only counts as unset).  Otherwise the identity is the first 16
    hex characters of the SHA-256 over three newline-joined parts: the
    resolved ``project_root``, the output of ``git -C <project_root> config
    --get remote.origin.url`` (empty on any failure, 5-second timeout) and the
    root package name (``[package].name`` of ``<project_root>/Cargo.toml``,
    see :func:`_root_package_name`).

    The derived identity collides for two different projects that share the
    mount path, share the git remote (or both have none) and have the same
    root package name (or both have none); ``RLM_PROJECT_ID`` tells those
    apart.
    """
    explicit = (settings.project_id or "").strip()
    if explicit:
        return explicit
    root = pathlib.Path(settings.project_root).resolve()
    remote = _git_remote_url(root)
    package_name = _root_package_name(root)
    digest = hashlib.sha256("\n".join([str(root), remote, package_name]).encode())
    return digest.hexdigest()[:16]


def _collection_name_for(settings: Settings, identity: str) -> str:
    """Return the ChromaDB collection name for *identity* (see doc_collection_name)."""
    suffix = hashlib.sha256(identity.encode()).hexdigest()[:12]
    return f"{settings.doc_collection}_{suffix}"


def doc_collection_name(settings: Settings, identity: str | None = None) -> str:
    """Return the live ChromaDB collection name for *settings*' project.

    ``settings.doc_collection`` is only the prefix: the server appends ``_``
    and 12 hex characters of the SHA-256 of the project identity, so the name
    is always a valid ChromaDB identifier whatever ``RLM_PROJECT_ID`` holds,
    and projects sharing one ``chroma_path`` get separate collections.  Pass
    *identity* when the caller already computed it, to avoid a second
    ``git`` call.  Tests read the live name through this helper too.
    """
    if identity is None:
        identity = project_identity(settings)
    return _collection_name_for(settings, identity)


def _project_fingerprint(settings: Settings, identity: str | None = None) -> str:
    """Return ``<resolved project_root>|<project identity>`` for *settings*.

    The path is resolved to an absolute, symlink-free form so different
    spellings of the same directory (relative vs absolute, trailing slash,
    symlink hops) match.  Stored in collection metadata under
    ``project_root`` at build time (DS-05, SW-01) so a collection built for
    one project is never silently adopted by a server pointed at another,
    including another project mounted at the same path.
    """
    if identity is None:
        identity = project_identity(settings)
    return f"{pathlib.Path(settings.project_root).resolve()}|{identity}"


@contextlib.contextmanager
def _doc_store_flock(chroma_path: str) -> Iterator[None]:
    """Hold the exclusive inter-process doc-store lock for the ``with`` body.

    Creates ``chroma_path`` first (the lock now runs before
    ``PersistentClient``, which used to be what created it), then opens
    ``<chroma_path>/.rlm-doc-store.lock`` with ``O_CREAT | O_RDWR`` and takes
    ``fcntl.flock(LOCK_EX)`` on it.  No timeout: the kernel releases the lock
    when the holder exits.  A wait longer than two seconds is logged once at
    info.  The calling thread acquires and releases; closing the descriptor
    on exit releases the lock.
    """
    lock_dir = pathlib.Path(chroma_path)
    lock_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_dir / _LOCK_FILENAME, os.O_CREAT | os.O_RDWR, 0o666)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            timer = threading.Timer(
                _LOCK_WAIT_LOG_SECONDS,
                _log.info,
                args=("doc_store: waiting for the doc-store lock held by another process",),
            )
            timer.daemon = True
            timer.start()
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            finally:
                timer.cancel()
        yield
    finally:
        # Closing the only descriptor on this open file description releases
        # the flock (explicit unlock first for clarity).
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class DocStore:
    """ChromaDB-backed documentation search store.

    Args:
        settings: Runtime settings (``chroma_path``, ``project_root``,
            ``doc_collection``, ``project_id``, ``doc_glob_patterns`` are the
            relevant fields).
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
        # Two distinct locks (DS-12):
        #   _build_lock — guards rebuild() against concurrent invocation (e.g.
        #       the DS-08 background build task racing a synchronous refresh()
        #       rebuild).  Held for the ENTIRE rebuild (long: globbing,
        #       chunking, embedding, batch-add).
        #   _read_lock — guards the _state/_collection reads in search() and
        #       their transitions in _rebuild_locked().  Held only BRIEFLY: by
        #       search() across its readiness-check + snapshot + count/query,
        #       and by _rebuild_locked() only around its two brief start/end
        #       transitions (not around the long build body).
        # Ordering/no-deadlock: rebuild() always acquires _build_lock (outer)
        # before _read_lock (inner, and only briefly); search() acquires only
        # _read_lock and never _build_lock.  Since no code path acquires
        # _read_lock first and then blocks on _build_lock, there is no cycle
        # and thus no deadlock.
        self._build_lock = threading.Lock()
        self._read_lock = threading.Lock()

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

    def chunk_count(self) -> int | None:
        """Return the collection's live ``count()``, or ``None`` if not ``"ready"``.

        Deliberately queries the collection directly rather than returning a
        value captured during a build — that is what makes it work on BOTH
        the ``rebuild()`` path (populated or intentionally-empty corpus) AND
        the DS-24 adopt path (an existing collection adopted without ever
        running ``_rebuild_locked`` in this process). An intentionally-empty
        adopted corpus (DS-24: ready, ``build_complete``, 0 rows) correctly
        reports ``0``, not ``None`` — only "not ready" (no collection yet, or
        state != ``"ready"``) reports ``None``.

        Lock choice (DS-12): only the readiness check + collection snapshot
        is taken under ``_read_lock`` (briefly); the ``collection.count()``
        Chroma/SQLite call itself happens OUTSIDE the lock — unlike
        ``search()``, which holds ``_read_lock`` across its own
        ``count()``+``query()``. This is a deliberate trade for this accessor:
        it is called on every ``status`` poll (a frequent, low-stakes read),
        so holding ``_read_lock`` across the Chroma call would serialize
        every poll behind a concurrent rebuild's brief start/end transitions.
        Reading a possibly-stale/wrong count on the rare occasion a rebuild's
        destructive start transition lands between the snapshot and the
        ``count()`` call is the better trade than adding that contention. If
        the snapshotted collection object is invalidated by such a race
        before ``count()`` returns, the exception is swallowed and ``None``
        is reported rather than propagating.
        """
        with self._read_lock:
            if self._state != DOC_STATE_READY or self._collection is None:
                return None
            collection = self._collection
        try:
            return collection.count()
        except Exception:
            return None

    def rebuild(self, *, lock_held: bool = False) -> int:
        """Wholesale rebuild: drop + recreate the collection, re-index every
        matching ``*.md``.  Flips ``state`` to ``"building"`` during the rebuild
        and to ``"ready"`` on completion (or ``"error"`` on failure, re-raising
        the exception).  Returns the number of chunks indexed.

        Synchronous/blocking — callers that must not block the event loop run it
        via a worker thread (e.g. ``anyio.to_thread.run_sync`` or
        ``asyncio.to_thread``).

        Args:
            lock_held: ``False`` (every caller except ``_prepare_store``) takes
                the in-process ``_build_lock`` first, so a concurrent caller
                cannot interleave with an in-flight rebuild, and then the
                inter-process doc-store flock for the duration of the build,
                released afterwards.  ``True`` means the calling thread already
                holds both ``_build_lock`` and the flock (``_prepare_store``'s
                decide-and-build section); neither is taken again.
        """
        if lock_held:
            return self._rebuild_recording_errors(acquire_flock=False)
        with self._build_lock:
            return self._rebuild_recording_errors(acquire_flock=True)

    def _rebuild_recording_errors(self, *, acquire_flock: bool) -> int:
        """Run ``_rebuild_locked`` (under the flock if asked), recording a failure.

        Must only be called while holding ``_build_lock``.
        """
        try:
            if acquire_flock:
                with _doc_store_flock(self._settings.chroma_path):
                    return self._rebuild_locked()
            return self._rebuild_locked()
        except Exception as exc:
            self._state = DOC_STATE_ERROR
            self._error = f"{type(exc).__name__}: {exc}"
            raise

    def _rebuild_locked(self) -> int:
        """The actual rebuild body.

        Must only be called while holding ``_build_lock`` and the doc-store
        flock.
        """
        # Computed once up front; the identity names the collection and is
        # stamped into its metadata (with the fingerprint) below so a later
        # init_doc_store() can refuse to adopt a collection built for a
        # different project (DS-05, SW-01).
        identity = project_identity(self._settings)
        collection_name = doc_collection_name(self._settings, identity)
        project_fingerprint = _project_fingerprint(self._settings, identity)
        build_metadata = {
            "build_complete": True,
            "project_root": project_fingerprint,
            "project_identity": identity,
        }

        # --- Start transition (DS-12): under _read_lock, briefly -----------
        # Mark not ready at the very start — never ready mid-build.  Clearing
        # _error here (rather than only on success) means a rebuild that is
        # itself interrupted mid-flight (e.g. process killed) leaves the
        # store honestly "building" rather than replaying a stale error from
        # a previous failed attempt.  The delete is INSIDE this locked
        # section (not after it) so no in-flight search() can be mid-query on
        # the collection being deleted: search() holds _read_lock across its
        # own count()+query(), so the delete here waits for it to finish, and
        # any search() that starts after this section observes
        # state=BUILDING/collection=None and raises DocStoreNotReady instead
        # of racing a half-deleted collection.
        with self._read_lock:
            self._state = DOC_STATE_BUILDING
            self._error = None
            self._collection = None

            # Drop existing collection if present.
            try:
                self._client.delete_collection(collection_name)
                _log.debug("doc_store: deleted existing collection %r", collection_name)
            except NotFoundError:
                # Expected on first build.
                pass
            except Exception as exc:
                _log.debug("doc_store: delete_collection raised %r (ignored)", exc)

        # --- Long section: NO lock held --------------------------------
        # create_collection into a LOCAL variable (never assigned to
        # self._collection until the end transition below) so any
        # concurrent state-check race is doubly safe: self._collection stays
        # None for the whole build, not just until create_collection returns.
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
            # Write completion sentinel so the adopt branch (DS-24) recognises
            # an intentionally-empty corpus as a completed (not interrupted)
            # build and ADOPTS it on the next startup instead of rebuilding
            # every time — the adopt gate no longer requires count() > 0 (a
            # count-0 collection can never satisfy that), it relies solely on
            # this sentinel + the project_root fingerprint below. project_root
            # and project_identity are stamped alongside it (DS-05, SW-01) —
            # modify() REPLACES metadata wholesale (verified against chromadb
            # 1.5.9), so all keys must be written together in one call.
            collection.modify(metadata=build_metadata)
            # --- End transition (empty-corpus path): under _read_lock -----
            with self._read_lock:
                self._collection = collection
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
        # adopt a silently-partial collection.  project_root and project_identity
        # are stamped alongside it (DS-05, SW-01) so a later init_doc_store() can
        # detect cross-project reuse.
        collection.modify(metadata=build_metadata)

        # --- End transition (DS-12): under _read_lock, briefly -------------
        # Only mark ready after ALL adds AND the sentinel are written, and only
        # publish self._collection now — never earlier — so no state-check
        # race during the (unlocked) build above can observe a half-built
        # collection.
        with self._read_lock:
            self._collection = collection
            self._state = DOC_STATE_READY
        return total

    def search(self, query: str, n_results: int = 5) -> list[dict[str, Any]]:
        """Return up to ``n_results`` best-matching chunks, best-first.

        Each result dict has EXACTLY this shape (other agents depend on it)::

            {
                "file":       str,    # workspace-relative path of the source .md
                "breadcrumb": str,    # e.g. "GUIDE.md > Configuration > Ignoring files"
                "text":       str,    # the chunk BODY — breadcrumb prefix stripped
                "distance":   float,  # cosine distance (0 = identical), lower = closer
            }

        ``text`` is body-only: the embedded document is ``breadcrumb + "\\n\\n" +
        body``, and that prefix is stripped here at read time so the breadcrumb
        is not shipped twice (once structured, once as a text prefix).  For a
        header-only chunk (no body — the embedded document equals the
        breadcrumb verbatim), ``text`` is ``""`` rather than the breadcrumb
        again, keeping the "text = body-only" contract consistent across all
        chunks.

        Returns an empty list only if the collection is empty.  (Semantic search
        over a non-empty collection always returns the top-k nearest neighbours.)

        Raises:
            DocStoreNotReady: the store is not ``"ready"`` (or has no collection
                yet) at the moment this call takes its atomic snapshot.  This is
                the DS-12 fix: the readiness check, the collection snapshot, and
                the count()+query() calls all happen while holding
                ``_read_lock``, so a concurrent rebuild's destructive start
                transition (which also takes ``_read_lock``, briefly) cannot
                interleave with this method mid-query — it either happens
                entirely before this call starts, or waits for this call to
                finish.  Callers (``search_docs``) must translate this into a
                ``not_ready`` envelope, not treat it as "no results".
        """
        with self._read_lock:
            if self._state != DOC_STATE_READY or self._collection is None:
                raise DocStoreNotReady()
            collection = self._collection
            count = collection.count()
            if count == 0:
                return []

            # Clamp n_results to the number of available documents.
            effective_n = min(n_results, count)

            result = collection.query(
                query_texts=[query],
                n_results=effective_n,
            )

        # Map row 0 of each list-of-lists into the documented shape — pure
        # dict work, done OUTSIDE the lock.
        docs_row = result.get("documents", [[]])[0]
        metas_row = result.get("metadatas", [[]])[0]
        dists_row = result.get("distances", [[]])[0]

        output: list[dict[str, Any]] = []
        for doc_text, meta, dist in zip(docs_row, metas_row, dists_row, strict=False):
            breadcrumb = meta.get("breadcrumb", "")
            # Strip the "{breadcrumb}\n\n" prefix so text is body-only — the
            # breadcrumb already ships as its own structured field and
            # otherwise doubles up (UR-14).  Guarded rather than a blind
            # slice: a header-only chunk's embedded text is the breadcrumb
            # verbatim (no "\n\n", no body), so it does not match the
            # startswith check and correctly degrades to "" instead of a
            # silently truncated/garbage slice.
            prefix = breadcrumb + "\n\n"
            text = doc_text[len(prefix) :] if doc_text.startswith(prefix) else ""
            output.append(
                {
                    "file": meta.get("file", ""),
                    "breadcrumb": breadcrumb,
                    "text": text,
                    "distance": float(dist),
                }
            )

        return output


# ---------------------------------------------------------------------------
# Module-level singleton — set during lifespan startup, cleared on exit.
# These accessors are FINAL (agent B implements DocStore, not these).
# ---------------------------------------------------------------------------

_doc_store: DocStore | None = None

# Set when _prepare_store fails before the DocStore exists (taking the
# doc-store lock, or constructing the store itself, e.g. a bad chroma_path);
# read by doc_store_state() when there is no store singleton to
# ask.  Cleared at the start of every _prepare_store call and by
# clear_doc_store().
_init_error: str | None = None

# The background startup task spawned by init_doc_store_background() (it runs
# the whole of _prepare_store on a worker thread), or None if none was started.
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
    task's ``_prepare_store`` call is still waiting on the doc-store lock or
    constructing it) or if lock acquisition or construction itself failed.

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


def doc_index_chunk_count() -> int | None:
    """Return the doc store's current chunk count, or ``None`` if unavailable.

    Mirrors :func:`doc_store_state` — usable even before the singleton exists
    (returns ``None`` in that case, same as "not ready"). See
    :meth:`DocStore.chunk_count` for what ``None`` vs ``0`` means and the
    DS-12 lock-safety rationale for reading it without holding a lock across
    the underlying Chroma call.
    """
    if _doc_store is None:
        return None
    return _doc_store.chunk_count()


def _try_adopt(
    store: DocStore,
    settings: Settings,
    embedding_function: Any | None,
    identity: str | None = None,
) -> bool:
    """Attempt to adopt an existing, fully-built ChromaDB collection onto *store*.

    Cheap (no embedding work): a single ``get_collection`` metadata check.
    "Build once" persistence: if the ChromaDB collection was built for the
    SAME project (its ``project_root`` fingerprint and its
    ``project_identity`` both equal the current values — DS-05, SW-01) and
    carries the build-complete sentinel, adopt it (``store._collection`` and
    ``store._state = DOC_STATE_READY``) and return ``True`` WITHOUT
    re-embedding.  Returns ``False`` on any failure to adopt (not found,
    interrupted build, cross-project mismatch, or an unexpected exception) —
    mirroring the original ``init_doc_store``'s fall-through-to-rebuild
    behaviour exactly.

    DS-24: the adopt gate deliberately does NOT require ``count() > 0``.
    ``build_complete`` is written ONLY at successful ``rebuild()`` completion
    (both the empty-corpus path and the populated path — see
    ``_rebuild_locked``), so the sentinel alone (plus the project fingerprint
    and identity) is the reliable "complete + same project" marker; an
    interrupted build (rows written, process killed before the sentinel)
    still correctly lacks the sentinel and falls through to rebuild below
    regardless of row count.  Requiring ``count() > 0`` would make an
    intentionally-empty completed corpus (0 markdown files matched the glob)
    impossible to ever adopt — it would rebuild on every single startup even
    though nothing changed.

    Args:
        store: The just-constructed :class:`DocStore` to adopt onto.
        settings: Runtime settings (for the collection name / fingerprinting).
        embedding_function: Same embedding function passed to ``DocStore()``;
            forwarded to ``get_collection`` so an adopted collection queries
            with the SAME EF it was built with.
        identity: The project identity, when the caller already computed it.
    """
    if identity is None:
        identity = project_identity(settings)
    current_fingerprint = _project_fingerprint(settings, identity)
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
        existing = store._client.get_collection(
            doc_collection_name(settings, identity), **get_kwargs
        )
        meta = existing.metadata or {}
        existing_project_root = meta.get("project_root")
        existing_identity = meta.get("project_identity")
        build_complete = bool(meta.get("build_complete"))
        same_project = (
            existing_project_root == current_fingerprint and existing_identity == identity
        )
        if build_complete and same_project:
            _log.info(
                "doc_store: adopting existing collection (%d chunks) — skipping rebuild",
                existing.count(),
            )
            # Publish collection+state atomically under _read_lock (DS-12),
            # uniform with rebuild's start/end transitions.  _prepare_store
            # publishes the singleton BEFORE calling _try_adopt, and searches
            # run on other threads, so this adopt IS concurrently reachable —
            # the lock makes the two writes atomic w.r.t. an in-flight
            # search()'s snapshot rather than relying on statement-order-
            # under-GIL.
            with store._read_lock:
                store._collection = existing
                store._state = DOC_STATE_READY
            return True
        if build_complete:
            # Sentinel present but built for a different project (or a
            # pre-SW-01 collection with no project_identity at all, which
            # compares unequal and is safely treated the same way — it just
            # triggers a rebuild instead of silently adopting an unidentified
            # collection).
            _log.info(
                "doc_store: collection was built for project_root=%r, project_identity=%r"
                " but current is %r, %r — rebuilding",
                existing_project_root,
                existing_identity,
                current_fingerprint,
                identity,
            )
        elif existing.count() > 0:
            _log.info(
                "doc_store: existing collection has %d chunks but missing build_complete sentinel"
                " (interrupted build) — will rebuild",
                existing.count(),
            )
        # count() == 0 with no sentinel (never built, or an interrupted build
        # that was killed before writing any rows) also falls through to
        # rebuild.
        return False
    except NotFoundError:
        # No existing collection — will rebuild below.
        return False
    except Exception as exc:
        _log.debug("doc_store: get_collection check raised %r — will rebuild", exc)
        return False


def _prepare_store(settings: Settings, embedding_function: Any | None = None) -> DocStore:
    """Run the whole decide-and-build critical section under the doc-store flock.

    Sequence, all on the calling thread and all under ONE exclusive
    acquisition of ``<chroma_path>/.rlm-doc-store.lock`` (SW-02):

    1. Construct the :class:`DocStore` (and with it the ``PersistentClient``).
    2. Take the new store's in-process ``_build_lock`` BEFORE publishing it
       as the singleton, so no other thread can hold that ``_build_lock``
       while this thread holds the flock.
    3. Publish the singleton (DS-14: ``get_doc_store()`` /
       ``doc_store_state()`` observe the in-flight ``"building"`` store
       rather than ``None``).
    4. Adopt a completed same-project collection when one exists; otherwise
       ``rebuild(lock_held=True)`` under the locks already held.

    Both locks are released when this returns or raises.  A second process
    (another container on the same volume) waits on the flock, then adopts
    the collection the first one built instead of rebuilding it.

    Raises whatever lock acquisition, :class:`DocStore` construction or the
    rebuild raises.  A failure before the store exists is recorded in the
    module-level ``_init_error`` for ``doc_store_state()`` to report; a
    rebuild failure is recorded on the (published) store itself.  Callers
    are responsible for catching it.
    """
    global _doc_store, _init_error
    _init_error = None
    store: DocStore | None = None
    try:
        with _doc_store_flock(settings.chroma_path):
            identity = project_identity(settings)
            _log.info(
                "doc_store: project identity %s, collection %s",
                identity,
                doc_collection_name(settings, identity),
            )
            store = DocStore(settings, embedding_function=embedding_function)
            with store._build_lock:
                _doc_store = store
                if not _try_adopt(store, settings, embedding_function, identity):
                    _log.info("doc_store: no usable existing collection — rebuilding")
                    count = store.rebuild(lock_held=True)
                    _log.info("doc_store: rebuild complete, %d chunks indexed", count)
    except Exception as exc:
        if store is None:
            _init_error = f"{type(exc).__name__}: {exc}"
        raise
    return store


def init_doc_store(settings: Settings, embedding_function: Any | None = None) -> DocStore:
    """Construct the :class:`DocStore`, build it (if not adopted), return it.

    Synchronous/blocking — used by tests and by ``refresh`` (which already
    runs off the event loop via ``run_sync``).  Not used directly by lifespan
    startup; see ``init_doc_store_background`` for the non-blocking entry
    point.  Runs :func:`_prepare_store` on the calling thread, so it may wait
    on the inter-process doc-store lock while another process builds.

    "Build once" persistence: if the ChromaDB collection already exists, was
    built for the SAME project (DS-05/SW-01 identity check), and carries
    the build-complete sentinel, adopt it and mark ready WITHOUT re-embedding
    — this includes an intentionally-empty completed corpus (DS-24: the
    sentinel, not row count, is what marks a build "complete"; see
    ``_try_adopt``).  Otherwise rebuild from scratch.  This avoids
    re-embedding on every server restart when the bind-mount data is already
    populated, while refusing to silently serve a previous project's docs when
    the server is pointed at a different project on the same ``chroma_path``
    (the documented shared-volume, repo-agnostic flow).

    Args:
        settings: Runtime settings.
        embedding_function: Optional ChromaDB embedding function forwarded to
            the underlying :class:`DocStore` AND to the adopt-path
            ``get_collection`` call.  ``None`` (production default) means "use
            ChromaDB's default EF" — tests inject a deterministic fake so the
            real adopt gate, interrupted-build fallback, and singleton
            assignment run fully offline (DS-06).
    """
    return _prepare_store(settings, embedding_function)


async def init_doc_store_background(
    settings: Settings, embedding_function: Any | None = None
) -> None:
    """Non-blocking startup entry point (DS-08): prepare and build on a worker thread.

    Never raises — this is called directly from the FastMCP lifespan, which
    must not fail startup over a doc-index problem (nav tools are unaffected).
    Any failure is logged and swallowed; ``doc_store_state()`` /
    ``get_doc_store()`` remain the source of truth for callers that care.

    Returns without touching the doc-store lock: the whole of
    :func:`_prepare_store` (flock wait, construction, adopt check, rebuild)
    runs via ``asyncio.to_thread`` inside a tracked background
    ``asyncio.Task`` (module global ``_build_task``), so a wait on another
    process's build never freezes MCP ``initialize`` or the analyzer task.
    While the worker waits on the flock the singleton is ``None`` and
    ``doc_store_state()`` reports ``"building"``.  The task's own exception
    handling logs a failure (already recorded in ``_init_error`` or on the
    store's ``state``/``error_message``) without ever propagating.
    """
    global _build_task, _init_error
    # Drop a stale construction error from an earlier attempt so the window
    # before the worker starts reports "building", not the old error.
    _init_error = None

    async def _build() -> None:
        try:
            store = await asyncio.to_thread(_prepare_store, settings, embedding_function)
        except Exception:
            _log.exception("doc_store: background startup failed")
            return
        _log.info("doc_store: background startup complete (state=%s)", store.state)

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
