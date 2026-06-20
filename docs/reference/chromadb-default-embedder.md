# ChromaDB default embedding function + model cache location

**Library:** `chromadb` **1.5.9** (current on PyPI as of check). **Date:** 2026-06-19.
**Source:** Context7 (`/websites/cookbook_chromadb_dev`) for the EF identity; package
source inspection (`pip download chromadb --no-deps`, then
`chromadb/utils/embedding_functions/onnx_mini_lm_l6_v2.py`) for the cache path, which
the docs do not cover.

## Default embedding function

- `DefaultEmbeddingFunction` — **as of 1.5.9 it is its own class in
  `chromadb.api.types` that *delegates to* `ONNXMiniLM_L6_V2`** (no longer a bare
  alias; `__init__`/`__call__` route to `ONNXMiniLM_L6_V2()`). Same model:
  **all-MiniLM-L6-v2** on **ONNX Runtime**. (Re-confirmed 2026-06-19 by wheel
  inspection of chromadb 1.5.9.)
- The fallback "override DOWNLOAD_PATH" path still subclasses **`ONNXMiniLM_L6_V2`**
  (the class that actually holds `DOWNLOAD_PATH`), not `DefaultEmbeddingFunction`.
- Runs **locally on CPU, no torch, no API key**. Bundled with the **full `chromadb`**
  package — NOT the thin `chromadb-client` (which omits onnxruntime and raises
  "You must provide an embedding function").
- Output: 384-dim vectors; **~256-token input window** (longer chunks are truncated —
  size doc chunks under this).
- Default collection distance metric is **L2**; set **cosine** at collection creation
  for normalized text embeddings. **Current (1.5.x) form — VERIFIED 2026-06-19:**

  ```python
  import chromadb
  client = chromadb.PersistentClient(path="<bind-mount>/chroma")
  col = client.create_collection(
      "ripgrep_docs",
      configuration={"hnsw": {"space": "cosine"}},   # current API
  )
  ```
  The legacy `metadata={"hnsw:space": "cosine"}` form still works but
  `configuration={"hnsw": {...}}` is the current shape. Distance metric is immutable
  after creation. `DefaultEmbeddingFunction` is used unless an EF is passed explicitly.

## Model cache location (for "download once" via bind mount)

Hardcoded in `onnx_mini_lm_l6_v2.py` (1.5.9):

```python
DOWNLOAD_PATH = Path.home() / ".cache" / "chroma" / "onnx_models" / "all-MiniLM-L6-v2"
```

- **No environment variable** overrides this path (verified: the module reads none).
- The ~80 MB archive is downloaded once and SHA256-verified
  (`_MODEL_SHA256 = 913d7300...`), then extracted under `DOWNLOAD_PATH`.

### How we relocate it (DECIDED)

- **Primary:** bind-mount the container's `~/.cache/chroma` onto a persistent,
  gitignored folder under `.devcontainer/cache/` (same pattern as Phase 0.2 caches).
  Zero code change — ChromaDB writes to its default path, now backed by the mount, so
  the model downloads once and survives container rebuilds.
- **Fallback (if mount point can't be controlled):** subclass `ONNXMiniLM_L6_V2`,
  override `DOWNLOAD_PATH`, and pass that EF explicitly to the collection instead of
  relying on `DefaultEmbeddingFunction`.

## To re-verify at build (UNVERIFIED specifics)

- Confirm `uv add chromadb` resolves ~1.5.9 and `DOWNLOAD_PATH` is unchanged (it's a
  hardcoded class attribute, so re-grep on version bump).
- Confirm the container's home dir (`Path.home()`) so the bind mount targets the right
  `~/.cache/chroma`.
