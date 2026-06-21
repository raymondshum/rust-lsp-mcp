//! Tiny fixture for KI-5 (UTF-16 vs codepoint position offsets).
//!
//! The line below places an astral-plane character (U+1F600, one Unicode
//! codepoint but two UTF-16 code units) before an identifier, so the
//! identifier's column differs between UTF-16 (rust-analyzer's default
//! encoding) and UTF-32/codepoints. All-ASCII fixtures (like ripgrep) cannot
//! expose this.

/* 😀 */ pub fn target_after_emoji() {}

pub fn plain_ascii() {}

/// Caller that references `target_after_emoji` on a non-ASCII line.
/// The call site has an astral char before the identifier, so codepoint and
/// UTF-16 column numbers differ by 1.  Used by the input-side attack tests.
pub fn caller_with_astral_prefix() {
    /* 😀 */ let _ = target_after_emoji();
}
