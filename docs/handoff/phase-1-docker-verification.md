# Phase 1 — production image verification (run on a Docker host)

The [production image](../../Dockerfile) (PR #13) was authored in a build
container that has **no Docker**, so its build + runtime gates could not run
there. This is the deferred gate. Run it on any machine with Docker; it clears
residue **R1/R2/R3** from the
[plan](../planning/repo-agnostic-and-docker-launch.md).

## What we're proving

| ID | Claim | How |
|----|-------|-----|
| R2 | The image has the **full toolchain** rust-analyzer needs (rustc + cargo + rust-std + rust-src), not just the RA binary. | Probe the image's binaries + sysroot. |
| R1 | `docker run -i` carries MCP JSON-RPC **cleanly over stdout** (no log/telemetry contamination corrupting the protocol) and tools work end-to-end against a **non-ripgrep** project. | Wire the image as an MCP server and call the tools. |
| R3 | Caches on the `/data` volume warm subsequent sessions (and quantify the per-session rust-analyzer re-index that the plan says always happens — claim U5). | Time a cold vs warm `status → ready`. |

## Prerequisites

- Docker installed and running.
- A clone of this repo (for `docker build`).
- A target **Rust project** on the host (any Cargo project). If you have none,
  clone the sample: `git clone --depth 1 --branch 14.1.1 https://github.com/BurntSushi/ripgrep /tmp/ripgrep`.

## Checklist

1. **Build (R1 part 1).**
   `docker build -t rust-lsp-mcp .` → completes without error.
   - If it fails compiling a `-sys` crate for *your* target project later, the
     image is missing a system lib → add it to the `apt-get` line in the Dockerfile.

2. **Toolchain present (R2).**
   ```
   docker run --rm --entrypoint rust-analyzer rust-lsp-mcp --version
   docker run --rm --entrypoint cargo         rust-lsp-mcp --version
   docker run --rm --entrypoint rustc         rust-lsp-mcp --version
   # rust-src present (sysroot:discover needs it):
   docker run --rm --entrypoint sh rust-lsp-mcp -c \
     'ls "$(rustc --print sysroot)/lib/rustlib/src/rust/library/std" >/dev/null && echo rust-src-OK'
   ```
   All four print versions / `rust-src-OK`.

3. **Clean stdio + tools end-to-end, repo-agnostic (R1 part 2).** Wire the image
   into an MCP client (Claude Code below, or the MCP Inspector) pointed at your
   target project, then:
   - call `status` repeatedly until `state: "ready"` (proves indexing completes
     and — critically — that **stdout was clean enough for the client to parse
     every response**; any log/telemetry leak to stdout breaks the connection);
   - call `find_symbol` for a symbol you know exists in the target project →
     returns its file + 1-indexed position;
   - call `search_docs` with a phrase from the project's Markdown → returns
     relevant passages.
   Success here is R1: clean stdio **and** genuine repo-agnostic operation on a
   non-ripgrep project.

4. **Warm-start timing (R3).**
   - Cold: `docker volume rm rlm-data` (ignore if absent), then start a session
     and time how long `status` takes to reach `ready`.
   - Warm: end the session, start a new one (same `-v rlm-data:/data`), time again.
   - Expect the warm time to be **shorter** (cargo cache reused) but **not zero** —
     the plan (U5) says rust-analyzer re-indexes every process start. Record both.

5. **(Optional) `docker exec` warm path.**
   `RUST_PROJECT=/abs/path docker compose up -d`, then point the client at
   `docker exec -i rust-lsp-mcp /app/.venv/bin/rust-lsp-mcp`. After the first
   index, later sessions should be ~instant (RA stays hot). `docker compose down`
   when done.

## Report

Record per item: pass/fail, and for R3 the cold vs warm `status→ready` seconds.
Any failure on step 1/2 is a Dockerfile fix (usually a missing `apt` package);
a failure on step 3 where the client never connects points at **stdout
contamination** — capture the raw bytes (`docker run -i ... <<<'' | head -c 400`)
and look for non-JSON-RPC output.

---

## Copy-paste prompt for a Claude Code session ON THE HOST

> I'm on a machine with Docker. Verify the production Docker image for the
> `rust-lsp-mcp` project end-to-end, following
> `docs/handoff/phase-1-docker-verification.md` in the repo. This clears residue
> R1/R2/R3 from `docs/planning/repo-agnostic-and-docker-launch.md` — the image
> was authored in a container without Docker, so it has never actually been built
> or run.
>
> Do this:
> 1. From the repo root, run `docker build -t rust-lsp-mcp .` and report whether
>    it succeeds. If it fails, diagnose (usually a missing system package in the
>    Dockerfile's `apt-get` line) and propose the one-line fix.
> 2. Probe the toolchain inside the image: `rust-analyzer --version`,
>    `cargo --version`, `rustc --version`, and that `rust-src` exists under the
>    sysroot (commands are in the checklist). Confirm all present.
> 3. Pick a real Rust project on this host to target (ask me for a path, or clone
>    ripgrep 14.1.1 to /tmp as a fallback — but prefer a NON-ripgrep project to
>    prove repo-agnosticism). Register the image as an MCP server:
>    `claude mcp add rust-lsp-mcp -- docker run -i --rm -v <ABS_PROJECT_PATH>:/project:ro -v rlm-data:/data rust-lsp-mcp`
>    Then exercise it: poll the `status` tool until `state: ready` (this also
>    proves stdout is clean JSON-RPC — if the server never connects, suspect log
>    or telemetry output leaking to stdout, and capture the raw bytes to confirm).
>    Then call `find_symbol` for a symbol that exists in the target project, and
>    `search_docs` with a phrase from its Markdown. Report the actual responses.
> 4. Measure the re-index warmup: time `status → ready` with an empty `rlm-data`
>    volume (cold), then again in a fresh session reusing the volume (warm).
>    Report both numbers — the warm one should be faster but non-zero.
> 5. Summarize pass/fail per the checklist. If everything passes, update the
>    "Status (as built)" table in `docs/planning/repo-agnostic-and-docker-launch.md`
>    to mark R1/R2/R3 cleared (with the warm/cold timings), and open a small PR
>    with any Dockerfile fixes you had to make. Do NOT change application code
>    beyond what's needed to make the image build/run.
