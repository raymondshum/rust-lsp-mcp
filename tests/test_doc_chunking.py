"""Fast-tier unit tests for the doc_chunking module.

No I/O, no network, no ChromaDB.  All tests use small inline markdown strings.
Runs under ``pytest -m "not integration"`` (these tests carry no markers).

Coverage:
    - Empty / whitespace-only input → empty list.
    - Preamble (text before first header) → chunk with basename breadcrumb.
    - Single header → one chunk, correct breadcrumb.
    - Nested headers (h1 > h2 > h3) → correct breadcrumb at each level.
    - Fenced code block containing ``#`` lines → ``#`` inside fence NOT a header.
    - Tilde fenced block (~~~) → same protection as backtick fence.
    - Inline backtick code spans preserved verbatim.
    - Fenced code block body preserved verbatim in chunk text.
    - Size-split: body exceeding cap is split on paragraph boundaries.
    - Size-split: no chunk's text exceeds the cap (load-bearing assertion).
    - Size-split: single large paragraph (far over cap) triggers line-level fallback.
    - Size-split: bullet-list block with single-newline separators stays under cap.
    - Size-split: single unbreakable over-cap line triggers char/word last-resort.
    - Overlap: size-split pieces share trailing paragraph.
    - Unique, stable ids across all chunks of one document.
    - estimate_tokens: empty string → 0.
    - estimate_tokens: conservative (never under-counts for common inputs).
    - DocChunk is frozen (immutable after creation).
    - rel_path is preserved verbatim in chunk.file.
    - Fence length tracking: 4-backtick fence is not closed by 3-backtick line.
"""

import math

import pytest

from rust_lsp_mcp.doc_chunking import (
    _OVERLAP_PARAGRAPHS,
    BODY_TOKEN_CAP,
    DocChunk,
    _split_into_header_sections,
    chunk_markdown,
    estimate_tokens,
)

# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty_string_returns_zero(self) -> None:
        assert estimate_tokens("") == 0

    def test_single_word(self) -> None:
        # One word: char estimate = len/2 = 2.5 → 3; word estimate = 1 * 1.5 = 1.5 → 2.
        # max(3, 2) = 3.
        result = estimate_tokens("hello")
        assert result >= 1

    def test_conservative_vs_character_only(self) -> None:
        # The estimate must be >= ceil(non_cjk_len / 2) for pure ASCII text.
        text = " ".join(["a"] * 100)  # 100 words, ~200 chars, all ASCII
        expected_floor = math.ceil(len(text) / 2)
        assert estimate_tokens(text) >= expected_floor

    def test_long_prose_returns_positive(self) -> None:
        text = "This is a sentence. " * 50
        assert estimate_tokens(text) > 0

    def test_returns_integer(self) -> None:
        assert isinstance(estimate_tokens("hello world"), int)

    def test_never_underestimates_character_heuristic(self) -> None:
        # For pure ASCII text, the result must be >= ceil(len(text) / 2).
        for text in ["a", "hello", "word " * 20, "x" * 1000]:
            expected_floor = math.ceil(len(text) / 2)
            assert estimate_tokens(text) >= expected_floor, f"Failed for {text!r}"

    def test_never_underestimates_word_heuristic(self) -> None:
        # The result must be >= ceil(word_count * 1.5).
        text = "one two three four five"
        words = len(text.split())
        expected_floor = math.ceil(words * 1.5)
        assert estimate_tokens(text) >= expected_floor

    def test_cjk_counted_at_one_token_per_char(self) -> None:
        # Each CJK character should count as approximately 1 token.
        # 100 CJK chars → estimate >= 100.
        cjk_text = "字" * 100
        result = estimate_tokens(cjk_text)
        assert result >= 100, (
            f"CJK text (100 chars) estimated at {result} tokens; expected >= 100 "
            "(each CJK char ≈ 1 WordPiece token)"
        )

    def test_cjk_estimate_exceeds_ascii_estimate_for_same_byte_length(self) -> None:
        # A 100-char CJK string should estimate more tokens than a 100-char ASCII string,
        # because CJK chars are each 1 token while ASCII is ~0.5 tokens/char.
        cjk_text = "字" * 100
        ascii_text = "a" * 100
        assert estimate_tokens(cjk_text) > estimate_tokens(ascii_text), (
            "CJK estimate should exceed ASCII estimate for equal character counts"
        )


# ---------------------------------------------------------------------------
# DocChunk dataclass
# ---------------------------------------------------------------------------


class TestDocChunk:
    def test_is_frozen(self) -> None:
        chunk = DocChunk(id="f::0", text="hello", file="f", breadcrumb="f")
        with pytest.raises((AttributeError, TypeError)):
            chunk.id = "f::1"  # type: ignore[misc]  # ty: ignore[invalid-assignment]

    def test_fields_accessible(self) -> None:
        chunk = DocChunk(
            id="path/to/doc.md::3", text="bc\n\nbody", file="path/to/doc.md", breadcrumb="bc"
        )
        assert chunk.id == "path/to/doc.md::3"
        assert chunk.text == "bc\n\nbody"
        assert chunk.file == "path/to/doc.md"
        assert chunk.breadcrumb == "bc"


# ---------------------------------------------------------------------------
# Empty / whitespace input
# ---------------------------------------------------------------------------


class TestEmptyInput:
    def test_empty_string_returns_empty_list(self) -> None:
        assert chunk_markdown("", "doc.md") == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        assert chunk_markdown("   \n\n\t\n", "doc.md") == []

    def test_newlines_only_returns_empty_list(self) -> None:
        assert chunk_markdown("\n\n\n", "doc.md") == []


# ---------------------------------------------------------------------------
# Preamble (text before first header)
# ---------------------------------------------------------------------------


class TestPreamble:
    _MD = "This is some introductory text.\n\nAnother paragraph here.\n"

    def test_preamble_produces_one_chunk(self) -> None:
        chunks = chunk_markdown(self._MD, "README.md")
        assert len(chunks) == 1

    def test_preamble_breadcrumb_is_basename(self) -> None:
        chunks = chunk_markdown(self._MD, "docs/README.md")
        assert chunks[0].breadcrumb == "README.md"

    def test_preamble_file_is_rel_path(self) -> None:
        chunks = chunk_markdown(self._MD, "docs/README.md")
        assert chunks[0].file == "docs/README.md"

    def test_preamble_text_contains_body(self) -> None:
        chunks = chunk_markdown(self._MD, "README.md")
        assert "introductory text" in chunks[0].text

    def test_preamble_text_starts_with_breadcrumb(self) -> None:
        chunks = chunk_markdown(self._MD, "README.md")
        assert chunks[0].text.startswith("README.md")

    def test_preamble_id_is_ordinal_zero(self) -> None:
        chunks = chunk_markdown(self._MD, "README.md")
        assert chunks[0].id == "README.md::0"


# ---------------------------------------------------------------------------
# Single header
# ---------------------------------------------------------------------------


class TestSingleHeader:
    _MD = "# Introduction\n\nSome content under the introduction.\n"

    def test_single_header_produces_one_chunk(self) -> None:
        chunks = chunk_markdown(self._MD, "guide.md")
        assert len(chunks) == 1

    def test_single_header_breadcrumb(self) -> None:
        chunks = chunk_markdown(self._MD, "guide.md")
        assert chunks[0].breadcrumb == "guide.md > Introduction"

    def test_single_header_text_structure(self) -> None:
        chunks = chunk_markdown(self._MD, "guide.md")
        # text = breadcrumb + "\n\n" + body
        assert chunks[0].text.startswith("guide.md > Introduction\n\n")
        assert "Some content" in chunks[0].text

    def test_file_preserved(self) -> None:
        chunks = chunk_markdown(self._MD, "sub/guide.md")
        assert chunks[0].file == "sub/guide.md"


# ---------------------------------------------------------------------------
# Nested headers / breadcrumb correctness
# ---------------------------------------------------------------------------


class TestNestedHeaders:
    _MD = (
        "# Chapter One\n\nChapter intro.\n\n"
        "## Section A\n\nSection A content.\n\n"
        "### Subsection A1\n\nSubsection content.\n\n"
        "## Section B\n\nSection B content.\n"
    )

    def _chunks(self) -> list[DocChunk]:
        return chunk_markdown(self._MD, "book.md")

    def test_chunk_count(self) -> None:
        # h1, h2 A, h3 A1, h2 B = 4 sections (all non-empty)
        chunks = self._chunks()
        assert len(chunks) == 4

    def test_h1_breadcrumb(self) -> None:
        chunks = self._chunks()
        assert chunks[0].breadcrumb == "book.md > Chapter One"

    def test_h2_breadcrumb(self) -> None:
        chunks = self._chunks()
        assert chunks[1].breadcrumb == "book.md > Chapter One > Section A"

    def test_h3_breadcrumb(self) -> None:
        chunks = self._chunks()
        assert chunks[2].breadcrumb == "book.md > Chapter One > Section A > Subsection A1"

    def test_h2_after_h3_resets_to_h1_scope(self) -> None:
        """Section B is h2 under Chapter One — breadcrumb must NOT include Section A."""
        chunks = self._chunks()
        assert chunks[3].breadcrumb == "book.md > Chapter One > Section B"
        assert "Section A" not in chunks[3].breadcrumb

    def test_all_ids_unique(self) -> None:
        chunks = self._chunks()
        ids = [c.id for c in chunks]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Fenced code blocks — # inside fence is NOT a header
# ---------------------------------------------------------------------------


class TestFencedCodeBlocks:
    _MD_BACKTICK = (
        "## Shell Usage\n\n"
        "Run the following:\n\n"
        "```bash\n"
        "# this is a shell comment, not a header\n"
        "echo hello\n"
        "```\n\n"
        "More text after fence.\n"
    )

    _MD_TILDE = (
        "## Shell Usage\n\n"
        "Run the following:\n\n"
        "~~~bash\n"
        "# tilde fence shell comment\n"
        "echo hello\n"
        "~~~\n\n"
        "More text after tilde fence.\n"
    )

    def test_backtick_fence_hash_not_split(self) -> None:
        chunks = chunk_markdown(self._MD_BACKTICK, "doc.md")
        # Should be exactly ONE chunk (the h2 section), not two
        assert len(chunks) == 1

    def test_backtick_fence_breadcrumb_correct(self) -> None:
        chunks = chunk_markdown(self._MD_BACKTICK, "doc.md")
        assert chunks[0].breadcrumb == "doc.md > Shell Usage"

    def test_backtick_fence_body_preserved(self) -> None:
        chunks = chunk_markdown(self._MD_BACKTICK, "doc.md")
        assert "# this is a shell comment, not a header" in chunks[0].text
        assert "echo hello" in chunks[0].text

    def test_tilde_fence_hash_not_split(self) -> None:
        chunks = chunk_markdown(self._MD_TILDE, "doc.md")
        assert len(chunks) == 1

    def test_tilde_fence_body_preserved(self) -> None:
        chunks = chunk_markdown(self._MD_TILDE, "doc.md")
        assert "# tilde fence shell comment" in chunks[0].text

    def test_hash_after_closing_fence_is_a_header(self) -> None:
        md = "```\n# not a header\n```\n# Real Header\n\nContent.\n"
        chunks = chunk_markdown(md, "doc.md")
        # The preamble (before "# Real Header") should be one chunk (if non-empty),
        # and "# Real Header" should produce another.
        headers = [c for c in chunks if "Real Header" in c.breadcrumb]
        assert len(headers) == 1

    def test_nested_fence_content_not_split(self) -> None:
        """Multiple ``#`` lines inside a single fence stay in one chunk."""
        md = "## Config\n\n```toml\n# [section]\n# key = value\n```\n"
        chunks = chunk_markdown(md, "cfg.md")
        assert len(chunks) == 1
        assert "# [section]" in chunks[0].text
        assert "# key = value" in chunks[0].text


# ---------------------------------------------------------------------------
# Inline code span preservation
# ---------------------------------------------------------------------------


class TestInlineCodePreservation:
    def test_inline_backtick_preserved(self) -> None:
        md = "## Options\n\nUse `--ignore` to skip files.\n"
        chunks = chunk_markdown(md, "doc.md")
        assert len(chunks) == 1
        assert "`--ignore`" in chunks[0].text

    def test_inline_code_with_hash_inside_not_header(self) -> None:
        # Inline code spans are NOT fences — our chunker doesn't parse inline
        # spans, but the heading parser only matches lines starting with #.
        # A line like "Use `#id` for anchors" does NOT start with # so it's safe.
        md = "## Anchors\n\nUse `#id` for anchors.\n"
        chunks = chunk_markdown(md, "doc.md")
        assert len(chunks) == 1
        assert "`#id`" in chunks[0].text


# ---------------------------------------------------------------------------
# Size-split: token cap enforcement
# ---------------------------------------------------------------------------


class TestSizeSplit:
    def _long_body(self, paragraphs: int = 20, words_per_para: int = 30) -> str:
        """Produce a body with enough paragraphs to exceed BODY_TOKEN_CAP."""
        para = " ".join(["word"] * words_per_para)
        return "\n\n".join([para] * paragraphs)

    def test_short_section_not_split(self) -> None:
        md = "## Short\n\nBrief content.\n"
        chunks = chunk_markdown(md, "doc.md")
        assert len(chunks) == 1

    def test_long_section_splits_into_multiple_chunks(self) -> None:
        body = self._long_body()
        md = f"## Long Section\n\n{body}\n"
        chunks = chunk_markdown(md, "doc.md")
        assert len(chunks) > 1

    def test_no_chunk_exceeds_cap(self) -> None:
        """Critical load-bearing test: every chunk's text must be under the cap."""
        body = self._long_body(paragraphs=30, words_per_para=40)
        md = f"## Big Section\n\n{body}\n"
        chunks = chunk_markdown(md, "doc.md")
        for chunk in chunks:
            tok = estimate_tokens(chunk.text)
            assert tok <= BODY_TOKEN_CAP, (
                f"Chunk {chunk.id!r} has {tok} tokens (cap={BODY_TOKEN_CAP}). "
                f"First 80 chars: {chunk.text[:80]!r}"
            )

    def test_size_split_chunks_share_breadcrumb(self) -> None:
        body = self._long_body()
        md = f"## Repeated Breadcrumb\n\n{body}\n"
        chunks = chunk_markdown(md, "doc.md")
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.breadcrumb == "doc.md > Repeated Breadcrumb"

    def test_size_split_ids_are_unique(self) -> None:
        body = self._long_body()
        md = f"## Big\n\n{body}\n"
        chunks = chunk_markdown(md, "doc.md")
        ids = [c.id for c in chunks]
        assert len(ids) == len(set(ids))

    def test_size_split_ordinals_are_sequential(self) -> None:
        body = self._long_body()
        md = f"## Big\n\n{body}\n"
        chunks = chunk_markdown(md, "doc.md")
        ordinals = [int(c.id.split("::")[-1]) for c in chunks]
        assert ordinals == list(range(len(ordinals)))

    def test_size_split_file_preserved(self) -> None:
        body = self._long_body()
        md = f"## Big\n\n{body}\n"
        chunks = chunk_markdown(md, "path/to/doc.md")
        for chunk in chunks:
            assert chunk.file == "path/to/doc.md"

    # ------------------------------------------------------------------
    # Regression tests for MUST-FIX 1: oversized-paragraph fallbacks
    # ------------------------------------------------------------------

    def test_single_large_paragraph_stays_under_cap(self) -> None:
        """A single paragraph far over the cap must be split so every chunk fits.

        Regression: before the line-level fallback, this paragraph would be
        emitted as one oversized chunk (worst case: 1080 tokens on real docs).
        """
        # 300 space-separated words in one paragraph (no blank lines) → ~390 tokens.
        huge_para = " ".join(["word"] * 300)
        md = f"## Section\n\n{huge_para}\n"
        chunks = chunk_markdown(md, "doc.md")
        assert len(chunks) >= 1
        for chunk in chunks:
            tok = estimate_tokens(chunk.text)
            assert tok <= BODY_TOKEN_CAP, (
                f"Chunk {chunk.id!r} has {tok} tokens (cap={BODY_TOKEN_CAP}). "
                f"First 120 chars: {chunk.text[:120]!r}"
            )

    def test_bullet_list_single_newlines_under_cap(self) -> None:
        """Bullet-list block with single-\\n separators must stay under cap.

        Regression: a CHANGELOG-style block (20+ ``* [BUG #NNN]...`` items)
        separated by single newlines forms ONE paragraph in the old splitter,
        producing a single oversized chunk.  The line-level fallback must split
        it and preserve every item.
        """
        # 30 bullet items, each ~10 words → whole block ~390 tokens as one paragraph.
        items = [
            f"* [BUG #{i:04d}] Fixed the thing related to issue {i} in the tracker."
            for i in range(30)
        ]
        bullet_block = "\n".join(items)  # single newlines → one paragraph
        md = f"## Changelog\n\n{bullet_block}\n"
        chunks = chunk_markdown(md, "CHANGELOG.md")
        assert len(chunks) >= 1

        # Every chunk must be under cap.
        for chunk in chunks:
            tok = estimate_tokens(chunk.text)
            assert tok <= BODY_TOKEN_CAP, (
                f"Chunk {chunk.id!r} has {tok} tokens (cap={BODY_TOKEN_CAP})."
            )

        # Every bullet item must appear in at least one chunk (no content dropped).
        all_text = " ".join(c.text for c in chunks)
        for i in range(30):
            marker = f"BUG #{i:04d}"
            assert marker in all_text, f"Item {marker!r} was dropped from chunk output."

    def test_single_unbreakable_line_under_cap(self) -> None:
        """A single line with no spaces (e.g. giant URL) must not exceed the cap.

        Regression: the line-level fallback alone cannot split a no-whitespace
        line; the char/word last-resort must kick in.
        """
        # 800-char string with no spaces → ~200 tokens by char heuristic (borderline),
        # but we use 2000 chars to guarantee it exceeds the cap.
        long_token = "x" * 2000
        md = f"## Section\n\n{long_token}\n"
        chunks = chunk_markdown(md, "doc.md")
        assert len(chunks) >= 1
        for chunk in chunks:
            tok = estimate_tokens(chunk.text)
            assert tok <= BODY_TOKEN_CAP, (
                f"Chunk {chunk.id!r} has {tok} tokens (cap={BODY_TOKEN_CAP})."
            )


# ---------------------------------------------------------------------------
# Overlap on size-split pieces
# ---------------------------------------------------------------------------


class TestSizeSplitOverlap:
    def _make_md_with_many_short_paragraphs(self, n: int = 40) -> str:
        # Each paragraph: ~5 words → ~7 tokens each; need enough to exceed cap.
        paras = [f"Paragraph {i}: some short text here." for i in range(n)]
        return "## Section\n\n" + "\n\n".join(paras) + "\n"

    def test_overlap_exists_across_split_boundary(self) -> None:
        """The last paragraph of chunk N must appear as the first paragraph of chunk N+1."""
        if _OVERLAP_PARAGRAPHS == 0:
            pytest.skip("Overlap disabled (_OVERLAP_PARAGRAPHS=0)")

        md = self._make_md_with_many_short_paragraphs(n=40)
        chunks = chunk_markdown(md, "doc.md")
        assert len(chunks) >= 2, "Expected at least two size-split chunks for overlap test"

        # Find consecutive chunks from the same section (same breadcrumb).
        for i in range(len(chunks) - 1):
            if chunks[i].breadcrumb != chunks[i + 1].breadcrumb:
                continue
            # The last paragraph of chunk[i]'s body should appear in chunk[i+1]'s body.
            # Body = text after "breadcrumb\n\n"
            bc = chunks[i].breadcrumb
            body_i = chunks[i].text[len(bc) + 2 :]  # strip breadcrumb + "\n\n"
            body_i1 = chunks[i + 1].text[len(chunks[i + 1].breadcrumb) + 2 :]

            paras_i = [p.strip() for p in body_i.split("\n\n") if p.strip()]
            paras_i1 = [p.strip() for p in body_i1.split("\n\n") if p.strip()]

            if paras_i and paras_i1:
                last_para_of_i = paras_i[-1]
                assert last_para_of_i in paras_i1, (
                    f"Overlap missing between chunk {i} and {i + 1}. "
                    f"Last para of chunk {i}: {last_para_of_i!r}. "
                    f"First paras of chunk {i + 1}: {paras_i1[:3]}"
                )
                break  # One confirmed overlap is sufficient.


# ---------------------------------------------------------------------------
# Unique and stable IDs across the whole document
# ---------------------------------------------------------------------------


class TestIdUniqueness:
    def test_ids_unique_across_sections(self) -> None:
        md = (
            "# H1\n\nContent.\n\n"
            "## H2a\n\nMore content.\n\n"
            "## H2b\n\nEven more.\n\n"
            "### H3\n\nDeep.\n"
        )
        chunks = chunk_markdown(md, "file.md")
        ids = [c.id for c in chunks]
        assert len(ids) == len(set(ids)), f"Duplicate ids: {ids}"

    def test_id_format(self) -> None:
        md = "# Section\n\nContent.\n"
        chunks = chunk_markdown(md, "docs/guide.md")
        assert chunks[0].id == "docs/guide.md::0"

    def test_ids_contain_rel_path(self) -> None:
        md = "# Section\n\nContent.\n"
        rel = "nested/path/doc.md"
        chunks = chunk_markdown(md, rel)
        for chunk in chunks:
            assert chunk.id.startswith(rel + "::")

    def test_ordinals_start_at_zero(self) -> None:
        md = "# A\n\nA body.\n\n# B\n\nB body.\n"
        chunks = chunk_markdown(md, "doc.md")
        ordinals = [int(c.id.split("::")[-1]) for c in chunks]
        assert ordinals[0] == 0


# ---------------------------------------------------------------------------
# Preamble followed by headers
# ---------------------------------------------------------------------------


class TestPreambleAndHeaders:
    def test_preamble_then_header_gives_two_chunks(self) -> None:
        md = "Intro text before any header.\n\n# First Header\n\nHeader content.\n"
        chunks = chunk_markdown(md, "doc.md")
        assert len(chunks) == 2

    def test_preamble_breadcrumb_no_header_title(self) -> None:
        md = "Intro.\n\n# Header\n\nContent.\n"
        chunks = chunk_markdown(md, "my.md")
        preamble = chunks[0]
        assert preamble.breadcrumb == "my.md"
        assert ">" not in preamble.breadcrumb

    def test_header_chunk_follows_preamble(self) -> None:
        md = "Intro.\n\n# Header\n\nContent.\n"
        chunks = chunk_markdown(md, "my.md")
        header_chunk = chunks[1]
        assert header_chunk.breadcrumb == "my.md > Header"

    def test_preamble_ordinal_is_zero(self) -> None:
        md = "Intro.\n\n# Header\n\nContent.\n"
        chunks = chunk_markdown(md, "my.md")
        assert chunks[0].id == "my.md::0"
        assert chunks[1].id == "my.md::1"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_header_only_no_body(self) -> None:
        """A header with no body still produces a chunk."""
        md = "# Empty Section\n\n# Next Section\n\nContent.\n"
        chunks = chunk_markdown(md, "doc.md")
        # "Empty Section" has no body — it may be omitted or emitted; test that
        # "Next Section" is present and correct.
        next_chunks = [c for c in chunks if "Next Section" in c.breadcrumb]
        assert len(next_chunks) == 1
        assert "Content" in next_chunks[0].text

    def test_rel_path_preserved_verbatim(self) -> None:
        md = "# H\n\nBody.\n"
        rel = "some/nested/path.md"
        chunks = chunk_markdown(md, rel)
        for chunk in chunks:
            assert chunk.file == rel

    def test_basename_used_in_breadcrumb_not_full_path(self) -> None:
        md = "# H\n\nBody.\n"
        chunks = chunk_markdown(md, "a/b/c/guide.md")
        assert chunks[0].breadcrumb.startswith("guide.md")
        assert "a/b/c" not in chunks[0].breadcrumb

    def test_deep_header_hierarchy(self) -> None:
        md = (
            "# L1\n\ncontent.\n\n"
            "## L2\n\ncontent.\n\n"
            "### L3\n\ncontent.\n\n"
            "#### L4\n\ncontent.\n\n"
            "##### L5\n\ncontent.\n\n"
            "###### L6\n\ncontent.\n"
        )
        chunks = chunk_markdown(md, "deep.md")
        l6 = [c for c in chunks if "L6" in c.breadcrumb]
        assert len(l6) == 1
        assert l6[0].breadcrumb == "deep.md > L1 > L2 > L3 > L4 > L5 > L6"

    def test_multiple_sections_then_size_split_ordinals_contiguous(self) -> None:
        """Ordinals must be contiguous across header sections and size splits."""
        long_para = " ".join(["word"] * 40)
        long_body = "\n\n".join([long_para] * 25)
        md = f"# Short\n\nBrief.\n\n# Long\n\n{long_body}\n\n# After\n\nBrief again.\n"
        chunks = chunk_markdown(md, "doc.md")
        ordinals = sorted(int(c.id.split("::")[-1]) for c in chunks)
        assert ordinals == list(range(len(ordinals)))

    def test_text_field_equals_breadcrumb_plus_body(self) -> None:
        md = "## Config\n\nSome configuration options here.\n"
        chunks = chunk_markdown(md, "guide.md")
        assert len(chunks) == 1
        chunk = chunks[0]
        expected_text = f"{chunk.breadcrumb}\n\n" + "Some configuration options here."
        assert chunk.text == expected_text

    def test_fenced_block_with_multiple_hash_levels(self) -> None:
        """Various ``#`` levels inside a fence must all be ignored."""
        md = (
            "## Script\n\n"
            "```sh\n"
            "# Top-level comment\n"
            "## Double hash\n"
            "### Triple hash\n"
            "echo done\n"
            "```\n\n"
            "Prose after.\n"
        )
        chunks = chunk_markdown(md, "doc.md")
        assert len(chunks) == 1
        assert "## Double hash" in chunks[0].text
        assert "### Triple hash" in chunks[0].text


# ---------------------------------------------------------------------------
# Internal helper tests
# ---------------------------------------------------------------------------


class TestSplitIntoHeaderSections:
    """Tests for the internal ``_split_into_header_sections`` helper."""

    def test_empty_string_returns_one_empty_section(self) -> None:
        sections = _split_into_header_sections("")
        # Should return the preamble section with level=0, empty body.
        assert len(sections) == 1
        level, title, body = sections[0]
        assert level == 0
        assert title == ""

    def test_preamble_has_level_zero(self) -> None:
        sections = _split_into_header_sections("Preamble text.\n")
        assert sections[0][0] == 0

    def test_header_level_parsed_correctly(self) -> None:
        md = "# H1\n\n## H2\n\n### H3\n"
        sections = _split_into_header_sections(md)
        # sections[0] = preamble (empty), sections[1..3] = H1, H2, H3
        levels = [s[0] for s in sections if s[0] > 0]
        assert levels == [1, 2, 3]

    def test_fence_hash_not_parsed_as_header(self) -> None:
        md = "```\n# fake header\n```\n"
        sections = _split_into_header_sections(md)
        # Only the preamble section; the # inside fence is NOT a header.
        assert all(s[0] == 0 for s in sections)


# ---------------------------------------------------------------------------
# Fence length tracking (MINOR-FIX 3)
# ---------------------------------------------------------------------------


class TestFenceLengthTracking:
    """Regression tests for fence-length-aware closing logic.

    A 4-backtick opening fence must NOT be closed by a 3-backtick line;
    the closing fence must use the same character AND be at least as long
    as the opening (CommonMark §4.5).
    """

    def test_four_backtick_fence_not_closed_by_three(self) -> None:
        """A 3-backtick line inside a 4-backtick fence must NOT close it.

        Before the fix, ``fence_char`` stored only the first char, so a
        3-backtick close would match a 4-backtick open, letting subsequent
        ``#`` lines leak out as headers.
        """
        md = (
            "## Outer\n\n"
            "````python\n"  # 4-backtick open
            "```\n"  # 3-backtick line — must NOT close the fence
            "# not-a-header\n"  # inside the fence, must stay as content
            "````\n"  # 4-backtick close — actually closes the fence
            "\nContent after fence.\n"
        )
        chunks = chunk_markdown(md, "doc.md")
        # The ``# not-a-header`` line must NOT produce a new section/breadcrumb.
        header_chunks = [c for c in chunks if "not-a-header" in c.breadcrumb]
        assert len(header_chunks) == 0, (
            "``# not-a-header`` inside a 4-backtick fence was incorrectly "
            "parsed as a header.  Chunks: " + str([c.breadcrumb for c in chunks])
        )

    def test_four_backtick_fence_content_preserved(self) -> None:
        """Content inside a 4-backtick fence (including a 3-backtick line) is preserved."""
        md = "## Block\n\n````md\n```\n# not-a-header\n````\nAfter.\n"
        chunks = chunk_markdown(md, "doc.md")
        assert len(chunks) == 1, (
            f"Expected 1 chunk, got {len(chunks)}: {[c.breadcrumb for c in chunks]}"
        )
        assert "# not-a-header" in chunks[0].text

    def test_three_backtick_fence_closed_by_three(self) -> None:
        """A normal 3-backtick fence is still closed by exactly 3 backticks."""
        md = "## Normal\n\n```python\nx = 1\n```\n\n# Real Header\n\nContent.\n"
        chunks = chunk_markdown(md, "doc.md")
        real_header_chunks = [c for c in chunks if "Real Header" in c.breadcrumb]
        assert len(real_header_chunks) == 1


# ---------------------------------------------------------------------------
# Break 2 regression: oversized mid-line token must be char-split
# ---------------------------------------------------------------------------


class TestHardSplitMidLineOvercapToken:
    """Regression for Break 2: _hard_split_text must char-split a single
    over-cap token that appears mid-line (not only at the final flush).

    Before the fix, the mid-loop flush emitted a giant space-free token whole
    without going through the char-level last resort, producing a chunk with
    estimate_tokens > BODY_TOKEN_CAP.
    """

    def test_overcap_token_then_trailing_word_all_chunks_under_cap(self) -> None:
        """An over-cap space-free token followed by a trailing word must be split.

        Repro: ``chunk_markdown("# H\\n\\n" + "A"*2000 + " tail\\n", "x.md")``
        produced a 503-est-token chunk because 'A'*2000 was flushed whole as a
        mid-loop flush (not the final flush) and the char-split path was skipped.
        """
        bigword = "A" * 2000
        md = f"# H\n\n{bigword} tail\n"
        chunks = chunk_markdown(md, "x.md")
        assert len(chunks) >= 1, "Expected at least one chunk"
        for chunk in chunks:
            tok = estimate_tokens(chunk.text)
            assert tok <= BODY_TOKEN_CAP, (
                f"Chunk {chunk.id!r} has {tok} tokens (cap={BODY_TOKEN_CAP}). "
                f"First 80 chars: {chunk.text[:80]!r}"
            )

    def test_leading_word_then_overcap_token_then_trailing(self) -> None:
        """'x ' + giant_token + ' y' on one line — all chunks under cap."""
        bigword = "B" * 3000
        md = f"# H\n\nx {bigword} y\n"
        chunks = chunk_markdown(md, "x.md")
        for chunk in chunks:
            tok = estimate_tokens(chunk.text)
            assert tok <= BODY_TOKEN_CAP, (
                f"Chunk {chunk.id!r} has {tok} est-tokens (cap={BODY_TOKEN_CAP})."
            )

    def test_table_giant_cell_all_chunks_under_cap(self) -> None:
        """A markdown table row with a huge cell must not produce an over-cap chunk.

        The ``|`` markers after the giant cell cause a mid-loop flush in
        ``_hard_split_text``; the giant cell must be char-split, not emitted whole.
        """
        table = "# T\n\n| col |\n|---|\n| " + "x" * 3000 + " |\n"
        chunks = chunk_markdown(table, "table.md")
        for chunk in chunks:
            tok = estimate_tokens(chunk.text)
            assert tok <= BODY_TOKEN_CAP, (
                f"Table chunk {chunk.id!r} has {tok} est-tokens (cap={BODY_TOKEN_CAP})."
            )


# ---------------------------------------------------------------------------
# Break 3 regressions: setext headers (=== / ---)
# ---------------------------------------------------------------------------


class TestSetextHeaders:
    """Regressions for Break 3: setext (underline-style) headers.

    ripgrep's CHANGELOG.md uses setext headers throughout.  Before the fix,
    all 156 chunks collapsed to a single 'CHANGELOG.md' breadcrumb.
    """

    # ------------------------------------------------------------------
    # Basic recognition
    # ------------------------------------------------------------------

    def test_setext_h1_produces_correct_breadcrumb(self) -> None:
        """A ``===`` underline creates an h1 breadcrumb with the title."""
        md = "Title\n=====\n\nbody text\n"
        chunks = chunk_markdown(md, "d.md")
        assert any("Title" in c.breadcrumb for c in chunks), (
            f"Expected breadcrumb containing 'Title', got: {[c.breadcrumb for c in chunks]}"
        )

    def test_setext_h2_produces_correct_breadcrumb(self) -> None:
        """A ``---`` underline creates an h2 breadcrumb with the title."""
        md = "Parent\n======\n\nSubtitle\n--------\n\nbody\n"
        chunks = chunk_markdown(md, "d.md")
        breadcrumbs = [c.breadcrumb for c in chunks]
        assert any("Subtitle" in bc for bc in breadcrumbs), (
            f"Expected breadcrumb with 'Subtitle', got: {breadcrumbs}"
        )

    def test_setext_underline_not_in_chunk_body(self) -> None:
        """The ``=====`` or ``------`` underline line must NOT appear in chunk text."""
        md = "Title\n=====\n\nbody\n"
        chunks = chunk_markdown(md, "d.md")
        all_text = " ".join(c.text for c in chunks)
        assert "=====" not in all_text, (
            "Setext underline '=====' was included in chunk body (should be consumed)."
        )

    def test_setext_h2_underline_not_in_chunk_body(self) -> None:
        """The ``-----`` underline line must NOT appear in chunk text."""
        md = "Subtitle\n---------\n\nbody\n"
        chunks = chunk_markdown(md, "d.md")
        all_text = " ".join(c.text for c in chunks)
        assert "-----" not in all_text, (
            "Setext underline '-----' was included in chunk body (should be consumed)."
        )

    def test_setext_h1_then_h2_breadcrumb_hierarchy(self) -> None:
        """Setext h1 followed by setext h2 builds a nested breadcrumb."""
        md = "BigTitle\n========\n\nSubtitle\n--------\n\ncontent\n"
        chunks = chunk_markdown(md, "d.md")
        # The content chunk should be under both BigTitle and Subtitle.
        content_bc = [c.breadcrumb for c in chunks if "content" in c.text]
        assert content_bc, "No chunk with 'content' found"
        assert "BigTitle" in content_bc[0], f"Missing BigTitle in {content_bc}"
        assert "Subtitle" in content_bc[0], f"Missing Subtitle in {content_bc}"

    # ------------------------------------------------------------------
    # Non-misfires: things that must NOT become setext headers
    # ------------------------------------------------------------------

    def test_thematic_break_after_blank_line_is_not_setext(self) -> None:
        """A ``---`` line after a blank line is a thematic break, not a setext header.

        A blank line between a paragraph and ``---`` means there is no immediately
        preceding text line, so ``---`` cannot be a setext underline.
        """
        md = "Paragraph one.\n\n---\n\nParagraph two.\n"
        chunks = chunk_markdown(md, "d.md")
        # All breadcrumbs should be just the filename (no header created).
        for chunk in chunks:
            assert chunk.breadcrumb == "d.md", (
                f"Thematic break created a spurious header: {chunk.breadcrumb!r}"
            )

    def test_table_separator_not_setext(self) -> None:
        """A ``|---|---|`` table separator row must NOT become a setext header."""
        md = "# T\n\n| a | b |\n|---|---|\n| x | y |\n"
        chunks = chunk_markdown(md, "d.md")
        assert len(chunks) == 1, f"Expected 1 chunk, got {len(chunks)}"
        # The table separator must appear in the body, not be consumed as a header.
        assert "|---|---|" in chunks[0].text, "Table separator was incorrectly consumed"

    def test_setext_underline_inside_fenced_block_not_header(self) -> None:
        """A ``===`` line inside a fenced code block must NOT become a setext header."""
        md = (
            "# Outer\n\n"
            "```\n"
            "Title inside fence\n"
            "==================\n"  # setext-like, but inside fence
            "more content\n"
            "```\n\n"
            "After.\n"
        )
        chunks = chunk_markdown(md, "d.md")
        # Only one section (the ATX h1); the fence content is body, not a new header.
        assert len(chunks) == 1, (
            f"Expected 1 chunk, got {len(chunks)}: {[c.breadcrumb for c in chunks]}"
        )
        assert "==================" in chunks[0].text, (
            "Setext-like line inside fence was incorrectly consumed"
        )

    def test_yaml_frontmatter_close_dash_not_setext(self) -> None:
        """The closing ``---`` of a YAML frontmatter block is not a setext h2.

        YAML frontmatter: first line is ``---``, content follows, then another ``---``
        closes it.  The closing ``---`` looks like a setext h2 for the last frontmatter
        line, but must be treated as the end of the frontmatter block instead.
        """
        md = "---\ntitle: My Doc\nauthor: Foo\n---\n\n# Real Header\n\nContent.\n"
        chunks = chunk_markdown(md, "d.md")
        # Only 'Real Header' should be a header; 'author: Foo' must NOT be one.
        breadcrumbs = {c.breadcrumb for c in chunks}
        assert not any("author" in bc for bc in breadcrumbs), (
            f"YAML frontmatter close was misidentified as setext header: {breadcrumbs}"
        )
        assert any("Real Header" in bc for bc in breadcrumbs), (
            f"Real ATX header missing after frontmatter: {breadcrumbs}"
        )


# ---------------------------------------------------------------------------
# Integration tier: real-tokenizer gate (excluded from CI)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRealTokenizerGate:
    """Integration tests that load the real all-MiniLM-L6-v2 tokenizer and verify
    that no chunk from the ripgrep corpus exceeds the 256-token MiniLM window.

    These tests are excluded from CI (``pytest -m "not integration"``).
    Run locally with ``pytest -m integration tests/test_doc_chunking.py``.

    Skip gracefully if the ``tokenizers`` package or the model cache is absent.
    """

    _TOKENIZER_PATH = "/home/vscode/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx/tokenizer.json"
    _RIPGREP_ROOT = "/workspaces/ripgrep"
    _WINDOW = 256  # MiniLM token budget (including [CLS] and [SEP] → effective 254 content)

    @staticmethod
    def _load_tok():  # type: ignore[return]
        """Load the real tokenizer; skip the test if unavailable."""
        import os

        tokenizers = pytest.importorskip("tokenizers", reason="tokenizers package not installed")
        tok_path = TestRealTokenizerGate._TOKENIZER_PATH
        if not os.path.exists(tok_path):
            pytest.skip(f"Tokenizer cache not found: {tok_path}")
        tok = tokenizers.Tokenizer.from_file(tok_path)
        tok.no_truncation()
        return tok

    @staticmethod
    def _real_tokens(tok, text: str) -> int:
        """Count non-padding content tokens (excluding [CLS] and [SEP])."""
        tokens = tok.encode(text).tokens
        return sum(1 for t in tokens if t not in ("[PAD]", "[CLS]", "[SEP]"))

    def test_ripgrep_corpus_no_chunk_exceeds_256_real_tokens(self) -> None:
        """Every chunk from the ripgrep corpus must be ≤ 256 real MiniLM tokens.

        This is the TRUE truncation gate that estimate_tokens alone cannot catch.
        The estimate is conservative but not perfect; this test verifies the real
        invariant holds for the actual target corpus.
        """
        import os
        from pathlib import Path

        if not os.path.isdir(self._RIPGREP_ROOT):
            pytest.skip(f"ripgrep source not found at {self._RIPGREP_ROOT}")

        tok = self._load_tok()

        over_window: list[tuple[str, int]] = []
        total = 0
        worst = 0

        for md_path in Path(self._RIPGREP_ROOT).rglob("*.md"):
            text = md_path.read_text(encoding="utf-8", errors="replace")
            rel = str(md_path.relative_to(self._RIPGREP_ROOT))
            for chunk in chunk_markdown(text, rel):
                total += 1
                real = self._real_tokens(tok, chunk.text)
                worst = max(worst, real)
                if real > self._WINDOW:
                    over_window.append((chunk.id, real))

        assert over_window == [], (
            f"{len(over_window)} chunk(s) exceed {self._WINDOW} REAL MiniLM tokens "
            f"(total={total}, worst={worst}). First offenders: {over_window[:5]}"
        )

    def test_cjk_fixture_under_256_real_tokens(self) -> None:
        """CJK fixture (\"字\" * 700 under a header) must produce chunks ≤ 256 real tokens.

        CJK characters each expand to one WordPiece token.  The old estimator
        (len/4) severely under-counted them; this test verifies the new estimator
        correctly caps CJK-heavy chunks within the MiniLM window.
        """
        tok = self._load_tok()
        body = "字" * 700
        md = f"# 配置\n\n{body}\n"
        chunks = chunk_markdown(md, "cjk_doc.md")
        assert chunks, "Expected at least one chunk from CJK fixture"
        for chunk in chunks:
            real = self._real_tokens(tok, chunk.text)
            assert real <= self._WINDOW, (
                f"CJK chunk {chunk.id!r} has {real} real tokens "
                f"(window={self._WINDOW}). text[:60]={chunk.text[:60]!r}"
            )
