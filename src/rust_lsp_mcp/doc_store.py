"""Documentation RAG store over ChromaDB (Phase 5).

INTERFACE CONTRACT (orchestrator-owned stub).
=============================================
This file defines the public surface that the ``search_docs`` tool and the
``refresh`` tool code against.  Wave-2 agent **B** replaces the
``NotImplementedError`` bodies with the real ChromaDB implementation; the
singleton accessors below are already final and must keep their signatures.

The store wraps a ChromaDB ``PersistentClient`` (cosine collection) over the
chunks produced by :func:`rust_lsp_mcp.doc_chunking.chunk_markdown`.  It is
built once at lifespan startup and rebuilt by ``refresh`` on a background
thread.

Readiness contract (load-bearing — mirrors the analyzer's readiness gate):
    ``state`` is one of ``"building"`` / ``"ready"`` / ``"error"``; ``is_ready``
    (kept for backward compatibility) is ``True`` only when ``state ==
    "ready"``.  ``search_docs`` gates on this so a caller never receives a
    misleading empty/partial answer, and surfaces a permanent failure
    (``"error"``) distinctly from a transient one (``"building"``).

Build-and-swap (SW-10):
    A rebuild writes into a staging collection named ``<live name>_staging``
    while the store keeps serving the live collection, so ``state`` stays
    ``"ready"`` through a rebuild over an existing index; only the first build
    on an empty store reads ``"building"``.  On success the live collection is
    deleted and the staging one renamed to the live name and published, under
    ``_read_lock``; on failure (or cancellation at shutdown) the staging
    collection is deleted and the live one keeps answering, with
    ``error_message`` set.  Every chunk carries the SHA-256 of its file's
    bytes (``file_sha256``); a file whose hash matches the live collection's
    has its chunks and embeddings copied into staging instead of re-embedded.
    :meth:`DocStore.progress` reports the rebuild's phase and file counts.

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

    A ``refresh`` in one container deletes the collection another container
    on the same volume is serving (KI-13).  When ``search()`` or
    ``chunk_count()`` gets ``NotFoundError`` from the snapshotted collection,
    the store re-opens the live collection by name and serves it when it
    carries ``build_complete``.  Otherwise it probes the flock without
    blocking: held means a rebuild is mid-swap, so the call reads not ready
    and the state is unchanged; free means the collection is gone, so the
    store reads ``"error"`` with a message naming ``refresh``.

Doc index freshness (SW-04):
    :func:`doc_store_files` is the one place that selects the files to index
    (the glob patterns, de-duplication, ``doc_exclude_patterns``, and the
    SW-L21 skip of symlinks and of files resolving outside ``project_root``);
    no other function globs the doc patterns.  A successful rebuild stamps the
    collection with ``indexed_commit`` (git ``HEAD`` of ``project_root``) and
    ``doc_digest`` (:func:`doc_store_digest`, over the same file list), so
    :func:`_try_adopt` refuses an index built on another branch or from other
    file contents, and :func:`doc_store_freshness` reports whether the ready
    index still matches the tree.

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

Shutdown (SW-L14):
    :func:`clear_doc_store` sets the cancel flag of every store whose rebuild
    is running (started by the startup task or by a ``refresh`` worker) and
    waits up to 10 seconds for it to stop; the rebuild checks the flag between
    files and before each embedding batch (at most one file's chunks) and
    abandons its staging collection.  It also bumps a shutdown generation,
    which workers capture before they start: :func:`_prepare_store` checks it
    after the flock and again, under ``_running_lock``, when it publishes the
    singleton, and ``rebuild()`` checks it at registration, so a worker
    interrupted anywhere before its publish or rebuild does neither.
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
import time
import tomllib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any, cast

import chromadb
from chromadb.api.types import Metadatas
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import NotFoundError

from rust_lsp_mcp.doc_chunking import CHUNKER_VERSION, chunk_markdown, drop_junk_lines
from rust_lsp_mcp.settings import Settings

_log = logging.getLogger(__name__)

_ADD_BATCH_SIZE = 500

# Doc-store readiness tri-state — mirrors the analyzer's STATE_* constants.
DOC_STATE_BUILDING = "building"
DOC_STATE_READY = "ready"
DOC_STATE_ERROR = "error"


# Suffix of the collection a rebuild writes before swapping it in (SW-10).
_STAGING_SUFFIX = "_staging"

# Progress is logged at info every this many files (T04 step 3).
_PROGRESS_LOG_EVERY = 50

# clear_doc_store() waits this long for a running rebuild to stop (SW-L14).
_SHUTDOWN_WAIT_SECONDS = 10.0

# Model name stamped for ChromaDB's bundled DefaultEmbeddingFunction (SW-L13).
_DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Rebuild phases reported by DocStore.progress().
PHASE_STARTING = "starting"
PHASE_SCANNING = "scanning"
PHASE_EMBEDDING = "embedding"
PHASE_SWAPPING = "swapping"


class DocRebuildCancelled(Exception):
    """Raised by :meth:`DocStore.rebuild` when shutdown cancelled it (SW-L14).

    The staging collection has been deleted and the live collection is
    intact.  Callers log it at info; it is not a failure of the index.
    """


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

# error_message set when the live collection was deleted by another process
# on the volume and no rebuild is bringing it back (KI-13).
_DELETED_COLLECTION_ERROR = "the doc index collection was deleted by another process on this volume"

# A wait on the lock longer than this is logged once at info.
_LOCK_WAIT_LOG_SECONDS = 2.0

# Timeout for the ``git config`` call in project_identity() and the
# ``git rev-parse HEAD`` call in _doc_head_commit().
_GIT_TIMEOUT_SECONDS = 5.0

# Stamped as indexed_commit when git rev-parse HEAD fails.
_NO_COMMIT = "none"


class ProjectIdentityUnavailable(Exception):
    """The derived project identity could not be computed on this start.

    Raised by :func:`project_identity` when ``git config --get
    remote.origin.url`` timed out (or failed with an ``OSError`` other than
    a missing git binary) twice in a row.  Such a failure can differ between
    two starts, so deriving an identity from it would rename the doc
    collection and orphan the old one; the callers record the error instead
    (``doc_index_state`` ``"error"`` at startup, ``doc_index_error`` after a
    failed ``refresh`` rebuild) and ``refresh`` retries.
    """


def _git_remote_url(project_root: pathlib.Path) -> str | None:
    """Return ``remote.origin.url`` for *project_root*, ``""`` for none, ``None`` on failure.

    ``""`` means a result that is the same on every start: git ran and
    exited with a positive non-zero status (``git config --get`` does for
    an absent key, and git does outside a repository) or the git binary is
    absent.  ``None`` means a failure that can differ between two starts:
    the call timed out, raised another ``OSError`` or git was killed by a
    signal (a negative ``returncode``).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "config", "--get", "remote.origin.url"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return ""
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode < 0:
        # Killed by a signal (for example SIGKILL under memory pressure):
        # transient, so it takes the retry-then-raise path.
        return None
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


def _doc_head_commit(project_root: str) -> str:
    """Return ``git -C <project_root> rev-parse HEAD``, or ``"none"`` on any failure.

    Synchronous with a 5-second timeout; callers run it on a worker thread.
    """
    try:
        result = subprocess.run(
            ["git", "-C", project_root, "rev-parse", "HEAD"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return _NO_COMMIT
    if result.returncode != 0:
        return _NO_COMMIT
    return result.stdout.strip() or _NO_COMMIT


def doc_store_files(settings: Settings) -> list[pathlib.Path]:
    """Return the Markdown files the doc index covers, sorted by path.

    The one function that globs the doc patterns: every
    ``settings.doc_glob_patterns`` entry is globbed under ``project_root``,
    matches are de-duplicated and filtered to regular files, and every path
    a ``settings.doc_exclude_patterns`` glob matches is removed.
    ``_rebuild_locked`` indexes exactly this list and :func:`doc_store_digest`
    hashes exactly this list, so an excluded file never changes the digest.

    SW-L21: a match that is itself a symlink, or whose resolved path lies
    outside the resolved ``project_root`` (reached through a symlinked
    directory), is skipped, so a hostile repository cannot get container
    files such as ``/proc/self/environ`` indexed and served.  The check lives
    here and nowhere else, so the indexer and the digest read the same list
    and a skipped file changing never flips ``doc_stale``.
    """
    src_root = pathlib.Path(settings.project_root)
    root_resolved = src_root.resolve()
    patterns = [p.strip() for p in settings.doc_glob_patterns.split(",") if p.strip()]

    all_files: list[pathlib.Path] = []
    for pattern in patterns:
        all_files.extend(src_root.glob(pattern))

    # Deduplicate (multiple patterns may match same file), drop symlinks and
    # files resolving outside project_root (SW-L21), keep regular files only.
    seen: set[pathlib.Path] = set()
    unique_files: list[pathlib.Path] = []
    for f in all_files:
        if f in seen:
            continue
        seen.add(f)
        if f.is_symlink() or not f.is_file():
            continue
        try:
            resolved = f.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root_resolved):
            _log.debug("doc_store: skipping %s (resolves outside project_root)", f)
            continue
        unique_files.append(f)

    # Compute the exclusion set from doc_exclude_patterns and remove those paths.
    exclude_patterns = [p.strip() for p in settings.doc_exclude_patterns.split(",") if p.strip()]
    if exclude_patterns:
        excluded: set[pathlib.Path] = set()
        for pattern in exclude_patterns:
            excluded.update(src_root.glob(pattern))
        before_count = len(unique_files)
        unique_files = [f for f in unique_files if f not in excluded]
        removed = before_count - len(unique_files)
        _log.debug("doc_store: excluded %d files matching doc_exclude_patterns", removed)

    return sorted(unique_files)


def doc_store_digest(settings: Settings, files: list[pathlib.Path] | None = None) -> str:
    """Return the doc index digest for *settings*' project (SW-04).

    The first 16 hex characters of the SHA-256 over the newline-joined
    ``<relative path>\t<size>\t<mtime_ns>`` lines of every file
    :func:`doc_store_files` returns, in its sorted order.  A file that
    vanishes between the listing and the ``stat`` contributes
    ``<relative path>\t-\t-``.  Pass *files* when the caller already holds
    the :func:`doc_store_files` result (the rebuild does, so the digest
    covers exactly the files it indexes); otherwise the list is computed
    here.  The one function that computes the digest: the rebuild stamp, the
    adopt check and :func:`doc_store_freshness` all call it.
    """
    if files is None:
        files = doc_store_files(settings)
    src_root = pathlib.Path(settings.project_root)
    lines: list[str] = []
    for filepath in files:
        rel_path = filepath.relative_to(src_root).as_posix()
        try:
            st = filepath.stat()
        except OSError:
            lines.append(f"{rel_path}\t-\t-")
            continue
        lines.append(f"{rel_path}\t{st.st_size}\t{st.st_mtime_ns}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


def project_identity(settings: Settings) -> str:
    """Return the identity of the target project for the doc index (SW-01).

    ``settings.project_id`` is stripped of surrounding whitespace first; when
    the stripped value is non-empty it is the identity (empty or
    whitespace-only counts as unset).  Otherwise the identity is the first 16
    hex characters of the SHA-256 over three newline-joined parts: the
    resolved ``project_root``, the output of ``git -C <project_root> config
    --get remote.origin.url`` (5-second timeout; empty when the repo has no
    origin remote, the root is not a git repository or git is not installed)
    and the root package name (``[package].name`` of
    ``<project_root>/Cargo.toml``, see :func:`_root_package_name`).

    A git call that times out, fails with another ``OSError`` or is killed
    by a signal is retried once; a second failure raises :class:`ProjectIdentityUnavailable` rather
    than hashing an empty remote, so a slow git never derives a different
    identity and renames the collection.  An explicit ``RLM_PROJECT_ID``
    makes no git call.

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
    if remote is None:
        remote = _git_remote_url(root)
    if remote is None:
        raise ProjectIdentityUnavailable(
            f"could not read the git remote of {root} (git timed out); set RLM_PROJECT_ID"
            " to a fixed name, or call refresh to retry"
        )
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


def _embedding_model_name(embedding_function: Any | None) -> str:
    """Return the embedding model name stamped on the collection (SW-L13).

    ``None`` (ChromaDB's bundled default) is ``all-MiniLM-L6-v2``; otherwise
    the embedding function's ``MODEL_NAME`` or ``model_name`` attribute when it
    has one (ChromaDB itself only compares ``name()``, which names the
    embedding-function family, not the model), else its ``name()``, falling
    back to its class name.
    """
    if embedding_function is None:
        return _DEFAULT_EMBEDDING_MODEL
    for attr in ("MODEL_NAME", "model_name"):
        model = getattr(embedding_function, attr, None)
        if isinstance(model, str) and model:
            return model
    name_fn = getattr(embedding_function, "name", None)
    if callable(name_fn):
        try:
            name = name_fn()
        except Exception:
            name = None
        if isinstance(name, str) and name:
            return name
    return type(embedding_function).__name__


def _build_settings_stamp(settings: Settings, embedding_function: Any | None) -> dict[str, str]:
    """Return the SW-L13 adopt keys for *settings* and *embedding_function*.

    Stamped on every built collection and compared by :func:`_try_adopt`:
    a change to the doc globs, the exclusions, the chunker or the embedding
    model makes the old chunks unusable.
    """
    return {
        "doc_glob_patterns": settings.doc_glob_patterns,
        "doc_exclude_patterns": settings.doc_exclude_patterns,
        "chunker_version": CHUNKER_VERSION,
        "embedding_model": _embedding_model_name(embedding_function),
    }


def _utc_now_iso() -> str:
    """Return the current time as ISO 8601 UTC with seconds precision."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read_doc_text(data: bytes) -> str:
    """Decode a doc file's bytes as UTF-8 with universal newlines.

    Matches what ``Path.read_text(encoding="utf-8", errors="replace")`` gave
    before the rebuild started hashing bytes: ``\r\n`` and a lone ``\r``
    become ``\n``.
    """
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


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
        # Name of the live collection last published by a build or an adopt,
        # so search()/chunk_count() can re-open it after another process on
        # the volume swapped it (KI-13) without a git call.
        self._live_name: str | None = None
        self._state: str = DOC_STATE_BUILDING
        self._error: str | None = None
        # Two distinct locks (DS-12):
        #   _build_lock — guards rebuild() against concurrent invocation (e.g.
        #       the DS-08 background build task racing a refresh worker's
        #       rebuild).  Held for the ENTIRE rebuild (long: globbing,
        #       chunking, embedding, batch-add).
        #   _read_lock — guards the _state/_error/_collection reads in
        #       search() and state_snapshot() and their transitions in
        #       _rebuild_locked() / _rebuild_recording_errors().  Held only
        #       BRIEFLY: by search() across its readiness-check + snapshot +
        #       count/query, and by the rebuild around its start transition
        #       and its swap (not around the long build body).
        # Ordering/no-deadlock: rebuild() always acquires _build_lock (outer)
        # before _read_lock (inner, and only briefly); search() acquires only
        # _read_lock and never _build_lock.  Since no code path acquires
        # _read_lock first and then blocks on _build_lock, there is no cycle
        # and thus no deadlock.
        self._build_lock = threading.Lock()
        self._read_lock = threading.Lock()
        # Rebuild progress (T04 step 3), guarded by its own small lock so a
        # status poll never waits behind a search holding _read_lock.
        self._progress_lock = threading.Lock()
        self._progress: dict[str, Any] = _idle_progress()
        # Shutdown coordination (SW-L14): clear_doc_store() sets _cancel; the
        # rebuild checks it between files.  _rebuild_idle is set whenever no
        # rebuild() call is running on this store (cleared on entry, set in a
        # finally on exit); _active_rebuilds counts the calls in flight and is
        # guarded by the module-level _running_lock.
        self._cancel = threading.Event()
        self._rebuild_idle = threading.Event()
        self._rebuild_idle.set()
        self._active_rebuilds = 0

    @property
    def is_ready(self) -> bool:
        """``True`` only when a collection is published and queryable.

        Kept for backward compatibility; equivalent to ``state == "ready"``.
        Stays ``True`` during a rebuild over an existing index (SW-10).
        """
        return self._state == DOC_STATE_READY

    @property
    def state(self) -> str:
        """One of ``"building"`` / ``"ready"`` / ``"error"``."""
        return self._state

    @property
    def error_message(self) -> str | None:
        """``"{ExceptionType}: {message}"`` if the last ``rebuild()`` failed, else ``None``.

        Set together with ``state`` under ``_read_lock`` (SW-L23).  A failed
        rebuild over an existing index leaves ``state == "ready"`` (the old
        index keeps answering) with this message set.
        """
        return self._error

    def state_snapshot(self) -> tuple[str, str | None]:
        """Return ``(state, error_message)`` read together under ``_read_lock`` (SW-L23).

        ``status`` reads the pair through this, so it never observes
        ``"error"`` with a ``None`` message mid-transition.
        """
        with self._read_lock:
            return self._state, self._error

    def progress(self) -> dict[str, Any]:
        """Return the rebuild's progress (T04 step 3).

        A new dict with ``files_total`` and ``files_done`` (ints),
        ``started_at`` (ISO 8601 UTC, or ``None`` when idle) and ``phase``:
        ``"starting"`` (registered, waiting for the locks and the project
        identity), ``"scanning"`` (listing and stamping the files), ``"embedding"``
        (chunking, embedding or copying each file), ``"swapping"`` (replacing
        the live collection) or ``None`` when no rebuild is running.
        """
        with self._progress_lock:
            return dict(self._progress)

    def _set_progress(self, **fields: Any) -> None:
        with self._progress_lock:
            self._progress.update(fields)

    def chunk_count(self) -> int | None:
        """Return the collection's live ``count()``, or ``None`` if not ``"ready"``.

        Deliberately queries the collection directly rather than returning a
        value captured during a build — that is what makes it work on BOTH
        the ``rebuild()`` path (populated or intentionally-empty corpus) AND
        the DS-24 adopt path (an existing collection adopted without ever
        running ``_rebuild_locked`` in this process). An intentionally-empty
        adopted corpus (DS-24: ready, ``build_complete``, 0 rows) correctly
        reports ``0``, not ``None`` — only "not ready" (no collection yet, or
        state != ``"ready"``) reports ``None``.  During a rebuild over an
        existing index this is the live (old) collection's count.

        Lock choice (DS-12): only the readiness check + collection snapshot
        is taken under ``_read_lock`` (briefly); the ``collection.count()``
        Chroma/SQLite call itself happens OUTSIDE the lock — unlike
        ``search()``, which holds ``_read_lock`` across its own
        ``count()``+``query()``. This is a deliberate trade for this accessor:
        it is called on every ``status`` poll (a frequent, low-stakes read),
        so holding ``_read_lock`` across the Chroma call would serialize
        every poll behind a concurrent rebuild's brief transitions.  If the
        snapshotted collection object is invalidated by a concurrent swap
        before ``count()`` returns, the exception is swallowed and ``None``
        is reported rather than propagating.
        """
        with self._read_lock:
            if self._state != DOC_STATE_READY or self._collection is None:
                return None
            collection = self._collection
        try:
            return collection.count()
        except NotFoundError:
            pass
        except Exception:
            return None
        # KI-13: another process on the volume deleted the collection.  The
        # re-open and the publish run under _read_lock; the count after it
        # runs outside, as above.
        with self._read_lock:
            if self._collection is not collection:
                # Another thread already replaced (or cleared) the snapshot.
                if self._state != DOC_STATE_READY or self._collection is None:
                    return None
                collection = self._collection
            else:
                try:
                    collection = self._recover_collection_locked()
                except DocStoreNotReady:
                    return None
                except Exception:
                    # An OSError from the flock probe (mkdir, open, flock):
                    # this accessor never raises (status calls it on every poll).
                    _log.warning("doc_store: collection recovery failed", exc_info=True)
                    return None
        try:
            return collection.count()
        except Exception:
            return None

    def rebuild(self, *, lock_held: bool = False, generation: int | None = None) -> int:
        """Rebuild the index into a staging collection and swap it in (SW-10).

        While the rebuild runs the store keeps serving the live collection
        (``state`` stays ``"ready"`` when one is published; the first build on
        an empty store reads ``"building"``).  On success the staging
        collection replaces the live one and ``state`` is ``"ready"``; on
        failure the staging collection is deleted, ``error_message`` is set
        and the exception re-raised, with ``state`` left ``"ready"`` when a
        live collection exists and ``"error"`` otherwise.  Files whose bytes
        hash the same as in the live collection are copied, not re-embedded.
        Returns the number of chunks in the new index.

        Synchronous/blocking — callers that must not block the event loop run it
        on a worker thread.

        Args:
            lock_held: ``False`` (every caller except ``_prepare_store``) takes
                the in-process ``_build_lock`` first, so a concurrent caller
                cannot interleave with an in-flight rebuild, and then the
                inter-process doc-store flock for the duration of the build,
                released afterwards.  ``True`` means the calling thread already
                holds both ``_build_lock`` and the flock (``_prepare_store``'s
                decide-and-build section); neither is taken again.
            generation: the shutdown generation the caller captured (see
                :func:`current_shutdown_generation`).  When a
                :func:`clear_doc_store` has run since, registration sets the
                cancel flag, so the rebuild stops before doing any work.

        Raises:
            DocRebuildCancelled: :func:`clear_doc_store` set the cancel flag
                (SW-L14); the live collection is intact.
        """
        _register_running(self, generation)
        try:
            if lock_held:
                return self._rebuild_recording_errors(acquire_flock=False)
            with self._build_lock:
                return self._rebuild_recording_errors(acquire_flock=True)
        finally:
            _unregister_running(self)

    def _rebuild_recording_errors(self, *, acquire_flock: bool) -> int:
        """Run ``_rebuild_locked`` (under the flock if asked), recording a failure.

        Must only be called while holding ``_build_lock``.  ``state`` and
        ``error_message`` are written together under ``_read_lock`` (SW-L23);
        ``state`` becomes ``"error"`` only when no live collection is
        published.
        """
        try:
            if acquire_flock:
                with _doc_store_flock(self._settings.chroma_path):
                    return self._rebuild_locked()
            return self._rebuild_locked()
        except Exception as exc:
            with self._read_lock:
                self._error = f"{type(exc).__name__}: {exc}"
                if self._collection is None:
                    self._state = DOC_STATE_ERROR
            raise

    def _check_cancelled(self) -> None:
        if self._cancel.is_set():
            raise DocRebuildCancelled("doc index rebuild cancelled at shutdown")

    def _open_copy_source(
        self, live_name: str, identity: str, stamp: dict[str, str]
    ) -> tuple[Any, dict[str, str]]:
        """Return ``(collection, {file: file_sha256})`` to copy unchanged files from.

        The source is the live collection: the published one when it carries
        the live name, else the on-disk collection of that name (for example
        one ``_try_adopt`` refused because the tree moved on).  It is usable
        only when it carries the build-complete sentinel and the same
        project identity, chunker version and embedding model; otherwise, or
        on any read failure (logged at warning), ``(None, {})`` and every file
        is re-embedded.  A file is listed only when all its chunks carry the
        same hash.
        """
        source = self._collection
        try:
            if source is None or getattr(source, "name", None) != live_name:
                get_kwargs: dict[str, Any] = {}
                if self._ef is not None:
                    get_kwargs["embedding_function"] = self._ef
                source = self._client.get_collection(live_name, **get_kwargs)
            meta = source.metadata or {}
            if not (
                meta.get("build_complete")
                and meta.get("project_identity") == identity
                and meta.get("chunker_version") == stamp["chunker_version"]
                and meta.get("embedding_model") == stamp["embedding_model"]
            ):
                return None, {}
            rows = source.get(include=["metadatas"])
        except NotFoundError:
            return None, {}
        except Exception as exc:
            _log.warning(
                "doc_store: cannot read the live collection for copying (%r) —"
                " re-embedding every file",
                exc,
            )
            return None, {}
        hashes: dict[str, str] = {}
        mixed: set[str] = set()
        for row in rows.get("metadatas") or []:
            if not row:
                continue
            file = row.get("file")
            sha = row.get("file_sha256")
            if not isinstance(file, str):
                continue
            if not isinstance(sha, str) or hashes.get(file, sha) != sha:
                mixed.add(file)
                continue
            hashes[file] = sha
        for file in mixed:
            hashes.pop(file, None)
        return source, hashes

    def _rebuild_locked(self) -> int:
        """The actual rebuild body: build into ``<live name>_staging``, then swap.

        Must only be called while holding ``_build_lock`` and the doc-store
        flock.  See the module docstring's build-and-swap section.
        """
        started = time.monotonic()
        # Computed once up front; the identity names the collection and is
        # stamped into its metadata (with the fingerprint) so a later
        # init_doc_store() can refuse to adopt a collection built for a
        # different project (DS-05, SW-01).
        identity = project_identity(self._settings)
        live_name = doc_collection_name(self._settings, identity)
        staging_name = live_name + _STAGING_SUFFIX
        project_fingerprint = _project_fingerprint(self._settings, identity)
        stamp = _build_settings_stamp(self._settings, self._ef)
        self._check_cancelled()
        self._set_progress(phase=PHASE_SCANNING, files_total=0, files_done=0)
        try:
            # --- Start transition (DS-12): under _read_lock, briefly -------
            # The live collection keeps answering when one is published
            # (SW-10).  With none (first build on an empty store) the store
            # reads "building".  _error is cleared either way: a rebuild in
            # flight replaces the previous attempt's outcome.
            with self._read_lock:
                self._error = None
                if self._collection is None:
                    self._state = DOC_STATE_BUILDING

            # Leftover _staging from an interrupted rebuild is discarded first.
            with contextlib.suppress(NotFoundError):
                self._client.delete_collection(staging_name)

            # Note: passing embedding_function=None explicitly disables the default EF
            # in chromadb 1.5.x.  When no custom EF is provided, omit the parameter
            # entirely so create_collection uses its default (all-MiniLM-L6-v2).
            create_kwargs: dict[str, Any] = {
                "configuration": {"hnsw": {"space": "cosine"}},
            }
            if self._ef is not None:
                create_kwargs["embedding_function"] = self._ef
            staging = self._client.create_collection(staging_name, **create_kwargs)

            try:
                return self._fill_staging(
                    staging, live_name, identity, project_fingerprint, stamp, started
                )
            except BaseException:
                # A failed or cancelled rebuild abandons _staging; the live
                # collection (if any) is untouched and keeps answering.
                try:
                    self._client.delete_collection(staging_name)
                except Exception as cleanup_exc:
                    _log.debug("doc_store: deleting staging raised %r (ignored)", cleanup_exc)
                raise
        finally:
            # Back to "starting" while another rebuild() call on this store
            # waits on _build_lock; _unregister_running sets idle at zero.
            self._set_progress(phase=PHASE_STARTING, files_total=0, files_done=0)

    def _fill_staging(
        self,
        staging: Any,
        live_name: str,
        identity: str,
        project_fingerprint: str,
        stamp: dict[str, str],
        started: float,
    ) -> int:
        """Index every file into *staging*, then swap it in as *live_name*."""
        src_root = pathlib.Path(self._settings.project_root)
        unique_files = doc_store_files(self._settings)
        _log.debug("doc_store: found %d markdown files to index", len(unique_files))

        # Freshness stamp (SW-04), taken BEFORE the files are read: a file
        # changed while the build runs then reads as stale on the next check
        # instead of being stamped as indexed.  Written at the swap below.
        build_metadata: dict[str, Any] = {
            "build_complete": True,
            "project_root": project_fingerprint,
            "project_identity": identity,
            "indexed_commit": _doc_head_commit(self._settings.project_root),
            "doc_digest": doc_store_digest(self._settings, unique_files),
            **stamp,
        }

        source, source_hashes = self._open_copy_source(live_name, identity, stamp)

        self._set_progress(phase=PHASE_EMBEDDING, files_total=len(unique_files))
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, str]] = []
        total = 0
        unchanged = 0
        reembedded = 0
        junk_lines = 0
        # Backlog line 63: files whose read raised OSError; the count is
        # stamped as unreadable_files so status reports what is missing.
        unreadable: list[pathlib.Path] = []

        def _flush() -> None:
            # Embedding happens inside add(); one file's chunks at a time, in
            # batches of at most _ADD_BATCH_SIZE, with a cancel check before
            # each embed (SW-L14, R-gate), so a shutdown never waits for the
            # rest of the corpus to embed.
            for batch_start in range(0, len(ids), _ADD_BATCH_SIZE):
                self._check_cancelled()
                batch_end = batch_start + _ADD_BATCH_SIZE
                staging.add(
                    ids=ids[batch_start:batch_end],
                    documents=documents[batch_start:batch_end],
                    metadatas=cast(Metadatas, metadatas[batch_start:batch_end]),
                )
            ids.clear()
            documents.clear()
            metadatas.clear()

        for done, filepath in enumerate(unique_files, start=1):
            self._check_cancelled()
            rel_path = str(filepath.relative_to(src_root))
            try:
                data = filepath.read_bytes()
            except OSError as exc:
                _log.debug("doc_store: could not read %s: %s", filepath, exc)
                unreadable.append(filepath)
                self._set_progress(files_done=done)
                continue
            file_sha = hashlib.sha256(data).hexdigest()

            copied = (
                self._copy_file_chunks(source, staging, rel_path)
                if source is not None and source_hashes.get(rel_path) == file_sha
                else None
            )
            if copied is not None:
                unchanged += 1
                total += copied
            else:
                text, dropped = drop_junk_lines(_read_doc_text(data))
                junk_lines += dropped
                for chunk in chunk_markdown(text, rel_path):
                    ids.append(chunk.id)
                    documents.append(chunk.text)
                    metadatas.append(
                        {
                            "file": chunk.file,
                            "breadcrumb": chunk.breadcrumb,
                            "file_sha256": file_sha,
                        }
                    )
                    total += 1
                reembedded += 1
                _flush()

            self._set_progress(files_done=done)
            if done % _PROGRESS_LOG_EVERY == 0:
                _log.info("doc_store: rebuild progress %d/%d files", done, len(unique_files))

        _flush()
        self._check_cancelled()

        build_metadata["unreadable_files"] = len(unreadable)
        if unreadable:
            _log.warning(
                "doc_store: %d doc files could not be read and are not indexed: %s",
                len(unreadable),
                ", ".join(str(f) for f in unreadable[:5]),
            )

        # --- Swap (SW-10): under _read_lock, briefly -----------------------
        # Delete the live collection, rename staging to the live name with the
        # completion sentinel and stamps (modify() replaces metadata
        # wholesale, verified against chromadb 1.5.9, so every key is written
        # in one call), and publish it.  search() holds _read_lock across its
        # own count()+query(), so no query runs on a half-swapped collection.
        # A hard kill between the delete and the rename leaves no live
        # collection, so the next start rebuilds (it never adopts staging).
        self._set_progress(phase=PHASE_SWAPPING)
        with self._read_lock:
            with contextlib.suppress(NotFoundError):
                self._client.delete_collection(live_name)
            try:
                staging.modify(name=live_name, metadata=build_metadata)
            except BaseException:
                # The old live collection is already gone: stop serving it,
                # so the failure handler reports "error" instead of "ready".
                self._collection = None
                self._state = DOC_STATE_BUILDING
                raise
            self._collection = staging
            self._live_name = live_name
            self._state = DOC_STATE_READY
            self._error = None

        if junk_lines:
            _log.info(
                "doc_store: dropped %d lines over 2000 characters with no whitespace", junk_lines
            )
        _log.info(
            "doc_store: rebuild complete, %d files unchanged, %d re-embedded, %d chunks,"
            " %.1f seconds",
            unchanged,
            reembedded,
            total,
            time.monotonic() - started,
        )
        return total

    @staticmethod
    def _copy_file_chunks(source: Any, staging: Any, rel_path: str) -> int | None:
        """Copy *rel_path*'s chunks and embeddings from *source* into *staging*.

        Returns the number of chunks copied, or ``None`` when the copy failed
        (logged at warning) or found nothing, so the caller re-embeds the file.
        """
        try:
            rows = source.get(
                where={"file": rel_path}, include=["embeddings", "documents", "metadatas"]
            )
            row_ids = rows.get("ids") or []
            embeddings = rows.get("embeddings")
            if not row_ids or embeddings is None or len(embeddings) != len(row_ids):
                return None
            staging.add(
                ids=row_ids,
                embeddings=embeddings,
                documents=rows.get("documents"),
                metadatas=rows.get("metadatas"),
            )
        except Exception as exc:
            _log.warning("doc_store: copying %s from the live index failed (%r)", rel_path, exc)
            return None
        return len(row_ids)

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
                ``not_ready`` envelope, not treat it as "no results".  Also
                raised when another process on the volume deleted the
                collection and it cannot be re-opened (KI-13, see
                :meth:`_recover_collection_locked`); ``state`` then reads
                ``"error"`` unless a rebuild holds the doc-store flock.
        """
        with self._read_lock:
            if self._state != DOC_STATE_READY or self._collection is None:
                raise DocStoreNotReady()
            try:
                result = self._query_locked(self._collection, query, n_results)
            except NotFoundError:
                # KI-13: another process on the volume deleted the collection.
                # Recovery runs inside this _read_lock region (the lock is not
                # reentrant, and nothing it calls takes it again); it raises
                # DocStoreNotReady when the collection cannot be served.
                recovered = self._recover_collection_locked()
                result = self._query_locked(recovered, query, n_results)
            if result is None:
                return []

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

    @staticmethod
    def _query_locked(collection: Any, query: str, n_results: int) -> Any:
        """Run ``count()`` and ``query()`` on *collection*; ``None`` when it is empty.

        Called by :meth:`search` while it holds ``_read_lock``.
        """
        count = collection.count()
        if count == 0:
            return None
        # Clamp n_results to the number of available documents.
        return collection.query(query_texts=[query], n_results=min(n_results, count))

    def _open_live_complete(self) -> Any | None:
        """Re-open the live collection by ``_live_name``; ``None`` unless it is complete.

        Returns the collection only when it exists and carries
        ``build_complete``.  Takes no lock and runs no git call.
        """
        if self._live_name is None:
            return None
        get_kwargs: dict[str, Any] = {}
        if self._ef is not None:
            get_kwargs["embedding_function"] = self._ef
        try:
            collection = self._client.get_collection(self._live_name, **get_kwargs)
        except NotFoundError:
            return None
        except Exception as exc:
            _log.warning("doc_store: re-opening %s failed (%r)", self._live_name, exc)
            return None
        if not (collection.metadata or {}).get("build_complete"):
            return None
        return collection

    def _recover_collection_locked(self) -> Any:
        """Recover from a live collection another process deleted (KI-13).

        Must be called while holding ``_read_lock``; takes no lock of its
        own besides a non-blocking probe of the inter-process flock.  Returns
        the re-opened collection, published as ``_collection``, when it
        exists with ``build_complete``.  Otherwise probes
        ``<chroma_path>/.rlm-doc-store.lock`` with ``LOCK_EX | LOCK_NB``: a
        held flock means a rebuild is mid-swap, so ``_state``, ``_error`` and
        ``_collection`` stay as they are.  A free flock means nothing will
        bring the collection back: after one more re-open under the flock,
        ``_state`` becomes ``"error"`` with ``_DELETED_COLLECTION_ERROR`` as
        its message, which says another process on the volume deleted the
        collection, and ``_collection`` is cleared.

        Raises:
            DocStoreNotReady: no collection can be served now.
            OSError: the flock probe failed (creating ``chroma_path``,
                opening or locking the lock file); the state is unchanged.
                ``chunk_count()`` reports ``None``; ``search()`` lets it
                propagate, and ``search_docs`` maps it to its ``error``
                envelope.
        """
        collection = self._open_live_complete()
        if collection is not None:
            _log.info("doc_store: re-opened %s after another process replaced it", self._live_name)
            self._collection = collection
            return collection
        lock_dir = pathlib.Path(self._settings.chroma_path)
        lock_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_dir / _LOCK_FILENAME, os.O_CREAT | os.O_RDWR, 0o666)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                _log.info(
                    "doc_store: collection %s is missing while another rebuild holds the"
                    " doc-store lock; reporting not ready",
                    self._live_name,
                )
                raise DocStoreNotReady() from None
            try:
                collection = self._open_live_complete()
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        if collection is not None:
            _log.info("doc_store: re-opened %s after another process replaced it", self._live_name)
            self._collection = collection
            return collection
        _log.warning("doc_store: %s (collection %s)", _DELETED_COLLECTION_ERROR, self._live_name)
        self._state = DOC_STATE_ERROR
        self._error = _DELETED_COLLECTION_ERROR
        self._collection = None
        raise DocStoreNotReady()


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

# The background thread started by start_doc_rebuild() (refresh), or None.
_rebuild_thread: threading.Thread | None = None

# Guards _running_stores, each store's _active_rebuilds, _shutdown_generation
# and the publish of the _doc_store singleton by _prepare_store (so a shutdown
# either precedes the publish and stops it, or follows it and drops the
# store).  The check-and-start in start_doc_rebuild() is serialised by the
# refresh tool's per-event-loop asyncio lock, not by this lock.
_running_lock = threading.Lock()

# Every store with a rebuild() call in flight, whichever caller started it
# (the startup task or a refresh worker); clear_doc_store() cancels them.
_running_stores: set[DocStore] = set()

# Bumped by every clear_doc_store().  A worker captures it before it starts
# (current_shutdown_generation()); _prepare_store compares it after the flock
# and again, under _running_lock, when it publishes the singleton, and
# rebuild() compares it at registration, so work that began before a shutdown
# publishes nothing and rebuilds nothing after it (SW-L14).
_shutdown_generation = 0


def current_shutdown_generation() -> int:
    """Return the shutdown generation; capture it before starting doc-store work."""
    with _running_lock:
        return _shutdown_generation


def _idle_progress() -> dict[str, Any]:
    """Return the progress dict of a store with no rebuild running."""
    return {"files_total": 0, "files_done": 0, "started_at": None, "phase": None}


def _register_running(store: DocStore, generation: int | None = None) -> None:
    """Record that a ``rebuild()`` call on *store* has started.

    Sets the progress phase to ``"starting"`` (with ``started_at``) when no
    other call is running, so ``doc_index_progress`` is non-null from this
    moment (step 3, R-gate).  When *generation* is stale (a shutdown ran since
    the caller captured it) the cancel flag is set here, atomically with the
    shutdown's own bookkeeping.
    """
    with _running_lock:
        if generation is not None and generation != _shutdown_generation:
            store._cancel.set()
        store._active_rebuilds += 1
        if store._active_rebuilds == 1:
            store._set_progress(
                phase=PHASE_STARTING, started_at=_utc_now_iso(), files_total=0, files_done=0
            )
        store._rebuild_idle.clear()
        _running_stores.add(store)


def _unregister_running(store: DocStore) -> None:
    """Record that a ``rebuild()`` call on *store* has exited (any outcome)."""
    with _running_lock:
        store._active_rebuilds -= 1
        if store._active_rebuilds <= 0:
            store._active_rebuilds = 0
            store._set_progress(**_idle_progress())
            _running_stores.discard(store)
            store._rebuild_idle.set()


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

    - If the singleton is set, defer to it (:meth:`DocStore.state_snapshot`,
      read under its lock so ``"error"`` never pairs with ``None``, SW-L23).
      During a rebuild over an existing index this is ``"ready"``, and a
      failed rebuild that left the old index serving reads ``"ready"`` with
      the failure's message.
    - Else, if construction previously failed, report ``("error", _init_error)``.
    - Else, report ``("building", None)`` — construction/build has not
      reached the singleton-assignment point yet.
    """
    store = _doc_store
    if store is not None:
        return store.state_snapshot()
    if _init_error is not None:
        return DOC_STATE_ERROR, _init_error
    return DOC_STATE_BUILDING, None


def reported_doc_state(
    snapshot: tuple[str, str | None] | None = None,
) -> tuple[str, str | None]:
    """Return the doc state the tools report: :func:`doc_store_state`, masked.

    Same pair as :func:`doc_store_state` (or as *snapshot*, a pair the caller
    already read from it), with one change: ``("error", <message>)`` becomes
    ``("building", None)`` while :func:`doc_rebuild_in_progress` is true, so
    ``status``, ``refresh`` and ``search_docs`` all report an errored index
    whose ``refresh`` rebuild is in flight as ``"building"`` (backlog line
    117).  Call from the event loop, as :func:`doc_rebuild_in_progress`
    requires.
    """
    state, message = snapshot if snapshot is not None else doc_store_state()
    if state == DOC_STATE_ERROR and doc_rebuild_in_progress():
        return DOC_STATE_BUILDING, None
    return state, message


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


def doc_index_progress() -> dict[str, Any] | None:
    """Return the running doc rebuild's :meth:`DocStore.progress`, or ``None`` (T04).

    ``None`` when no rebuild is running.  Prefers the singleton when it is
    rebuilding, else any other store with a rebuild in flight.
    """
    with _running_lock:
        running = list(_running_stores)
    candidates = [_doc_store] if _doc_store in running else []
    candidates.extend(s for s in running if s is not _doc_store)
    for store in candidates:
        if store is None:
            continue
        progress = store.progress()
        if progress.get("phase") is not None:
            return progress
    return None


def doc_rebuild_in_progress() -> bool:
    """Return ``True`` while any doc index build or rebuild is in flight.

    Covers the startup task (``_build_task`` not done, including its wait on
    the flock), a ``refresh`` worker thread, and any store with a
    ``rebuild()`` call running.  Call from the event loop.
    """
    if _build_task is not None and not _build_task.done():
        return True
    if _rebuild_thread is not None and _rebuild_thread.is_alive():
        return True
    with _running_lock:
        return bool(_running_stores)


def start_doc_rebuild(work: Callable[[], None]) -> bool:
    """Start *work* (a doc rebuild or re-init) on a background thread (T04 step 4).

    Returns ``False`` without starting anything when a build or rebuild is
    already in flight (:func:`doc_rebuild_in_progress`), so a second
    ``refresh`` never queues another rebuild.  Otherwise starts a daemon
    thread, recorded as ``_rebuild_thread``, and returns ``True`` once it has
    started.  *work* must catch and log its own exceptions.  Call from the
    event loop, under the ``refresh`` tool's per-loop lock.
    """
    global _rebuild_thread
    if doc_rebuild_in_progress():
        return False
    thread = threading.Thread(target=work, name="doc-store-rebuild", daemon=True)
    _rebuild_thread = thread
    thread.start()
    return True


def _ready_collection() -> tuple[DocStore, Any] | None:
    """Return ``(store, collection)`` for the ready singleton, else ``None``.

    The readiness check and the snapshot are taken under ``_read_lock``.
    Never raises: a store double without the lock reads as not ready.
    """
    store = _doc_store
    if store is None:
        return None
    try:
        with store._read_lock:
            if store._state != DOC_STATE_READY or store._collection is None:
                return None
            return store, store._collection
    except Exception:
        _log.debug("doc_store: ready-collection snapshot failed", exc_info=True)
        return None


def doc_store_freshness() -> tuple[str | None, bool | None]:
    """Return ``(doc_indexed_commit, doc_stale)`` for the doc index (SW-04).

    ``(None, None)`` when there is no store or it is not ``"ready"``.
    Otherwise the ``indexed_commit`` stamped on the ready collection (``None``
    when an adopted pre-stamp collection lacks it) and ``doc_stale``:
    ``True`` when the stamped ``doc_digest`` differs from
    :func:`doc_store_digest` now (a Markdown file the index covers was added,
    removed or changed since the build; a missing stamp compares unequal),
    else ``False``.  The commit is reported but not compared: a code-only
    branch switch with byte-identical docs leaves ``doc_stale`` ``False``,
    and no git call runs here.

    Synchronous: the digest walk runs on the caller's thread, so async
    callers (``status``, ``search_docs``) run it on a worker thread, never on
    the event loop.  Never raises; an unexpected failure reads as
    ``(None, None)``.
    """
    indexed_commit, stale, _unreadable = doc_store_status_fields()
    return indexed_commit, stale


def doc_store_status_fields() -> tuple[str | None, bool | None, int | None]:
    """Return ``(doc_indexed_commit, doc_stale, doc_unreadable_files)`` from one snapshot.

    All three come from one read of one ready collection's metadata, so a
    swap during the digest walk cannot pair one collection's commit and
    staleness with another's unreadable count.  ``doc_indexed_commit`` and
    ``doc_stale`` are as in :func:`doc_store_freshness` and
    ``doc_unreadable_files`` as in :func:`doc_store_unreadable`.
    ``(None, None, None)`` when there is no ready store or the read fails.
    Synchronous (the digest walk); async callers run it on a worker thread.
    Never raises.
    """
    snapshot = _ready_collection()
    if snapshot is None:
        return None, None, None
    store, collection = snapshot
    try:
        meta = dict(collection.metadata or {})
        current_digest = doc_store_digest(store._settings)
    except Exception:
        _log.debug("doc_store: freshness check failed", exc_info=True)
        return None, None, None
    stamped_commit = meta.get("indexed_commit")
    indexed_commit = stamped_commit if isinstance(stamped_commit, str) else None
    return indexed_commit, meta.get("doc_digest") != current_digest, _unreadable_stamp(meta)


def _unreadable_stamp(meta: dict[str, Any]) -> int | None:
    """Return the integer ``unreadable_files`` stamp in *meta*, or ``None``."""
    value = meta.get("unreadable_files")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def doc_store_unreadable() -> int | None:
    """Return the ``unreadable_files`` stamp of the ready doc collection (backlog line 63).

    The number of matched doc files whose read raised ``OSError`` during the
    build that produced the ready collection, so they are absent from the
    index.  ``None`` when there is no store, it is not ``"ready"``, the
    collection carries no stamp (built before the stamp existed) or the read
    fails.  Synchronous (a ChromaDB metadata read); async callers run it on a
    worker thread.
    """
    snapshot = _ready_collection()
    if snapshot is None:
        return None
    _store, collection = snapshot
    try:
        meta = dict(collection.metadata or {})
    except Exception:
        _log.debug("doc_store: unreadable-files read failed", exc_info=True)
        return None
    return _unreadable_stamp(meta)


def _try_adopt(
    store: DocStore,
    settings: Settings,
    embedding_function: Any | None,
    identity: str | None = None,
) -> bool:
    """Attempt to adopt an existing, fully-built ChromaDB collection onto *store*.

    Cheap (no embedding work): a single ``get_collection`` metadata check,
    plus one ``git rev-parse HEAD`` and one ``stat`` walk over
    :func:`doc_store_files` for the freshness stamp.  When the git call fails
    (current commit ``"none"``) and the stamp is a real commit, the commit
    compare is skipped and the digest decides (backlog line 45).
    "Build once" persistence: if the ChromaDB collection was built for the
    SAME project (its ``project_root`` fingerprint and its
    ``project_identity`` both equal the current values — DS-05, SW-01), from
    the SAME tree state (its stamped ``indexed_commit`` equals the current
    ``HEAD`` and its stamped ``doc_digest`` equals :func:`doc_store_digest`
    now — SW-04; a missing stamp on a pre-SW-04 collection compares unequal)
    with the SAME build settings (``doc_glob_patterns``,
    ``doc_exclude_patterns``, ``chunker_version`` and ``embedding_model`` all
    equal — SW-L13; a missing key compares unequal) and carries the
    build-complete sentinel, adopt it (``store._collection`` and
    ``store._state = DOC_STATE_READY``) and return ``True`` WITHOUT
    re-embedding.  Returns ``False`` on any failure to adopt (not found,
    interrupted build, cross-project mismatch, a stale commit or digest, a
    changed build setting, or an unexpected exception).  A read failure is
    logged at warning and deletes nothing (SW-L15); the rebuild that follows
    builds into staging and replaces the live collection only on success.

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
            # SW-L13: refuse an index built with other doc globs, exclusions,
            # chunker or embedding model.  Logged at info with the key that
            # differed; the caller rebuilds.
            for key, current in _build_settings_stamp(settings, embedding_function).items():
                if meta.get(key) != current:
                    _log.info(
                        "doc_store: not adopting: %s differs (stamped %r, current %r) — rebuilding",
                        key,
                        meta.get(key),
                        current,
                    )
                    return False
            # SW-04: refuse an index built from another tree state.  Logged at
            # info with the key that differed; the caller rebuilds.
            # Backlog line 45: _doc_head_commit returns "none" on any git
            # failure.  A current "none" against a real stamp is unknown, not
            # a mismatch, so the digest alone decides; a stamped "none"
            # against a real current commit still refuses.
            current_commit = _doc_head_commit(settings.project_root)
            stamped_commit = meta.get("indexed_commit")
            if (
                current_commit == _NO_COMMIT
                and isinstance(stamped_commit, str)
                and stamped_commit != _NO_COMMIT
            ):
                _log.info(
                    "doc_store: current commit unknown (git failed);"
                    " deciding adoption on doc_digest"
                )
            elif stamped_commit != current_commit:
                _log.info(
                    "doc_store: not adopting: %s differs (stamped %r, current %r) — rebuilding",
                    "indexed_commit",
                    meta.get("indexed_commit"),
                    current_commit,
                )
                return False
            current_digest = doc_store_digest(settings)
            if meta.get("doc_digest") != current_digest:
                _log.info(
                    "doc_store: not adopting: %s differs (stamped %r, current %r) — rebuilding",
                    "doc_digest",
                    meta.get("doc_digest"),
                    current_digest,
                )
                return False
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
                store._live_name = existing.name
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
        # SW-L15: a transient read failure must be visible and must not cost
        # the index: nothing is deleted here, and the rebuild that follows
        # replaces the live collection only once its staging build succeeds.
        _log.warning("doc_store: adopt check failed (%r) — rebuilding into staging", exc)
        return False


def _prepare_store(
    settings: Settings,
    embedding_function: Any | None = None,
    generation: int | None = None,
) -> DocStore | None:
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

    Shutdown (SW-L14): *generation* is the module's shutdown generation as
    the caller saw it (read here on entry when ``None``).  When
    :func:`clear_doc_store` ran while this call waited on the flock, the
    generation differs once the flock is acquired: this logs at info,
    publishes nothing to the singleton and returns ``None`` without
    adopting or rebuilding.  The same check runs again before a rebuild.

    Raises whatever lock acquisition, :class:`DocStore` construction or the
    rebuild raises.  A failure before the store exists is recorded in the
    module-level ``_init_error`` for ``doc_store_state()`` to report, and a
    construction failure also drops the singleton (SW-L22), so
    ``doc_store_state()`` reports the new error rather than an older store's
    state; a rebuild failure is recorded on the (published) store itself.
    Callers are responsible for catching it.
    """
    global _doc_store, _init_error
    if generation is None:
        generation = _shutdown_generation
    _init_error = None
    store: DocStore | None = None
    try:
        with _doc_store_flock(settings.chroma_path):
            if generation != _shutdown_generation:
                _log.info(
                    "doc_store: shutdown ran while waiting for the doc-store lock — not starting"
                )
                return None
            identity = project_identity(settings)
            _log.info(
                "doc_store: project identity %s, collection %s",
                identity,
                doc_collection_name(settings, identity),
            )
            try:
                store = DocStore(settings, embedding_function=embedding_function)
            except Exception:
                # SW-L22: drop an older (e.g. errored) singleton so the new
                # construction error recorded below is what callers see.
                _doc_store = None
                raise
            with store._build_lock:
                # SW-L14 (R-gate): the generation check and the publish happen
                # together under _running_lock, so a shutdown that landed
                # after the flock (during project_identity or DocStore(...))
                # publishes nothing, on the adopt and rebuild paths alike.
                with _running_lock:
                    current = generation == _shutdown_generation
                    if current:
                        _doc_store = store
                if not current:
                    _log.info("doc_store: shutdown ran while starting the doc store — not starting")
                    return None
                if not _try_adopt(store, settings, embedding_function, identity):
                    _log.info("doc_store: no usable existing collection — rebuilding")
                    # A shutdown after the publish is caught at registration
                    # (stale generation) or by the cancel flag.
                    store.rebuild(lock_held=True, generation=generation)
    except Exception as exc:
        if store is None:
            _init_error = f"{type(exc).__name__}: {exc}"
        raise
    return store


def init_doc_store(
    settings: Settings,
    embedding_function: Any | None = None,
    generation: int | None = None,
) -> DocStore:
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
        generation: the shutdown generation captured before this work was
            started (the ``refresh`` worker captures it on the event loop);
            ``None`` reads it on entry.

    Raises:
        DocRebuildCancelled: shutdown (:func:`clear_doc_store`) ran while this
            call waited on the doc-store lock, so nothing was started.
    """
    store = _prepare_store(settings, embedding_function, generation)
    if store is None:
        raise DocRebuildCancelled("doc store not started: shutdown ran while waiting for the lock")
    return store


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
    # Read on the event loop now, so a clear_doc_store() that runs before the
    # worker thread even starts still stops it (SW-L14).
    generation = current_shutdown_generation()

    async def _build() -> None:
        try:
            store = await asyncio.to_thread(
                _prepare_store, settings, embedding_function, generation
            )
        except DocRebuildCancelled:
            _log.info("doc_store: background startup build cancelled at shutdown")
            return
        except Exception:
            _log.exception("doc_store: background startup failed")
            return
        if store is None:
            _log.info("doc_store: background startup skipped (shutdown)")
            return
        _log.info("doc_store: background startup complete (state=%s)", store.state)

    _build_task = asyncio.create_task(_build(), name="doc-store-build")


def clear_doc_store() -> None:
    """Stop any running doc rebuild, then drop the module singleton (lifespan teardown).

    SW-L14: bumps the shutdown generation (a worker still waiting on the
    flock in :func:`_prepare_store` then publishes nothing), sets the cancel
    flag of every store with a rebuild running — started by the startup task
    or by a ``refresh`` worker — and waits up to 10 seconds in total for
    those rebuilds to exit.  A cancelled rebuild stops between files or
    between embedding batches and deletes its staging collection, so the live
    collection survives.  A rebuild still running after 10 seconds is logged
    at warning and left to
    finish on its daemon thread.  The wait blocks the caller (the lifespan's
    ``finally``) for at most 10 seconds, which is acceptable because the
    lifespan is ending.
    """
    global _doc_store, _init_error, _build_task, _rebuild_thread, _shutdown_generation
    with _running_lock:
        _shutdown_generation += 1
        running = list(_running_stores)
    for store in running:
        store._cancel.set()
    deadline = time.monotonic() + _SHUTDOWN_WAIT_SECONDS
    for store in running:
        if not store._rebuild_idle.wait(max(0.0, deadline - time.monotonic())):
            _log.warning(
                "doc_store: the doc index rebuild did not stop within %.0f seconds of shutdown",
                _SHUTDOWN_WAIT_SECONDS,
            )
    _doc_store = None
    _init_error = None
    _build_task = None
    _rebuild_thread = None
