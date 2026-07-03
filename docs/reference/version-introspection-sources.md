# Version introspection sources (KI-12 status-envelope fields)

**Libraries:** multilspy 0.0.15, rust-analyzer 1.96.0 · **Verified:**
2026-07-02 (source inspection + live commands) · For plan
[cli-frontend.md](../planning/cli-frontend.md) (D10, U6); closes the design
side of [KI-12](../impl/known-issues.md) (#115).

## Per-field mechanism

- **rust-analyzer version:** the LSP `serverInfo` route is **dead** — multilspy
  discards the `InitializeResult` as a local variable after capability asserts
  (`multilspy/language_servers/rust_analyzer/rust_analyzer.py:166-173`); it is
  never stored on `self`, anywhere in the package. Use a subprocess
  `<RLM_RUST_ANALYZER_BIN> --version`, output format
  `rust-analyzer <semver> (<sha> <date>)` (confirmed: `rust-analyzer 1.96.0
  (ac68faa 2026-05-25)`). Run **once at manager/daemon start** and cache for
  the process lifetime (the binary can't change without a restart); don't pay
  a subprocess per `status` call.
- **multilspy version:** `importlib.metadata.version("multilspy")` — confirmed
  (0.0.15); in-process, cheap enough per request.
- **server version:** `importlib.metadata.version("rust-lsp-mcp")` — confirmed
  (0.1.0); same treatment.
