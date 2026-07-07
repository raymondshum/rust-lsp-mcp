#!/usr/bin/env python3
"""Guide-doc freshness / source-pin drift gate (docs SHARED CONTRACT §4).

Every doc under ``docs/guide/**/*.md`` carries YAML frontmatter with a
``source_pins`` block (the source files it substantively describes, each pinned
to a commit) and an optional ``cite`` list (load-bearing ``path`` or
``path:symbol`` anchors its prose leans on). This gate keeps the prose honest
against the code it documents:

HARD checks (fail the gate, exit 1) — for every doc:
  * each ``source_pins[].path`` exists on disk (repo-root-relative);
  * each ``cite`` entry resolves: ``path`` -> the file exists;
    ``path:symbol`` -> the file exists AND ``symbol`` is a literal substring of
    it (plain grep, no parsing).

SOFT checks (advisory only — NEVER change the exit code): for each pinned path,
``git log --oneline <commit>..HEAD -- <path>`` reveals whether the file moved on
since the pin. A drifted file only means "a human should re-read and re-pin,"
so it is printed as a ``⚠ drift`` advisory and nothing more.

Frontmatter is parsed by a hand-rolled minimal reader (stdlib only — no PyYAML)
that understands just the two shapes this schema uses: a ``key: value`` scalar
and a ``- `` list of either scalars or ``path:``/``commit:`` dict rows.

Usage: check_doc_freshness.py [--guide-dir DIR]
Exit: 0 no hard failures, 1 hard failure(s), 2 bad usage / no docs found.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def repo_root() -> Path:
    """Repo root = this file's parent's parent (``scripts/`` sits directly under
    the root).

    Deliberately NOT ``git rev-parse --show-toplevel``: when this script runs from
    inside a git hook (e.g. pre-push), git exports ``GIT_DIR``/``GIT_WORK_TREE``
    into the environment, and rev-parse would then resolve to whichever worktree
    that points at — not necessarily the checkout the script lives in. The script
    and the docs it checks are always co-located, so ``__file__`` is the reliable,
    hook-independent anchor."""
    return Path(__file__).resolve().parent.parent


def _git_env() -> dict:
    """A copy of the environment with git's per-invocation location vars removed,
    so a ``git`` subprocess rediscovers the repo from its ``cwd`` instead of an
    inherited ``GIT_DIR`` (which a pre-push hook exports and which may point at a
    different worktree)."""
    env = dict(os.environ)
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(var, None)
    return env


def _strip_scalar(value: str) -> str:
    """Strip surrounding quotes and whitespace from a scalar YAML value."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value


def parse_frontmatter(text: str) -> dict:
    """Extract the leading ``---`` … ``---`` YAML frontmatter as a dict.

    Understands exactly the shapes this schema uses:
      * ``key: value``                    -> scalar (str)
      * ``key: []``                       -> empty list
      * ``key:`` then ``  - item`` lines  -> list of scalars, OR
      * ``key:`` then ``  - path: x`` /
        ``    commit: y`` row pairs       -> list of {path, commit} dicts

    Returns ``{}`` when there is no frontmatter block.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}

    result: dict = {}
    current_key = None  # the list-bearing key we are currently accumulating into
    i = 1
    while i < end:
        raw = lines[i]
        stripped = raw.strip()
        i += 1
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw) - len(raw.lstrip())

        # A list item under the active list key.
        if stripped.startswith("- ") or stripped == "-":
            if current_key is None:
                continue
            item = stripped[1:].strip()
            if item.startswith("path:"):
                # start of a {path, commit} dict row (the source_pins shape)
                entry = {"path": _strip_scalar(item[len("path:"):])}
                result[current_key].append(entry)
            else:
                # a scalar list item — kept verbatim. cite entries are scalars
                # that legitimately contain a colon (``path:symbol``), so we do
                # NOT treat an embedded ':' as a dict row here.
                result[current_key].append(_strip_scalar(item))
            continue

        # A continuation line of the most recent dict row (e.g. ``commit:``).
        if indent > 0 and ":" in stripped and current_key is not None:
            k, _, v = stripped.partition(":")
            bucket = result.get(current_key)
            if isinstance(bucket, list) and bucket and isinstance(bucket[-1], dict):
                bucket[-1][k.strip()] = _strip_scalar(v)
                continue

        # A top-level ``key: value`` (or ``key:`` opening a list).
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if value == "" :
                result[key] = []
                current_key = key
            elif value == "[]":
                result[key] = []
                current_key = None
            elif value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                result[key] = (
                    [_strip_scalar(p) for p in inner.split(",")] if inner else []
                )
                current_key = None
            else:
                result[key] = _strip_scalar(value)
                current_key = None
    return result


def changed_since(root: Path, path: str, commit: str) -> int:
    """Number of commits touching ``path`` on ``<commit>..HEAD`` (0 == in sync).

    Returns -1 when git cannot answer (bad commit, missing path in history) — an
    unknowable state is not asserted as drift.
    """
    try:
        out = subprocess.run(
            ["git", "log", "--oneline", f"{commit}..HEAD", "--", path],
            cwd=root,
            env=_git_env(),
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return -1
    return len([ln for ln in out.stdout.splitlines() if ln.strip()])


def check_doc(root: Path, doc: Path) -> tuple[list[str], list[str], bool]:
    """Check one doc. Returns (hard_failures, soft_advisories, had_pins)."""
    rel = doc.relative_to(root)
    hard: list[str] = []
    soft: list[str] = []

    meta = parse_frontmatter(doc.read_text())
    pins = meta.get("source_pins") or []
    cites = meta.get("cite") or []
    if not pins and not cites:
        return hard, soft, False

    # --- HARD: source_pins paths must exist -----------------------------------
    for pin in pins:
        if not isinstance(pin, dict):
            continue
        p = pin.get("path")
        if not p:
            hard.append(f"FAIL {rel}: source_pins entry has no 'path'")
            continue
        if not (root / p).exists():
            hard.append(f"FAIL {rel}: pinned source path does not exist: {p}")

    # --- HARD: cite entries must resolve (path, and symbol if given) -----------
    for cite in cites:
        if not isinstance(cite, str) or not cite.strip():
            continue
        if ":" in cite:
            path_part, symbol = cite.split(":", 1)
            path_part, symbol = path_part.strip(), symbol.strip()
        else:
            path_part, symbol = cite.strip(), None
        target = root / path_part
        if not target.exists():
            hard.append(f"FAIL {rel}: cite path does not exist: {path_part}")
            continue
        if symbol:
            try:
                if symbol not in target.read_text(errors="replace"):
                    hard.append(
                        f"FAIL {rel}: cite symbol '{symbol}' not found in {path_part}"
                    )
            except OSError as exc:
                hard.append(f"FAIL {rel}: cannot read cite path {path_part}: {exc}")

    # --- SOFT: has each pinned file moved since its pin? -----------------------
    for pin in pins:
        if not isinstance(pin, dict):
            continue
        p, commit = pin.get("path"), pin.get("commit")
        if not p or not commit or not (root / p).exists():
            continue
        n = changed_since(root, p, commit)
        if n > 0:
            soft.append(
                f"⚠ drift: {rel} describes {p}, changed in {n} commit(s) "
                "since pin — re-review & re-pin"
            )

    return hard, soft, True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--guide-dir",
        default=None,
        help="guide docs root (default: <repo>/docs/guide)",
    )
    args = parser.parse_args(argv[1:])

    root = repo_root()
    guide_dir = Path(args.guide_dir) if args.guide_dir else root / "docs" / "guide"
    if not guide_dir.is_dir():
        print(f"no guide dir at {guide_dir} — nothing to check", file=sys.stderr)
        return 2

    docs = sorted(guide_dir.rglob("*.md"))
    if not docs:
        print(f"no docs under {guide_dir} — nothing to check", file=sys.stderr)
        return 2

    all_hard: list[str] = []
    all_soft: list[str] = []
    checked = 0
    skipped: list[str] = []

    for doc in docs:
        hard, soft, had_pins = check_doc(root, doc)
        if not had_pins:
            skipped.append(str(doc.relative_to(root)))
            continue
        checked += 1
        all_hard.extend(hard)
        all_soft.extend(soft)

    for line in all_hard:
        print(line)
    for line in all_soft:
        print(line)

    print()
    print(
        f"doc-freshness: {checked} doc(s) with pins checked, "
        f"{len(skipped)} without frontmatter/pins skipped, "
        f"{len(all_hard)} hard failure(s), {len(all_soft)} soft advisory(ies)"
    )
    if skipped:
        print("  skipped (no frontmatter/pins): " + ", ".join(skipped))

    return 1 if all_hard else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
