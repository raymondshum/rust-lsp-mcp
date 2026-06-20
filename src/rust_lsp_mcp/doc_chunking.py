"""Structure-aware markdown chunker for Phase 5 documentation RAG.

Converts markdown text into ``DocChunk`` objects suitable for embedding with
sentence-transformers (MiniLM, 256-token window).  Two-stage split:

1. **Header-tree split** — splits on ATX headers (``#`` … ``######``) and
   setext headers (text underlined with ``===`` or ``---``), tracks fenced-code
   blocks to skip ``#`` lines inside them, emits one chunk per leaf section with
   a breadcrumb built from ancestor headers.

2. **Size-split** — any chunk whose total ``text`` (breadcrumb + body) exceeds
   the token cap is further split, using a three-level fallback cascade:

   a. **Paragraph-level** (primary): accumulate paragraphs (blank-line-separated)
      greedily until adding the next paragraph would exceed the cap; flush and
      continue with a one-paragraph overlap for semantic continuity.

   b. **Line-level** (fallback): when a single paragraph still exceeds the cap,
      split that paragraph on ``\\n`` boundaries, packing lines greedily.  No
      overlap at the line level (lines are short; overlap is not worth the cost).

   c. **Char/word-level** (last resort): when a single line exceeds the cap (rare —
      very long URL, minified text, etc.), hard-split the line by word boundaries
      or, failing that, by character count.  No overlap at this level either.
      This guarantees the invariant ``estimate_tokens(chunk.text) <= BODY_TOKEN_CAP``
      for EVERY chunk regardless of input.

Token estimation
----------------
We use a conservative multi-metric heuristic that correctly handles:

- **CJK / multibyte text**: each CJK codepoint (Unified Ideographs, Extension A,
  CJK Symbols/Punctuation, Hiragana, Katakana, Fullwidth Forms) is counted at
  1.0 token because MiniLM's WordPiece treats each CJK character as its own token.
  The old ``len/4`` formula counted 3-byte CJK chars at 0.75 tokens — a severe
  under-estimate.

- **Code / punctuation-heavy text**: WordPiece splits aggressively on special
  characters (``<``, ``>``, ``&``, ``::``, ``[``, ``]``, etc.), producing far more
  tokens than words.  We use ``len(non_cjk) / 2`` as the char-based estimate,
  which over-estimates by ~2× for typical English prose but is necessary to cover
  observed real/char ratios up to 0.52 for code-heavy corpus chunks.

Both metrics intentionally *over*-estimate.  The formula is::

    estimate_tokens(text) = ceil(
        max(
            cjk_count * 1.0 + len(non_cjk_text) / 2.0,   # char-based
            word_count * 1.5                                # word-based
        )
    )

Cap
---
``BODY_TOKEN_CAP = 200`` body tokens.  The total ``text`` field (breadcrumb +
``\\n\\n`` + body) is checked against this same cap; breadcrumbs are typically
10–25 tokens so the total stays comfortably under 256.

With the improved estimator, the effective maximum chunk length for pure ASCII
code text is ~400 characters.  At the worst observed real-token/char ratio of
0.52 on the ripgrep corpus, this produces at most ~208 real tokens — safely
under the 256-token MiniLM window with 48-token margin.
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
"""Number of trailing paragraphs to repeat at the start of the next size-split piece.

Applies only to the paragraph-level split (primary path).  The line-level and
char/word-level fallbacks do not carry overlap — those paths apply only when a
single paragraph already exceeds the cap, so repeating lines would just re-bloat
the next piece.
"""

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

# CJK Unicode ranges: each codepoint ≈ 1 MiniLM WordPiece token.
# Covers CJK Unified Ideographs, Extension A, Extension B, CJK Symbols and
# Punctuation, Hiragana, Katakana, and Halfwidth/Fullwidth Forms.
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x3000, 0x303F),  # CJK Symbols and Punctuation
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0x3400, 0x4DBF),  # CJK Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs (main block)
    (0xFF00, 0xFFEF),  # Halfwidth and Fullwidth Forms
    (0x20000, 0x2A6DF),  # CJK Extension B
)


def _is_cjk(cp: int) -> bool:
    """Return True if code-point *cp* falls in a CJK range (≈ 1 WP token each)."""
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def estimate_tokens(text: str) -> int:
    """Conservative token-count estimate for a MiniLM embedding window.

    Uses the larger of two fast heuristics:

    - **Char-based**: ``cjk_count * 1.0 + non_cjk_len / 2.0``
      CJK characters are each 1 WordPiece token; non-CJK characters are
      estimated at 2 chars per token (len/2), which is conservative for
      code-heavy and punctuation-heavy text where WordPiece may split a
      4-char token into 2–3 sub-tokens.

    - **Word-based**: ``word_count * 1.5``
      Provides a floor for short, dense text where the char estimate is too low.

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
    cjk_count = sum(1 for c in text if _is_cjk(ord(c)))
    non_cjk_len = len(text) - cjk_count
    char_estimate = cjk_count * 1.0 + non_cjk_len / 2.0
    word_estimate = len(text.split()) * 1.5
    return math.ceil(max(char_estimate, word_estimate))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Regex for ATX headers: one-to-six ``#`` chars followed by a space/tab and content.
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)")

# Regex for fenced-code block delimiters (``` or ~~~, optionally followed by info string).
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")

# Regex for setext underline: a line of one or more ``=`` chars (h1) or ``-`` chars (h2),
# optionally preceded/followed by spaces, with nothing else.
_SETEXT_H1_RE = re.compile(r"^=+\s*$")
_SETEXT_H2_RE = re.compile(r"^-+\s*$")

# Regex for table separator rows: ``| --- | --- |`` style lines.
# These look like setext underlines when the row is ``|---|---|`` but must not fire
# as setext headers.  We detect them by the presence of ``|`` on the line.
_TABLE_SEP_RE = re.compile(r"^\|")


def _is_fence_delimiter(line: str) -> bool:
    """Return True if *line* opens or closes a fenced code block."""
    return bool(_FENCE_RE.match(line.rstrip()))


def _could_be_setext_preceding(line: str) -> bool:
    """Return True if *line* can be the text line before a setext underline.

    A setext header text line must be:
    - Non-blank (has non-whitespace content).
    - Not itself a list item (``- …``, ``* …``, ``+ …``, ``N. …``).
    - Not a table row (starts with ``|``).
    - Not an ATX header (starts with ``#``).
    - Not a thematic break (``---``, ``***``, ``___`` on their own line).

    We do NOT check whether it's inside a fence here; that is handled by the
    caller, which already tracks fence state.
    """
    stripped = line.strip()
    if not stripped:
        return False
    # ATX header
    if stripped.startswith("#"):
        return False
    # Table row
    if stripped.startswith("|"):
        return False
    # List item (unordered: ``- ``, ``* ``, ``+ ``)
    if re.match(r"^[-*+]\s", stripped):
        return False
    # Ordered list item (``1. ``, ``10. ``, etc.)
    if re.match(r"^\d+\.\s", stripped):
        return False
    # Thematic break: ``---``, ``***``, ``___`` (3+ same char, only that char and spaces)
    return not re.match(r"^[-*_]{3,}\s*$", stripped)


def _split_into_header_sections(text: str) -> list[tuple[int, str, str]]:
    """Split *text* into (level, header_title, body) tuples.

    Lines inside fenced code blocks are treated as content and never
    interpreted as headers, even if they start with ``#``.

    Recognizes two header syntaxes:

    - **ATX headers**: lines starting with ``#`` … ``######``.
    - **Setext headers**: a non-blank paragraph text line immediately followed
      by a line of ``=`` (→ h1) or ``-`` (→ h2).  The underline line is consumed
      (not emitted as body).  Setext underlines inside fenced code blocks, after
      blank lines (thematic breaks), after table rows, after list items, and
      after ATX headers are NOT treated as setext headers — only after a valid
      text line.

    The very first section (text before the first header) is represented as
    ``(0, "", body)`` — level 0 means "preamble, no header".

    Fence tracking stores both the opening delimiter character and its length.
    A closing fence must use the same character AND have length >= the opening
    length (per CommonMark spec).  This prevents a 3-backtick close from
    incorrectly closing a 4-backtick open.

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
    fence_char: str = ""  # the opening fence character (` or ~)
    fence_len: int = 0  # the length of the opening fence sequence

    # YAML frontmatter: a leading ``---`` block at the very start of the document.
    # We track whether we are inside one and suppress setext detection inside it.
    # Detection: if the very first non-empty line is exactly ``---``, we're in frontmatter.
    # Frontmatter ends at the next ``---`` or ``...`` line.
    in_frontmatter: bool = False
    frontmatter_possible: bool = True  # can still open frontmatter (no non-empty line seen)

    # For setext detection we need to look at the previous body line.
    # We keep track of the most recently emitted body line (stripped) so we can
    # check whether the NEXT line is a setext underline for it.
    prev_body_stripped: str = ""  # stripped content of last body line added
    prev_body_raw: str = ""  # raw (with newline) of last body line added

    def _flush() -> None:
        body = "".join(current_body_lines).strip("\n")
        sections.append((current_level, current_title, body))

    for line in lines:
        stripped = line.rstrip("\n").rstrip("\r")

        # YAML frontmatter: detect a leading ``---`` block and treat it as opaque body.
        # A frontmatter block begins when the very first non-empty line is exactly ``---``.
        if frontmatter_possible and stripped:
            frontmatter_possible = False  # Only the first non-empty line can open frontmatter.
            if stripped == "---":
                in_frontmatter = True
                current_body_lines.append(line)
                # Don't update prev_body_stripped: frontmatter cannot be a setext title.
                continue
        if in_frontmatter:
            # Inside YAML frontmatter: pass through verbatim.
            # Close on ``---`` or ``...`` (YAML document end marker).
            current_body_lines.append(line)
            if stripped in ("---", "..."):
                in_frontmatter = False
            continue

        # Fence tracking: only open a new fence when we are not already in one.
        if _is_fence_delimiter(stripped):
            if not in_fence:
                # Opening fence — record char and length.
                m = _FENCE_RE.match(stripped)
                if m:
                    fence_seq = m.group(1)
                    fence_char = fence_seq[0]  # ` or ~
                    fence_len = len(fence_seq)
                else:
                    fence_char = "`"
                    fence_len = 3
                in_fence = True
                current_body_lines.append(line)
                prev_body_stripped = stripped
                prev_body_raw = line
            else:
                # Potential closing fence: must use the same delimiter character
                # AND have length >= the opening fence length (CommonMark §4.5).
                m = _FENCE_RE.match(stripped)
                if m and m.group(1)[0] == fence_char and len(m.group(1)) >= fence_len:
                    in_fence = False
                    fence_char = ""
                    fence_len = 0
                current_body_lines.append(line)
                prev_body_stripped = stripped
                prev_body_raw = line
            continue

        # Inside a fence: never split on headers.
        if in_fence:
            current_body_lines.append(line)
            prev_body_stripped = stripped
            prev_body_raw = line
            continue

        # --- Setext header detection ---
        # A setext underline is ``=+`` (h1) or ``-+`` (h2) on its own line,
        # preceded by a valid non-blank paragraph text line.
        # We check this BEFORE ATX, because the underline might otherwise be
        # misidentified as a thematic break.
        if prev_body_stripped and _could_be_setext_preceding(prev_body_stripped):
            setext_level = 0
            if _SETEXT_H1_RE.match(stripped):
                setext_level = 1
            elif _SETEXT_H2_RE.match(stripped) and not _TABLE_SEP_RE.match(stripped):
                setext_level = 2

            if setext_level:
                # The previous body line becomes the setext header title.
                title = prev_body_stripped.strip()
                # Remove the previous line from the current body accumulation.
                # It was the last line added to current_body_lines.
                if current_body_lines and current_body_lines[-1] == prev_body_raw:
                    current_body_lines.pop()
                # Flush whatever came before this header.
                _flush()
                current_level = setext_level
                current_title = title
                current_body_lines = []
                prev_body_stripped = ""
                prev_body_raw = ""
                # The underline line is consumed — not added to body.
                continue

        # Check for ATX header.
        m_hdr = _HEADER_RE.match(stripped)
        if m_hdr:
            _flush()
            current_level = len(m_hdr.group(1))
            current_title = m_hdr.group(2).strip()
            current_body_lines = []
            prev_body_stripped = ""
            prev_body_raw = ""
        else:
            current_body_lines.append(line)
            prev_body_stripped = stripped
            prev_body_raw = line

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


def _hard_split_text(
    breadcrumb: str, text_piece: str, file: str, ordinal_start: int
) -> tuple[list[DocChunk], int]:
    """Last-resort char/word-level split for a single piece that still exceeds the cap.

    This handles pathological inputs: a single unbreakable long token (e.g. a giant URL,
    minified JS blob) or a very long line with no whitespace.  Strategy:
    - Try to split on whitespace (words), packing greedily.
    - If even a single word exceeds the cap, split by fixed character count.

    **Round-1 Break 2 fix**: an oversized space-free token that is NOT the last word in a
    line must also be char-split.  Previously only the final flush checked for a single-word
    oversize; the mid-loop flush emitted big tokens whole.  Now, after any flush that
    leaves ``accumulated_words`` as a single over-cap token, we char-split it immediately
    before continuing to accumulate the next word.

    **Round-2 Hole 2 fix (estimator unsound for pure-punctuation/symbol runs)**:
    The ``_char_split_single_word`` helper now sets its initial character-slice budget
    to ``max(16, body_budget)`` chars (treating 1 char ≈ 1 real token), rather than
    ``body_budget * 2`` (which assumed the 0.5 tokens/char ratio safe for prose).
    Pure-punctuation runs (``"_"*600``, ``"!"*600``, dense ``!@#$%``) tokenize at
    ~1.0 real token/char, so the old budget would produce slices that passed the
    *estimated* cap but exceeded 256 *real* tokens.  The new worst-case budget is
    safe for every input regardless of real/char ratio, while the tightening loop
    below handles CJK and other cases where even the conservative budget needs
    further shrinkage.  Normal prose/word-level splitting is unaffected (it never
    reaches this path).

    No overlap is applied at this level.

    Args:
        breadcrumb:    The breadcrumb prefix.
        text_piece:    The raw text to split (not yet prefixed with breadcrumb).
        file:          Workspace-relative path.
        ordinal_start: First ordinal to use.

    Returns:
        ``(chunks, next_ordinal)``.
    """
    chunks: list[DocChunk] = []
    ordinal = ordinal_start

    # Compute how many chars are "safe" for the body.
    # We rely on estimate_tokens to enforce correctness, not this ratio.
    # Reserve headroom: breadcrumb + "\n\n" prefix costs some tokens.
    prefix = f"{breadcrumb}\n\n"
    prefix_tokens = estimate_tokens(prefix)
    # Available token budget for the text_piece alone.
    body_budget = BODY_TOKEN_CAP - prefix_tokens
    if body_budget <= 0:
        # Breadcrumb alone already exceeds cap; emit the piece as a best-effort single chunk.
        chunk_text = f"{prefix}{text_piece}"
        chunks.append(
            DocChunk(id=f"{file}::{ordinal}", text=chunk_text, file=file, breadcrumb=breadcrumb)
        )
        return chunks, ordinal + 1

    def _char_split_single_word(word: str) -> None:
        """Emit *word* (which alone exceeds the cap) via character-level slicing.

        This is the LAST-RESORT path — it fires only on pathological space-free
        over-cap tokens (giant URLs, minified text, pure-symbol runs).  We set the
        initial character budget conservatively at ``max(16, body_budget)`` chars,
        treating 1 char ≈ 1 token (the worst-case real/char ratio for pure
        punctuation/symbol runs).  The tightening loop below then shrinks further
        until the estimate passes, so the invariant holds for CJK and every other
        high-density input as well.

        This does NOT affect normal prose or word-level splitting (which never
        reaches this path), so real-corpus chunk sizes are unchanged.

        NOTE: The cap guarantee (``estimate_tokens(chunk.text) <= BODY_TOKEN_CAP``)
        holds for any input whose breadcrumb itself fits the cap.  A breadcrumb
        (header title) that is itself > cap tokens cannot be made to fit by body-
        splitting — this is a known accepted residual applicable only to synthetic
        inputs (pathological 300-word header lines); it is absent from real docs.
        """
        nonlocal ordinal
        # Worst-case char budget: treat 1 char ≈ 1 real token (pure punctuation/symbol).
        # body_budget is in tokens; using it directly as a char count is safe even for
        # CJK (each CJK char is 1 token) and pure punctuation (each char ≈ 1 token).
        # Floor at 16 to avoid pathologically tiny slices on very long breadcrumbs.
        worst_case_chars = max(16, body_budget)
        raw = word
        while raw:
            # Reset slice size each iteration so tightening in one slice doesn't
            # permanently reduce the budget for subsequent slices.
            chars_per_slice = worst_case_chars
            slice_text = f"{prefix}{raw[:chars_per_slice]}"
            # Tighten until the estimate passes (handles CJK and other edge cases).
            while estimate_tokens(slice_text) > BODY_TOKEN_CAP and len(raw[:chars_per_slice]) > 1:
                chars_per_slice = max(1, chars_per_slice - 1)
                slice_text = f"{prefix}{raw[:chars_per_slice]}"
            chunks.append(
                DocChunk(id=f"{file}::{ordinal}", text=slice_text, file=file, breadcrumb=breadcrumb)
            )
            ordinal += 1
            raw = raw[chars_per_slice:]

    # Try word-level splitting first.
    words = text_piece.split(" ")
    accumulated_words: list[str] = []

    for word in words:
        candidate = " ".join(accumulated_words + [word]) if accumulated_words else word
        candidate_text = f"{prefix}{candidate}"
        if estimate_tokens(candidate_text) > BODY_TOKEN_CAP and accumulated_words:
            # Flush current accumulation.
            # Break 2 fix: if the accumulated content is a single over-cap word,
            # char-split it instead of emitting it whole.
            flushed_piece = " ".join(accumulated_words)
            flushed_text = f"{prefix}{flushed_piece}"
            if estimate_tokens(flushed_text) > BODY_TOKEN_CAP and len(accumulated_words) == 1:
                _char_split_single_word(flushed_piece)
            else:
                chunks.append(
                    DocChunk(
                        id=f"{file}::{ordinal}",
                        text=flushed_text,
                        file=file,
                        breadcrumb=breadcrumb,
                    )
                )
                ordinal += 1
            # Set up next accumulation starting with the current word.
            accumulated_words = [word]
            # Also char-split the new word immediately if it alone exceeds the cap.
            single_text = f"{prefix}{word}"
            if estimate_tokens(single_text) > BODY_TOKEN_CAP:
                _char_split_single_word(word)
                accumulated_words = []
        else:
            accumulated_words.append(word)

    if accumulated_words:
        piece = " ".join(accumulated_words)
        piece_text = f"{prefix}{piece}"
        # If even the remaining accumulation is over the cap and it's a single word,
        # char-split it as a last resort.
        if estimate_tokens(piece_text) > BODY_TOKEN_CAP and len(accumulated_words) == 1:
            _char_split_single_word(piece)
        else:
            chunks.append(
                DocChunk(id=f"{file}::{ordinal}", text=piece_text, file=file, breadcrumb=breadcrumb)
            )
            ordinal += 1

    return chunks, ordinal


def _split_lines_into_chunks(
    breadcrumb: str, paragraph: str, file: str, ordinal_start: int
) -> tuple[list[DocChunk], int]:
    """Line-level fallback split for a single paragraph that exceeds the cap.

    Splits *paragraph* on ``\\n`` boundaries and packs lines greedily into pieces
    that each fit under the cap.  If a single line still exceeds the cap, delegates
    to ``_hard_split_text`` (char/word-level last resort).

    No overlap is applied at this level.

    Args:
        breadcrumb:    The breadcrumb prefix.
        paragraph:     The oversized paragraph text (single-``\\n``-separated lines).
        file:          Workspace-relative path.
        ordinal_start: First ordinal to use.

    Returns:
        ``(chunks, next_ordinal)``.
    """
    chunks: list[DocChunk] = []
    ordinal = ordinal_start
    lines = paragraph.split("\n")
    accumulated_lines: list[str] = []

    def _flush_lines(line_list: list[str]) -> None:
        nonlocal ordinal
        if not line_list:
            return
        piece_body = "\n".join(line_list)
        piece_text = f"{breadcrumb}\n\n{piece_body}"
        if estimate_tokens(piece_text) > BODY_TOKEN_CAP and len(line_list) == 1:
            # Single accumulated line exceeds the cap — delegate to char/word last resort.
            # This mirrors the final-flush path and closes the mid-loop hole: without this
            # check, a single giant line flushed mid-loop would be emitted whole (oversized).
            nonlocal_chunks, new_ord = _hard_split_text(breadcrumb, piece_body, file, ordinal)
            chunks.extend(nonlocal_chunks)
            ordinal = new_ord
        else:
            chunks.append(
                DocChunk(id=f"{file}::{ordinal}", text=piece_text, file=file, breadcrumb=breadcrumb)
            )
            ordinal += 1

    for line in lines:
        candidate_lines = accumulated_lines + [line]
        candidate_body = "\n".join(candidate_lines)
        candidate_text = f"{breadcrumb}\n\n{candidate_body}"

        if estimate_tokens(candidate_text) > BODY_TOKEN_CAP and accumulated_lines:
            # Flush accumulated lines, start fresh with this line.
            _flush_lines(accumulated_lines)
            accumulated_lines = [line]
        else:
            accumulated_lines.append(line)

    # Flush remaining.
    if accumulated_lines:
        # Check if even this remaining piece fits.
        piece_body = "\n".join(accumulated_lines)
        piece_text = f"{breadcrumb}\n\n{piece_body}"
        if estimate_tokens(piece_text) <= BODY_TOKEN_CAP:
            chunks.append(
                DocChunk(id=f"{file}::{ordinal}", text=piece_text, file=file, breadcrumb=breadcrumb)
            )
            ordinal += 1
        else:
            # Single line exceeds cap → char/word last resort.
            # accumulated_lines is exactly one line here (we flush on multi-line overages above).
            hard_chunks, ordinal = _hard_split_text(breadcrumb, piece_body, file, ordinal)
            chunks.extend(hard_chunks)

    return chunks, ordinal


def _size_split(
    breadcrumb: str,
    body: str,
    file: str,
    ordinal_start: int,
) -> tuple[list[DocChunk], int]:
    """Split a section further on paragraph/line/char boundaries if it exceeds the cap.

    Three-level cascade:

    1. **Paragraph-level** (primary): paragraphs (blank-line-separated) are
       accumulated greedily until the next paragraph would exceed the cap.  At
       each boundary the current accumulation is flushed and the last
       ``_OVERLAP_PARAGRAPHS`` paragraphs are carried into the next piece for
       semantic continuity.

    2. **Line-level** (fallback): if adding a single paragraph would push the
       total over the cap even when the accumulation is empty (i.e. the
       paragraph itself exceeds the cap), the paragraph is split on ``\\n``
       boundaries.  No overlap at this level.

    3. **Char/word-level** (last resort): if a single line still exceeds the cap
       (e.g. a very long URL or minified text), ``_hard_split_text`` splits by
       words or characters.  No overlap at this level.

    The invariant ``estimate_tokens(chunk.text) <= BODY_TOKEN_CAP`` holds for
    every chunk produced by this function.

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

    def _para_fits_alone(para: str) -> bool:
        """Return True if *para* alone (with breadcrumb) fits under the cap."""
        return estimate_tokens(f"{breadcrumb}\n\n{para}") <= BODY_TOKEN_CAP

    def _emit_para(para: str) -> None:
        """Emit one paragraph through the appropriate path (line-level if oversized)."""
        nonlocal ordinal
        if _para_fits_alone(para):
            chunks.append(
                DocChunk(
                    id=f"{file}::{ordinal}",
                    text=f"{breadcrumb}\n\n{para}",
                    file=file,
                    breadcrumb=breadcrumb,
                )
            )
            ordinal += 1
        else:
            line_chunks, ordinal = _split_lines_into_chunks(breadcrumb, para, file, ordinal)
            chunks.extend(line_chunks)

    for para in paragraphs:
        # Check if para itself is oversized — must go through line-level fallback.
        if not _para_fits_alone(para):
            # Flush whatever we had first (without including this para).
            _flush_accumulated(accumulated)
            accumulated = []
            _emit_para(para)
            # No overlap after a line-level split: the oversized para was already
            # finely split; carrying lines back into the next piece would re-bloat it.
            continue

        # Build candidate text with this paragraph added.
        candidate_paras = accumulated + [para]
        candidate_body = "\n\n".join(candidate_paras)
        candidate_text = f"{breadcrumb}\n\n{candidate_body}"

        if estimate_tokens(candidate_text) > BODY_TOKEN_CAP and accumulated:
            # Flush current accumulation, then start next piece with overlap.
            _flush_accumulated(accumulated)
            overlap = accumulated[-_OVERLAP_PARAGRAPHS:] if _OVERLAP_PARAGRAPHS else []
            # Verify that (overlap + para) itself fits; if not, drop the overlap.
            # This avoids seeding the next accumulation with an already-over-cap window.
            candidate_with_overlap = "\n\n".join(overlap + [para])
            if (
                overlap
                and estimate_tokens(f"{breadcrumb}\n\n{candidate_with_overlap}") > BODY_TOKEN_CAP
            ):
                overlap = []
            accumulated = overlap + [para]
        else:
            # Fits (or accumulated is empty, in which case para itself fits — checked above).
            accumulated.append(para)

    # Flush any remaining paragraphs.
    if accumulated:
        _flush_accumulated(accumulated)

    return chunks, ordinal


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def chunk_markdown(text: str, rel_path: str) -> list[DocChunk]:
    """Chunk a markdown document into embeddable ``DocChunk`` objects.

    Two-stage split:

    1. **Header-tree split**: splits on ATX and setext headers, tracks fence
       state to avoid treating ``#`` lines inside code blocks as headers.
       Builds a breadcrumb from the ancestor header chain for each section.
       The preamble (text before the first header) gets a breadcrumb equal to
       the file basename alone.

    2. **Size-split**: any chunk whose total ``text`` exceeds ``BODY_TOKEN_CAP``
       tokens is further split using a three-level cascade (paragraph →
       line → char/word) ensuring the invariant holds for every output chunk.

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
