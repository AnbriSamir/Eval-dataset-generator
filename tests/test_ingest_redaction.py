"""Adversarial redaction battery (ADR-0001 rule 3).

Every payload here models an attack that defeated a naive scrubber somewhere: secrets
nested deep in structures, secrets in dict KEYS, invisible-character-split tokens
(zero-width, word joiner, soft hyphen — the red-team's U+2060 payload defeated the
original 4-char zero-width table), fullwidth homoglyphs, stringified JSON-in-JSON.
A new redaction rule ships with the payload that defeats the old one — this file is
where those payloads accumulate.
"""

from __future__ import annotations

import unicodedata

import pytest

from evalgen.ingest import normalize_text, sanitize_text, scrub_value

# ------------------------------------------------------------------ category coverage


@pytest.mark.parametrize(
    ("text", "placeholder", "secret_fragment"),
    [
        (
            "header eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c end",
            "[REDACTED:jwt]",
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c",
        ),
        ("key sk-proj0123456789abcdef used", "[REDACTED:api_key]", "sk-proj0123456789abcdef"),
        (
            "key sk-ant-api03-Zx9yW8vU7tS6rQ5pO4nM3lK2 used",
            "[REDACTED:api_key]",
            "Zx9yW8vU7tS6rQ5pO4nM3lK2",
        ),
        ("aws AKIAIOSFODNN7EXAMPLE used", "[REDACTED:aws_key]", "AKIAIOSFODNN7EXAMPLE"),
        (
            "Authorization: Bearer AbCdEf123456789012 sent",
            "[REDACTED:bearer]",
            "AbCdEf123456789012",
        ),
        ("password: hunter2secret9 leaked", "[REDACTED:secret_kv]", "hunter2secret9"),
        (
            "blob 3f786850e387550fdab836ed7e6dc881de23001b3f786850 end",
            "[REDACTED:token]",
            "3f786850e387550fdab836ed7e6dc881de23001b3f786850",
        ),
        ("mail jean.dupont@acme-corp.fr sent", "[REDACTED:email]", "jean.dupont"),
        ("call +33 6 12 34 56 78 now", "[REDACTED:phone]", "12 34 56 78"),
        ("appelle 06 12 34 56 78 stp", "[REDACTED:phone]", "06 12 34 56 78"),
        (
            r"log at C:\Users\SamirANBRI\Desktop\log.txt here",
            "[REDACTED:user_path]",
            "SamirANBRI",
        ),
        ("log at /home/samir/logs here", "[REDACTED:user_path]", "samir"),
        ("log at /Users/samir/logs here", "[REDACTED:user_path]", "samir"),
    ],
)
def test_each_category_is_caught_and_secret_is_gone(
    text: str, placeholder: str, secret_fragment: str
) -> None:
    scrubbed = sanitize_text(text)
    assert placeholder in scrubbed
    assert secret_fragment not in scrubbed


def test_placeholder_is_constant_never_derived_from_the_secret() -> None:
    # A hash/salt of the secret would leak bits and break the
    # same-id-regardless-of-secret invariant: two different secrets must scrub to the
    # exact same output.
    a = sanitize_text("key sk-ant-aaaaaaaaaaaaaaaaaaaa used")
    b = sanitize_text("key sk-ant-bbbbbbbbbbbbbbbbbbbb used")
    assert a == b == "key [REDACTED:api_key] used"


# --------------------------------------------------------------------- unicode attacks


def test_zero_width_split_key_is_caught() -> None:
    # "sk-ant-…" split by zero-width spaces walks past any raw-text regex; the
    # normalize-first order kills it.
    attack = "use s\u200bk\u200b-ant-XyZ0123456789012345 now"
    scrubbed = sanitize_text(attack)
    assert "[REDACTED:api_key]" in scrubbed
    assert "XyZ0123456789012345" not in scrubbed


def test_fullwidth_homoglyph_key_is_caught() -> None:
    # NFKC folds fullwidth latin (ｓｋ－…) to ASCII before the patterns run.
    attack = "use ｓｋ－ａｎｔ－ＸｙＺ"
    attack += "０１２３４５６７８９"
    attack += "０１２３４５ now"
    scrubbed = sanitize_text(attack)
    assert "[REDACTED:api_key]" in scrubbed


def test_word_joiner_split_key_is_caught() -> None:
    # RED-TEAM BLOCKER payload, replayed verbatim. U+2060 WORD JOINER is untouched by
    # NFKC and was outside the original 4-char zero-width table — the FULL key walked
    # straight past every pattern. The category-based strip (all of Cf) kills it.
    attack = "api_key was sk-ant-abc1234567\u2060Zx9yW8vU7tS6rQ5pO4nM3lK2wXyZ today"
    scrubbed = sanitize_text(attack)
    assert "[REDACTED:api_key]" in scrubbed
    assert "sk-ant-abc1234567" not in scrubbed
    assert "Zx9yW8vU7tS6rQ5pO4nM3lK2wXyZ" not in scrubbed


def test_word_joiner_split_opaque_token_is_caught() -> None:
    # RED-TEAM BLOCKER, second payload: a 40-char opaque token split 20/20 — each
    # half is <32 chars with no recognizable prefix, so BOTH halves survived the old
    # table. Rejoined by the strip, the whole blob matches the opaque-token rule.
    attack = "token A1b2C3d4E5f6G7h8I9j0\u2060K1l2M3n4O5p6Q7r8S9t0 end"
    scrubbed = sanitize_text(attack)
    assert "[REDACTED:token]" in scrubbed
    assert "A1b2C3d4E5f6G7h8I9j0" not in scrubbed
    assert "K1l2M3n4O5p6Q7r8S9t0" not in scrubbed


def test_mid_body_split_leaves_no_recognizable_tail() -> None:
    # RED-TEAM partial variant: a split landing mid-body used to redact the head but
    # leak a recognizable key TAIL ("[REDACTED:api_key]\u2060rQ5pO4nM3lK2").
    attack = "sk-ant-abcdefZx9yW8vU7tS6\u2060rQ5pO4nM3lK2"
    assert sanitize_text(attack) == "[REDACTED:api_key]"


def test_soft_hyphen_split_key_is_caught() -> None:
    # U+00AD SOFT HYPHEN: renders as nothing (unless a line actually breaks there),
    # survives NFKC, was not in the old table. It is Cf — the category strip eats it.
    attack = "use sk-ant-XyZ01234\u00ad56789012345 now"
    scrubbed = sanitize_text(attack)
    assert "[REDACTED:api_key]" in scrubbed
    assert "XyZ0123456789012345" not in scrubbed


@pytest.mark.parametrize(
    "code_point",
    [
        0x00AD,  # soft hyphen (Cf)
        0x200E,  # left-to-right mark (Cf)
        0x200F,  # right-to-left mark (Cf)
        0x061C,  # arabic letter mark (Cf)
        0x2060,  # word joiner (Cf)
        0x2061,  # invisible function application (Cf)
        0xE0041,  # tag latin capital A (Cf — the ASCII-smuggling block)
        0x034F,  # combining grapheme joiner (Mn — the non-Cf straggler)
        0xFE0F,  # variation selector 16 (Mn)
        0x1160,  # hangul jungseong filler (Lo, renders as blank)
    ],
)
def test_invisible_family_members_cannot_split_a_key(code_point: int) -> None:
    # The fix is the CATEGORY, not a bigger table: any member of the invisible family
    # must fail to split a token. A regression back to code-point enumeration would
    # fail here on whichever member the enumeration forgot.
    attack = f"use sk-{chr(code_point)}ant-XyZ0123456789012345 now"
    scrubbed = sanitize_text(attack)
    assert "[REDACTED:api_key]" in scrubbed
    assert "XyZ0123456789012345" not in scrubbed


def test_cgj_blocked_composition_still_hashes_equal() -> None:
    # U+034F CGJ has combining class 0 and BLOCKS canonical composition: with a
    # strip-AFTER-NFKC-only order, "e + CGJ + combining acute" would stay decomposed
    # and hash-diverge from "é" — a silent dedup miss. The pre-NFKC strip pass is
    # load-bearing; this pins it.
    assert normalize_text("cafe\u034f\u0301") == "café"


def test_nfkc_emitted_invisible_is_stripped_too() -> None:
    # NFKC can EMIT an invisible character: U+3164 HANGUL FILLER (and U+FFA0) fold to
    # U+1160, which also renders as nothing. The post-NFKC strip pass is load-bearing;
    # this pins that neither the compat forms nor their fold product survive.
    assert normalize_text("a\u3164b") == "ab"
    assert normalize_text("a\uffa0b") == "ab"
    assert normalize_text("a\u1160b") == "ab"


def test_nfc_and_nfd_equivalents_sanitize_identically() -> None:
    # Not only a security property: unicode-equivalent texts must hash equal
    # downstream, or dedup silently misses duplicates.
    text = "café credentials rotated"
    assert sanitize_text(unicodedata.normalize("NFC", text)) == sanitize_text(
        unicodedata.normalize("NFD", text)
    )


def test_crlf_normalizes_to_lf() -> None:
    assert normalize_text("line1\r\nline2\rline3") == "line1\nline2\nline3"


# ------------------------------------------------------------------ structured attacks


def test_secret_three_dicts_deep_is_scrubbed() -> None:
    value = {"a": {"b": {"c": "key sk-ant-abcdefghij0123456789 here"}}}
    scrubbed = scrub_value(value)
    assert scrubbed == {"a": {"b": {"c": "key [REDACTED:api_key] here"}}}


def test_secret_in_dict_key_is_scrubbed() -> None:
    # trace_store precedent: a secret-bearing KEY leaks just as hard as a value.
    value = {"sk-ant-abcdefghij0123456789": "rotated"}
    assert scrub_value(value) == {"[REDACTED:api_key]": "rotated"}


def test_secret_inside_list_and_tuple_is_scrubbed() -> None:
    value = {"items": ["ok", ("mail jean.dupont@acme-corp.fr",)]}
    scrubbed = scrub_value(value)
    assert scrubbed == {"items": ["ok", ("mail [REDACTED:email]",)]}


def test_stringified_json_in_json_is_scrubbed() -> None:
    # A JSON document hiding inside a string field: the patterns run on raw text, so
    # quoted keys are caught too (the kv pattern tolerates quotes).
    text = 'config was {"api_key": "supersecretvalue123"} at startup'
    scrubbed = sanitize_text(text)
    assert "supersecretvalue123" not in scrubbed
    assert "[REDACTED:secret_kv]" in scrubbed


def test_non_string_scalars_pass_through_unchanged() -> None:
    value = {"count": 3, "ratio": 0.5, "ok": True, "none": None}
    assert scrub_value(value) == value


# ----------------------------------------------------------- stated / chosen behavior


def test_idempotent_on_already_scrubbed_text() -> None:
    # build_record defensively re-sanitizes — that must never corrupt text, so
    # placeholders must match none of the patterns.
    samples = [
        "key [REDACTED:api_key] used",
        "Bearer [REDACTED:bearer]",
        "api_key=[REDACTED:api_key]",
        "[REDACTED:secret_kv] [REDACTED:token] [REDACTED:email]",
    ]
    for text in samples:
        assert sanitize_text(text) == text


def test_git_sha_over_redaction_is_the_chosen_failure_direction() -> None:
    # 40-hex git SHAs match the opaque-token rule. This is DELIBERATE (ADR-0001:
    # over-redaction is the accepted failure direction) — pinned so the behavior is a
    # documented decision, not a surprise discovered in an export.
    scrubbed = sanitize_text("deployed 3f786850e387550fdab836ed7e6dc881de23001b3f")
    assert "[REDACTED:token]" in scrubbed


def test_bare_french_mobile_without_separators_is_not_eaten() -> None:
    # The phone patterns are deliberately conservative: a 10-digit run could be any
    # numeric id, so only separator-formatted French numbers are matched.
    assert sanitize_text("order ref 0612345678") == "order ref 0612345678"


def test_short_numbers_and_ids_survive() -> None:
    text = "span-1a2b3c4d5e6f attempt 2 tokens 812 cost 0.0231"
    assert sanitize_text(text) == text


# --------------------------------------------------- C0-control injection (red-team MINOR)


class TestControlCharacterInjection:
    """Red-team MINOR replayed verbatim: an injected 0x1f (the CANONICAL_SEP used to
    join id/dedup parts) made two DISTINCT exchanges collide on both record_id and
    canonical_text. normalize_text now strips the whole C0 family (except \n/\t) so
    the "cannot appear in normal text" assumption is enforced, not asserted."""

    def test_unit_separator_is_stripped(self) -> None:
        assert sanitize_text("a\x1fb") == "ab"

    def test_c0_family_is_stripped_but_newline_and_tab_survive(self) -> None:
        # Every C0 control except \n and \t must go; DEL (0x7f) too. \r is not
        # dropped but FOLDED to \n (newline normalization runs first).
        assert sanitize_text("a\x00b\x01c\x1fd\x7fe") == "abcde"
        assert sanitize_text("line1\nline2\tcol") == "line1\nline2\tcol"
        assert sanitize_text("line1\r\nline2\rline3") == "line1\nline2\nline3"

    def test_injected_separator_no_longer_collides_distinct_exchanges(self) -> None:
        # The exact red-team payload pair: ("a", "b\x1fc") vs ("a\x1fb", "c")
        # collided pre-fix (same id, same dedup key). Post-fix they sanitize to
        # ("a", "bc") vs ("ab", "c") — distinct ids AND distinct canonical texts.
        from evalgen.contracts import SourceKind
        from evalgen.ingest.normalize import build_record

        kwargs = {"source_kind": SourceKind.GENERIC_JSONL, "source_name": "atk", "line_no": 1}
        rec_1 = build_record(input_text="a", output_text="b\x1fc", **kwargs)
        rec_2 = build_record(input_text="a\x1fb", output_text="c", **kwargs)
        assert rec_1.record_id != rec_2.record_id
        assert rec_1.canonical_text != rec_2.canonical_text
