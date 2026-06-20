"""Structure-aware markdown chunker for Phase 5 documentation RAG.

Converts markdown text into ``DocChunk`` objects suitable for embedding with
sentence-transformers (MiniLM, 256-token window).  Two-stage split:

1. **Header-tree split** — splits on ATX headers (``#`` … ``######``), tracks
   fenced-code blocks to skip ``#`` lines inside them, emits one chunk per leaf
   section with a breadcrumb built from ancestor headers.

2. **Size-split** — any chunk whose total ``text`` (breadcrumb + body) exceeds
   the token cap is further split on paragraph boundaries, with a small overlap
   (one trailing paragraph) across consecutive pieces.

Token estimation
----------------
We use a conservative two-metric heuristic::

    estimate_tokens(text) = ceil(max(len(text) / 4, word_count * 1.3))

Both metrics tend to *over*-estimate for typical English prose.  Over-estimating
is safe: the worst outcome is a chunk being split one level finer than necessary.
Under-estimating would cause silent truncation by the MiniLM embedder, which is
the primary correctness risk for this phase.

Cap
---
``BODY_TOKEN_CAP = 200`` body tokens.  The total ``text`` field (breadcrumb +
``\\n\\n`` + body) is checked against this same cap; breadcrumbs are typically
10–25 tokens so the total stays comfortably under 256.
"""

import math
import os
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

BODY_TOKEN_CAP: int = 200
"""Target cap for the total ``text`` field (breadcrumb + body) in tokens.

Keeps total chunk length comfortably under the MiniLM 256-token window.
"""

_OVERLAP_PARAGRAPHS: int = 1
"""Number of trailing paragraphs to repeat at the start of the next size-split piece."""

# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocChunk:
    """A single embeddable chunk produced by ``chunk_markdown``.

    Attributes:
        id:         Stable, unique identifier within one rebuild.
                    Format: ``"{rel_path}::{ordinal}"`` (0-indexed).
        text:       The text that gets embedded: ``"{breadcrumb}\\n\\n{body}"``.
        file:       Workspace-relative path — exactly the ``rel_path`` passed to
                    ``chunk_markdown``.
        breadcrumb: Ancestor-header chain, e.g.
                    ``"GUIDE.md > Configuration > Ignoring files"``.
    """

    id: str
    text: str
    file: str
    breadcrumb: str


# ---------------------------------------------------------------------------
# Token estimator
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Conservative token-count estimate for a MiniLM embedding window.

    Uses the larger of two fast heuristics:
    - ``len(text) / 4``  — character-based (works well for ASCII/code-heavy text).
    - ``word_count * 1.3`` — word-based (works better for short dense text).

    Both metrics intentionally *over*-estimate.  Over-estimation is safe
    (it causes at most an extra split); under-estimation risks silent truncation
    by the embedder.  We take ``math.ceil`` of the larger metric.

    Args:
        text: The string to estimate.

    Returns:
        Conservative integer upper-bound token estimate.
    """
    if not text:
        return 0
    char_estimate = len(text) / 4.0
    word_estimate = len(text.split()) * 1.3
    return math.ceil(max(char_estimate, word_estimate))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Regex for ATX headers: one-to-six ``#`` chars followed by a space/tab and content.
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)")

# Regex for fenced-code block delimiters (``` or ~~~, optionally followed by info string).
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


def _is_fence_delimiter(line: str) -> bool:
    """Return True if *line* opens or closes a fenced code block."""
    return bool(_FENCE_RE.match(line.rstrip()))


def _split_into_header_sections(text: str) -> list[tuple[int, str, str]]:
    """Split *text* into (level, header_title, body) tuples.

    Lines inside fenced code blocks are treated as content and never
    interpreted as headers, even if they start with ``#``.

    The very first section (text before the first header) is represented as
    ``(0, "", body)`` — level 0 means "preamble, no header".

    Args:
        text: Raw markdown string.

    Returns:
        List of ``(level, title, body)`` tuples in document order.
        ``level`` is 1–6 for ATX headers; 0 for the preamble section.
    """
    sections: list[tuple[int, str, str]] = []
    lines = text.splitlines(keepends=True)

    current_level: int = 0
    current_title: str = ""
    current_body_lines: list[str] = []
    in_fence: bool = False
    fence_char: str = ""  # the opening fence character sequence (``` or ~~~)

    def _flush() -> None:
        body = "".join(current_body_lines).strip("\n")
        sections.append((current_level, current_title, body))

    for line in lines:
        stripped = line.rstrip("\n").rstrip("\r")

        # Fence tracking: only open a new fence when we are not already in one.
        if _is_fence_delimiter(stripped):
            if not in_fence:
                # Opening fence.
                m = _FENCE_RE.match(stripped)
                fence_char = m.group(1)[0] if m else "`"  # ` or ~
                in_fence = True
                current_body_lines.append(line)
            else:
                # Potential closing fence: must use the same delimiter character
                # and be at least as long as the opening fence.
                m = _FENCE_RE.match(stripped)
                if m and m.group(1)[0] == fence_char:
                    in_fence = False
                    fence_char = ""
                current_body_lines.append(line)
            continue

        # Inside a fence: never split on headers.
        if in_fence:
            current_body_lines.append(line)
            continue

        # Check for ATX header.
        m_hdr = _HEADER_RE.match(stripped)
        if m_hdr:
            _flush()
            current_level = len(m_hdr.group(1))
            current_title = m_hdr.group(2).strip()
            current_body_lines = []
        else:
            current_body_lines.append(line)

    # Flush the last section.
    _flush()
    return sections


def _build_breadcrumb(basename: str, ancestor_stack: list[str]) -> str:
    """Build a breadcrumb string from a basename and an ancestor-title stack.

    Args:
        basename:       ``os.path.basename(rel_path)`` for the document.
        ancestor_stack: Ordered list of ancestor header titles from outermost
                        (h1) down to and *including* the current section title.
                        Pass ``[]`` for the preamble (no header).

    Returns:
        ``"basename"`` (preamble) or ``"basename > h1 > h2 > ..."`` (headed section).
    """
    if not ancestor_stack:
        return basename
    return " > ".join([basename] + ancestor_stack)


def _split_paragraphs(body: str) -> list[str]:
    """Split *body* on blank lines, returning a list of non-empty paragraph strings.

    Each returned paragraph preserves its internal newlines but has no leading
    or trailing blank lines.

    Args:
        body: Body text (no breadcrumb prefix).

    Returns:
        List of paragraph strings; empty list if *body* is whitespace-only.
    """
    # Split on one-or-more blank lines.
    raw_paragraphs = re.split(r"\n{2,}", body)
    return [p.strip("\n") for p in raw_paragraphs if p.strip()]


def _size_split(
    breadcrumb: str,
    body: str,
    file: str,
    ordinal_start: int,
) -> tuple[list[DocChunk], int]:
    """Split a section further on paragraph boundaries if it exceeds the cap.

    Paragraphs are accumulated greedily until the next paragraph would push the
    total ``text`` (breadcrumb + ``\\n\\n`` + accumulated) over the cap.  At
    each boundary the current accumulation is flushed as a chunk, and the last
    ``_OVERLAP_PARAGRAPHS`` paragraphs are carried into the next piece for
    semantic continuity.

    If the body is short enough (after prepending the breadcrumb the total is
    under the cap), it is emitted as a single chunk with no further splitting.

    Args:
        breadcrumb:    The breadcrumb string for this section.
        body:          The raw section body text (no breadcrumb).
        file:          Workspace-relative path.
        ordinal_start: The first ordinal to assign to chunks produced here.

    Returns:
        ``(chunks, next_ordinal)`` where ``chunks`` is the list of ``DocChunk``
        objects produced and ``next_ordinal`` is the next available ordinal.
    """
    full_text = f"{breadcrumb}\n\n{body}" if body else breadcrumb
    if estimate_tokens(full_text) <= BODY_TOKEN_CAP:
        chunk = DocChunk(
            id=f"{file}::{ordinal_start}",
            text=full_text,
            file=file,
            breadcrumb=breadcrumb,
        )
        return [chunk], ordinal_start + 1

    # Need to size-split on paragraph boundaries.
    paragraphs = _split_paragraphs(body)
    if not paragraphs:
        # Body was whitespace-only but breadcrumb alone is under cap — emit as-is.
        chunk = DocChunk(
            id=f"{file}::{ordinal_start}",
            text=full_text,
            file=file,
            breadcrumb=breadcrumb,
        )
        return [chunk], ordinal_start + 1

    chunks: list[DocChunk] = []
    ordinal = ordinal_start
    accumulated: list[str] = []

    def _flush_accumulated(paras: list[str]) -> None:
        nonlocal ordinal
        if not paras:
            return
        piece_body = "\n\n".join(paras)
        piece_text = f"{breadcrumb}\n\n{piece_body}"
        chunks.append(
            DocChunk(
                id=f"{file}::{ordinal}",
                text=piece_text,
                file=file,
                breadcrumb=breadcrumb,
            )
        )
        ordinal += 1

    for para in paragraphs:
        # Build candidate text with this paragraph added.
        candidate_paras = accumulated + [para]
        candidate_body = "\n\n".join(candidate_paras)
        candidate_text = f"{breadcrumb}\n\n{candidate_body}"

        if estimate_tokens(candidate_text) > BODY_TOKEN_CAP and accumulated:
            # Flush current accumulation, then start next piece with overlap.
            _flush_accumulated(accumulated)
            overlap = accumulated[-_OVERLAP_PARAGRAPHS:] if _OVERLAP_PARAGRAPHS else []
            accumulated = overlap + [para]
        else:
            accumulated.append(para)

    # Flush any remaining paragraphs.
    if accumulated:
        _flush_accumulated(accumulated)

    # Edge case: a single paragraph exceeded the cap — it must still be emitted
    # as one chunk (we cannot split finer than paragraph level here).
    return chunks, ordinal


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def chunk_markdown(text: str, rel_path: str) -> list[DocChunk]:
    """Chunk a markdown document into embeddable ``DocChunk`` objects.

    Two-stage split:

    1. **Header-tree split**: splits on ATX headers, tracks fence state to
       avoid treating ``#`` lines inside code blocks as headers.  Builds a
       breadcrumb from the ancestor header chain for each section.  The preamble
       (text before the first header) gets a breadcrumb equal to the file
       basename alone.

    2. **Size-split**: any chunk whose total ``text`` exceeds ``BODY_TOKEN_CAP``
       tokens is further split on paragraph boundaries with a small overlap
       (``_OVERLAP_PARAGRAPHS``) across consecutive pieces.

    Fenced code blocks (``` or ~~~) are preserved verbatim; inline code spans
    (backtick-delimited) are never touched.

    Args:
        text:     Raw markdown string.
        rel_path: Workspace-relative path to the document.  Used verbatim as
                  ``DocChunk.file`` and as the basis for ``id`` ordinals.

    Returns:
        List of ``DocChunk`` objects in document order.  Empty list if *text*
        is empty or whitespace-only.
    """
    if not text or not text.strip():
        return []

    basename = os.path.basename(rel_path)
    sections = _split_into_header_sections(text)

    chunks: list[DocChunk] = []
    ordinal = 0
    # Stack of (level, title) for ancestor headers.
    header_stack: list[tuple[int, str]] = []

    for level, title, body in sections:
        # Update ancestor stack.
        if level == 0:
            # Preamble — no header at all; reset stack for subsequent headers.
            header_stack = []
        else:
            # Pop any same-level or deeper ancestors off the stack.
            while header_stack and header_stack[-1][0] >= level:
                header_stack.pop()
            header_stack.append((level, title))

        # Build breadcrumb from stack titles only.
        titles = [t for _, t in header_stack]
        breadcrumb = _build_breadcrumb(basename, titles)

        # Skip entirely empty sections (no body and no meaningful breadcrumb).
        if not body.strip() and level == 0:
            continue

        new_chunks, ordinal = _size_split(breadcrumb, body, rel_path, ordinal)
        chunks.extend(new_chunks)

    return chunks
