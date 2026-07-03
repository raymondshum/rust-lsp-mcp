# Phase C5 — Skill revision (durable prompt)

**Depends on C2 (real command syntax). May run parallel with C4 (disjoint
files).**

## Read first
- [cli-frontend.md](../planning/cli-frontend.md) — D11 and the Phase 5 section.
- The existing skill: `.claude/skills/rust-code-navigation/SKILL.md` — keep
  its triggers and the position-protocol content intact.

## Build
- **One capability-branched skill, not two:** add a "How to invoke" section to
  `rust-code-navigation/SKILL.md`: use the MCP tools when available; otherwise
  the `rust-lsp` CLI. The CLI branch must give a Bash-only subagent everything
  it needs: runtime auto-detect (`docker`|`podman`, as in
  `scripts/prime-cache.sh`), container-name parameter (`rust-lsp-mcp` /
  `rust-lsp-mcp-isolated`), the no-prefix in-container case, `--wait 180` on
  first call, the D8 exit-code table, the `refresh`-affects-everyone warning,
  and the `not_found`-is-an-answer / exit-0 note.

## Scope / stop boundary
One file. No new skill directory.

## Definition of done (QA gate)
The QA agent dry-runs the skill's commands **verbatim** (copy-paste) against
C1's daemon fixture in the podman gate and **reports the transcript to the
orchestrator, who records it** in [progress-cli.md](progress-cli.md) (the
orchestrator is the tracker's sole writer).

## Adversarial (LOW)
Falsify: a command in the skill that doesn't run verbatim; MCP-tool guidance
that contradicts the CLI branch; a trigger change that breaks the existing
skill's firing conditions. 2-round cap.
