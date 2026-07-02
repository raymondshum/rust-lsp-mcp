"""Opt-in production-image smoke gate (#88) — DS-16, DS-25, end-to-end status.

This module builds the REAL production image (``Dockerfile`` at the repo
root) and drives it as a black box: no source is monkeypatched, no fake LSP,
no fake doc store.  It exists to catch regressions that only a running
container can catch — the fast/integration tiers stub out everything this
module deliberately does NOT stub.

Opt-in only (``pytestmark`` below): unset ``RLM_IMAGE_SMOKE`` (or no
podman/docker on PATH) makes every test in this module SKIP, never error, and
never collect into the fast (``-m "not integration"``) or integration
(``-m integration``) tiers — it has its own ``image`` marker.  Local
invocation::

    RLM_IMAGE_SMOKE=1 uv run pytest -m image

Each of the following build+run a container from ``rust-lsp-mcp:smoke``
(always rebuilt fresh by the session-scoped ``smoke_image`` fixture — a
pre-existing ``:latest``/``:smoke`` tag is never trusted; on this host the
local ``:latest`` predates the DS-16 fix and has no ``/etc/gitconfig`` at
all, which would make every assertion below pass for the wrong reason).

Test coverage:
    DS-16 (git-as-root on a foreign-owned bind mount — ``TestDS16...``):
        The image runs as root with no ``USER`` directive, and the target
        project is always a host-owned bind mount, so git's "dubious
        ownership" protection (added in git 2.35.2) would otherwise make
        every git-derived ``status`` field (``indexed_commit``,
        ``current_commit``, ``stale``) permanently ``null``.  The Dockerfile
        bakes ``git config --system --add safe.directory /project`` to
        rescue this.  See the class docstring for the "vacuity trap" this
        test's negative control exists to close: under rootless podman's
        DEFAULT user namespace, container uid 0 already maps to the invoking
        host user, so a plain bind mount is NOT foreign and the positive
        assertion would trivially pass even with the Dockerfile line
        deleted.  ``foreign_git_repo`` uses ``podman unshare chown -R 1:1``
        to force genuine foreign ownership (host subuid 589824 -> container
        uid 1), and the negative control (unset safe.directory, re-run
        rev-parse) proves that foreign-ness is real by asserting on git's
        actual "dubious ownership" failure text.

    DS-25 (zero-network baked embedding model — ``TestDS25...``):
        ChromaDB's default embedding model (all-MiniLM-L6-v2) is baked into
        the image at build time under ``HOME=/opt/rlm`` (a non-volume path),
        specifically so the doc index can build with ``--network none`` at
        runtime.  The positive assertion runs the embedding function inside
        the container under ``--network none`` and checks the real
        embedding dimension (384).  The mutation (``HOME=/tmp/elsewhere``,
        still ``--network none``) makes ChromaDB miss the baked cache and
        attempt an actual network fetch, which must fail with a DNS/connect
        error — proving the positive result is attributable to the baked
        model, not to some accidental network path being open in this test
        environment.

    End-to-end status over real MCP stdio (``TestStatusEndToEndOverStdio``):
        Launches the image exactly as a host MCP client would (``podman/
        docker run --rm -i --network none ... IMAGE``), speaks the real MCP
        stdio protocol (``mcp.client.stdio.stdio_client`` + ``ClientSession``,
        driven via ``anyio.run`` — this repo has no ``pytest-asyncio``, only
        the transitive ``anyio`` dependency), and asserts the ``status``
        tool's ``structuredContent`` shows a real (40-hex) ``indexed_commit``
        with ``doc_index_state`` not ``"error"``.  ``status`` is deliberately
        ungated (it IS the readiness check) and captures ``indexed_commit``
        before rust-analyzer is even started (see
        ``AnalyzerManager._run``), so this does not require the analyzer to
        reach ``"ready"`` — only that the whole stack (baked git config,
        baked embedding model, MCP stdio transport) came up cleanly under
        the same ``--network none`` a real offline run would use.

Deliberately NOT in CI: every test here builds a multi-hundred-MB image
(full Rust toolchain + rustup + the embedding model); a cold build takes
several minutes even on a fast connection.  This is a LOCAL pre-release
gate — see docs/guide/development.md.

Red evidence for these tests is unusual for a repo whose other suites open
with a red phase: there is no application code to comment out and re-run,
because the assertions here are about container/OS-level behavior (uid
mapping, DNS resolution, git's ownership check), not our Python. The
negative controls INSIDE the two container tests (git ownership unset;
HOME redirected under --network none) ARE that red evidence — each proves
its corresponding positive assertion is falsifiable in this exact
environment, not just true by construction. A third, out-of-band check
(building a Dockerfile *mutant* with the ``safe.directory`` line removed and
re-running the DS-16 positive assertion against it) was performed manually
during development to confirm the positive assertion itself fails without
the fix; it is not committed here since building a second image doubles
this suite's already-expensive image builds for a check the in-test
negative control already covers on every run.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

# ---------------------------------------------------------------------------
# Opt-in gating
# ---------------------------------------------------------------------------

RUNTIME: str = shutil.which("podman") or shutil.which("docker") or ""
OPTED_IN = os.environ.get("RLM_IMAGE_SMOKE") == "1"
pytestmark = [
    pytest.mark.image,
    pytest.mark.skipif(
        not (OPTED_IN and RUNTIME),
        reason="image smoke: set RLM_IMAGE_SMOKE=1 and install podman/docker",
    ),
]

IMAGE = "rust-lsp-mcp:smoke"
REPO_ROOT = Path(__file__).resolve().parent.parent
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")

_CARGO_TOML = """\
[package]
name = "smoke-fixture"
version = "0.1.0"
edition = "2021"
"""

_LIB_RS = "pub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n"

_EMBED_SCRIPT = (
    "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction as D; "
    "print(len(D()(['smoke test'])[0]))"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_rootless_podman() -> bool:
    """True when RUNTIME is podman running rootless.

    Rootless podman's default user namespace maps container uid 0 to the
    *invoking host user*, so a plain host-owned bind mount already looks
    root-owned from inside the container — DS-16 would be vacuous there
    without ``foreign_git_repo``'s ``podman unshare chown`` step. Rootful
    docker mounts are natively foreign (no uid remapping at all), so no
    chown dance is needed in that case.
    """
    if not RUNTIME or not RUNTIME.endswith("podman"):
        return False
    result = subprocess.run(
        [RUNTIME, "info", "--format", "{{.Host.Security.Rootless}}"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _git(repo: Path, *args: str) -> None:
    """Run ``git -C <repo> <args>`` on the HOST, checked."""
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _podman_run(
    image: str,
    *,
    repo: Path | None,
    entrypoint: str,
    args: Sequence[str],
    network: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 120,
) -> subprocess.CompletedProcess[str]:
    """Run ``image`` with entrypoint overridden, then belt-and-suspenders ``rm -f``.

    Always uses a unique container name and force-removes it in a
    ``finally`` — ``--rm`` alone is not trustworthy if this process is
    killed (e.g. on a ``subprocess.TimeoutExpired``) before the container
    exits on its own.
    """
    name = f"rlm-smoke-{uuid.uuid4().hex[:12]}"
    cmd = [RUNTIME, "run", "--rm", "--name", name]
    if network is not None:
        cmd += ["--network", network]
    for key, value in (env or {}).items():
        cmd += ["-e", f"{key}={value}"]
    if repo is not None:
        cmd += ["-v", f"{repo}:/project:ro,z"]
    cmd += ["--entrypoint", entrypoint, image, *args]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    finally:
        subprocess.run([RUNTIME, "rm", "-f", name], capture_output=True, timeout=30)


def _run_bash(
    image: str,
    repo: Path | None,
    script: str,
    *,
    network: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return _podman_run(
        image, repo=repo, entrypoint="/bin/bash", args=["-c", script], network=network, env=env
    )


def _run_python(
    image: str,
    repo: Path | None,
    script: str,
    *,
    network: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return _podman_run(
        image,
        repo=repo,
        entrypoint="/app/.venv/bin/python",
        args=["-c", script],
        network=network,
        env=env,
    )


def _extract_status_payload(result: types.CallToolResult) -> dict[str, Any]:
    """Pull the ``status`` tool's payload dict out of a CallToolResult.

    ``status`` returns ``dict[str, Any]`` with a plain (unwrapped)
    ``structuredContent`` (FastMCP only wraps under ``{"result": ...}`` for
    non-``dict[str, str-keyed]`` return annotations), so the common case is
    just ``result.structuredContent``. The text-content parse is a
    belt-and-suspenders fallback in case a client/server version mismatch
    ever drops structuredContent.
    """
    if result.isError:
        raise AssertionError(f"status call returned isError=True: {result!r}")
    if result.structuredContent is not None:
        return result.structuredContent
    for block in result.content:
        if isinstance(block, types.TextContent):
            return json.loads(block.text)
    raise AssertionError(f"status call returned no parseable payload: {result!r}")


async def _poll_status_until_indexed(session: ClientSession) -> dict[str, Any]:
    """Call ``status`` until ``indexed_commit`` is populated.

    ``AnalyzerManager._run`` captures ``indexed_commit`` via a synchronous
    git call BEFORE it even starts rust-analyzer (see analyzer.py), so in
    practice the very first call already has it — this loop is just cheap
    insurance against a cold-start race, bounded by the caller's
    ``anyio.fail_after(120)``, not an independent timeout.
    """
    while True:
        result = await session.call_tool("status", {})
        payload = _extract_status_payload(result)
        if payload.get("indexed_commit") is not None:
            return payload
        await anyio.sleep(0.5)


async def _call_status_over_stdio(image: str, repo: Path) -> dict[str, Any]:
    """Launch ``image`` as a real MCP client would and return status's payload.

    Mirrors the documented host invocation: ``podman/docker run --rm -i
    --network none -v <repo>:/project:ro,z IMAGE``, speaking MCP over
    stdio. ``--network none`` is the whole point of DS-25 — this proves the
    full stack (baked git config, baked embedding model, stdio transport)
    comes up with zero network, not just the embedding call in isolation.
    """
    name = f"rlm-smoke-{uuid.uuid4().hex[:12]}"
    params = StdioServerParameters(
        command=RUNTIME,
        args=[
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--name",
            name,
            "-e",
            "RLM_PROJECT_ROOT=/project",
            "-v",
            f"{repo}:/project:ro,z",
            image,
        ],
    )
    try:
        with anyio.fail_after(120):
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                return await _poll_status_until_indexed(session)
    finally:
        # stdio_client's own teardown closes stdin, which should make the
        # entrypoint exit and --rm reap the container — belt-and-suspenders
        # against a hang leaving an orphaned container behind.
        subprocess.run([RUNTIME, "rm", "-f", name], capture_output=True, timeout=30)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def smoke_image() -> str:
    """Build the production image fresh and tag it ``rust-lsp-mcp:smoke``.

    Never trust a pre-existing tag: the local ``:latest`` observed on this
    host predates the DS-16 fix (no ``/etc/gitconfig`` baked in at all),
    which would make every positive assertion below pass for the wrong
    reason. A warm build-cache re-run (no Dockerfile/src changes) takes
    seconds; a cold build (first run on a host, or after a toolchain/
    dependency bump) takes several minutes (rustup + the full apt/uv/
    embedding-model layers).
    """
    subprocess.run([RUNTIME, "build", "-t", IMAGE, str(REPO_ROOT)], check=True)
    return IMAGE


@pytest.fixture
def foreign_git_repo(tmp_path: Path) -> Iterator[Path]:
    """A committed git repo, genuinely foreign-owned from the container's PoV.

    Scaffolds a minimal (but real) Cargo project — one commit, so
    ``git rev-parse HEAD`` has something real to return and the server has
    a plausible (if trivial) target to index. Under rootless podman, the
    repo is then re-owned via ``podman unshare chown -R 1:1`` (host subuid
    589824, mapping to container uid 1) so it is NOT owned by the
    container's root user — the precondition DS-16 actually defends
    against. See ``_is_rootless_podman``'s docstring for why this step is
    skipped (and unnecessary) under rootful docker.

    Teardown chowns back to ``0:0`` (i.e. back to the invoking host user,
    within the ``podman unshare`` namespace) — mandatory, verified by hand:
    without it, ``tmp_path``'s own cleanup cannot remove files it no longer
    owns and pytest fails with a permission error after the test itself
    already passed.
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "Cargo.toml").write_text(_CARGO_TOML)
    (repo / "src" / "lib.rs").write_text(_LIB_RS)

    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.email=smoke@example.com",
        "-c",
        "user.name=Smoke Test",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        "initial commit",
    )

    rootless = _is_rootless_podman()
    if rootless:
        subprocess.run([RUNTIME, "unshare", "chown", "-R", "1:1", str(repo)], check=True)
    try:
        yield repo
    finally:
        if rootless:
            subprocess.run([RUNTIME, "unshare", "chown", "-R", "0:0", str(repo)], check=True)


# ---------------------------------------------------------------------------
# DS-16 — git-as-root on a foreign-owned bind mount
# ---------------------------------------------------------------------------


class TestDS16GitAsRootOnForeignMount:
    """The baked ``safe.directory`` line is what rescues ``rev-parse`` here.

    Vacuity trap: rootless podman's default userns already maps container
    uid 0 to the host user, so an *ordinary* bind mount is not foreign at
    all — the positive assertion below would pass even with the Dockerfile
    line deleted. ``foreign_git_repo`` forces genuine foreign ownership
    (``podman unshare chown -R 1:1``); the negative control here (unset
    ``safe.directory``, re-run ``rev-parse``) is what proves that: it must
    fail with git's real "dubious ownership" message, or the whole test is
    vacuous and its positive result is meaningless.
    """

    def test_positive_and_negative(self, smoke_image: str, foreign_git_repo: Path) -> None:
        positive = _run_bash(smoke_image, foreign_git_repo, "git -C /project rev-parse HEAD")
        assert positive.returncode == 0, positive.stderr
        commit = positive.stdout.strip()
        assert _COMMIT_RE.fullmatch(commit), f"not a 40-hex commit hash: {commit!r}"

        negative = _run_bash(
            smoke_image,
            foreign_git_repo,
            "git config --system --unset-all safe.directory && git -C /project rev-parse HEAD",
        )
        assert negative.returncode != 0, (
            "negative control unexpectedly SUCCEEDED — the bind mount is not "
            "actually foreign-owned inside the container, so this test is "
            "vacuous (DS-16 could regress silently). Check foreign_git_repo's "
            "chown step and _is_rootless_podman detection."
        )
        assert "dubious ownership" in negative.stderr, negative.stderr


# ---------------------------------------------------------------------------
# DS-25 — zero-network baked embedding model
# ---------------------------------------------------------------------------


class TestDS25EmbedsOfflineFromBakedModel:
    """The baked ``HOME=/opt/rlm`` model cache is what makes this offline.

    The mutation redirects ``HOME`` to a directory with no cache under the
    same ``--network none`` — ChromaDB then tries to download the model and
    must fail with a connect/DNS error. Without this mutation, a passing
    positive assertion could just as easily mean "there happens to be
    network access in this environment" rather than "the model is baked
    in" — the mutation is what rules that out.
    """

    def test_positive_and_mutation(self, smoke_image: str) -> None:
        positive = _run_python(smoke_image, None, _EMBED_SCRIPT, network="none")
        assert positive.returncode == 0, positive.stderr
        assert positive.stdout.strip() == "384", positive.stdout

        mutation = _run_python(
            smoke_image,
            None,
            _EMBED_SCRIPT,
            network="none",
            env={"HOME": "/tmp/elsewhere"},
        )
        assert mutation.returncode != 0, (
            "mutation unexpectedly SUCCEEDED — HOME=/tmp/elsewhere should have "
            "missed the baked model cache and attempted (and failed) a network "
            "download; if this passes, the model is baking somewhere HOME "
            "redirection doesn't reach, or --network none isn't actually "
            "blocking DNS in this environment"
        )
        combined = mutation.stdout + mutation.stderr
        assert "ConnectError" in combined or "Temporary failure in name resolution" in combined, (
            combined
        )


# ---------------------------------------------------------------------------
# End-to-end — status over real MCP stdio, --network none
# ---------------------------------------------------------------------------


class TestStatusEndToEndOverStdio:
    """Drive the image exactly as a host MCP client would and read ``status``.

    Unlike the two tests above (which probe one mechanism each via
    ``--entrypoint``), this launches the REAL entrypoint
    (``/app/.venv/bin/rust-lsp-mcp``) and speaks the real MCP protocol.
    ``indexed_commit`` non-null is the end-to-end signal that DS-16's fix is
    exercised through the actual server code path (not just a manual git
    invocation), under the same ``--network none`` DS-25 requires.
    """

    def test_indexed_commit_via_mcp_status(self, smoke_image: str, foreign_git_repo: Path) -> None:
        payload = anyio.run(_call_status_over_stdio, smoke_image, foreign_git_repo)

        assert payload.get("status") == "ok", payload
        indexed_commit = payload.get("indexed_commit")
        assert indexed_commit is not None, payload
        assert _COMMIT_RE.fullmatch(indexed_commit), f"not a 40-hex commit hash: {indexed_commit!r}"
        assert payload.get("doc_index_state") != "error", payload
